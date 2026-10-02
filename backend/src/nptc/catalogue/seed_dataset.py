"""Reads and validates `import-dataset.json` (ADR-0010) for the seed loader (FR-70, FR-76).

The models here mirror the transform's frozen dataclasses (`nptc_transform.dataset`) and are
duplicated on purpose: the file is the contract, it is versioned, and the backend does not depend
on `nptc_transform` (ADR-0001 keeps only `nptc_shared` common to both). `SUPPORTED_SCHEMA_VERSION`
pins the reader to one version; a file carrying any other is refused before it is parsed further.

**Strict parsing.** Every model forbids unknown keys and refuses type coercion, so a code written
as a JSON number is an error, never a silently converted string (FR-06).

**Two layers of refusal, both before any database write.** `DatasetInvalidError` reports a file
that is not the ADR-0010 shape. `DatasetNotSeedableError` reports a well-formed file that the
backend cannot store faithfully, and lists every such problem at once so one round of fixes
clears them all. Neither echoes a field value from the file, apart from the labels and keys an
editor needs to find the row (NFR-26).

What the backend cannot store, and the loader therefore refuses rather than repairs:

- A specimen value with no SNOMED CT code. A `property_value` holds `{system, code}`, and the
  specimen binding requires a code in the specimen value set, so a verbatim label has nowhere to
  go. RCPA-QAP resolves it in the workbook, then the transform is run again.
- A named specimen on an entry flagged `specimen_unconstrained` (FR-89 refuses the pair).
- A code binding with no FSN, which `create_binding` requires and which means the transform
  found no FSN cell.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.db.models.catalogue_entry import CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBindingEditionHint
from nptc_shared.language import DEFAULT_LANGUAGE

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "DatasetEntry",
    "DatasetInvalidError",
    "DatasetNotSeedableError",
    "DatasetReadError",
    "DatasetUnreadableError",
    "ImportDataset",
    "UnsupportedSchemaVersionError",
    "highest_business_key",
    "read_import_dataset",
]

SUPPORTED_SCHEMA_VERSION: Final[int] = 1

_SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"


class DatasetReadError(Exception):
    """Base class for every refusal to read a dataset. Raised before any database write."""


class DatasetUnreadableError(DatasetReadError):
    """The file is missing, is not UTF-8, or is not a JSON object."""


class UnsupportedSchemaVersionError(DatasetReadError):
    def __init__(self, found: object) -> None:
        super().__init__(
            f"import dataset schema_version is {found!r}; this loader reads only "
            f"schema_version {SUPPORTED_SCHEMA_VERSION}. Re-emit the dataset with a matching "
            "transform, or upgrade the loader"
        )
        self.found = found


class DatasetInvalidError(DatasetReadError):
    """The file does not match the ADR-0010 shape. `problems` are `path: message` strings."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(
            f"import dataset is not valid ({len(problems)} problem(s)): " + "; ".join(problems)
        )
        self.problems = tuple(problems)


class DatasetNotSeedableError(DatasetReadError):
    """The file is well formed but the backend cannot store it faithfully. `problems` name the
    entry (by `business_key` and workbook sheet and row) and what to fix."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(
            f"import dataset cannot be seeded ({len(problems)} problem(s)): " + "; ".join(problems)
        )
        self.problems = tuple(problems)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DatasetSource(_Strict):
    filename: str
    sha256: str = Field(pattern=_SHA256_PATTERN)


class DatasetBaselineRelease(_Strict):
    name: str
    note: str


class DatasetDesignation(_Strict):
    term: str
    use: Literal["preferred", "synonym"]
    language: str
    status: Literal["active"]


class DatasetCodeBinding(_Strict):
    system: str
    code: str
    fsn: str | None
    au_preferred_term: str | None
    edition_hint: str
    status: Literal["active"]

    @field_validator("edition_hint")
    @classmethod
    def _known_edition_hint(cls, value: str) -> str:
        if value not in {str(member) for member in CodeBindingEditionHint}:
            raise ValueError("is not a known edition hint")
        return value


class DatasetPropertyValue(_Strict):
    value: str
    code: str | None


class DatasetProperties(_Strict):
    discipline: tuple[DatasetPropertyValue, ...]
    subgroup: tuple[DatasetPropertyValue, ...]
    specimen: tuple[DatasetPropertyValue, ...]
    usage_guidance: str | None


class DatasetEntrySource(_Strict):
    sheet: str
    row: int = Field(ge=1)
    legacy_version: str | None
    legacy_history: str | None


class DatasetEntry(_Strict):
    business_key: str
    source: DatasetEntrySource
    preferred_term: str
    status: CatalogueEntryStatus
    specimen_unconstrained: bool
    designations: tuple[DatasetDesignation, ...]
    code_bindings: tuple[DatasetCodeBinding, ...]
    properties: DatasetProperties


class ImportDataset(_Strict):
    schema_version: Literal[1]
    tool_version: str
    source: DatasetSource
    baseline_release: DatasetBaselineRelease
    entries: tuple[DatasetEntry, ...] = Field(min_length=1)


def highest_business_key(dataset: ImportDataset) -> str:
    """The entry key with the greatest numeric value, which is what the sequence must advance
    past. Compared numerically: `NPTC-1000000` sorts before `NPTC-999999` as text."""

    def numeric(entry: DatasetEntry) -> int:
        match = BUSINESS_KEY_PATTERN.match(entry.business_key)
        assert match is not None  # `read_import_dataset` has refused any other shape
        return int(match.group(1))

    return max(dataset.entries, key=numeric).business_key


def _format_validation_errors(error: ValidationError) -> list[str]:
    # `loc` and `msg` only: pydantic's `input` would echo the offending value.
    return [
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    ]


def _where(entry: DatasetEntry) -> str:
    return f"{entry.business_key} (sheet {entry.source.sheet!r}, row {entry.source.row})"


def _entry_problems(entry: DatasetEntry) -> list[str]:
    where = _where(entry)
    problems: list[str] = []

    if BUSINESS_KEY_PATTERN.match(entry.business_key) is None:
        problems.append(f"{where}: business_key does not match the NPTC-nnnnnn format")

    if len(entry.code_bindings) != 1:
        problems.append(
            f"{where}: expected exactly one code binding, found {len(entry.code_bindings)}"
        )
    for binding in entry.code_bindings:
        if binding.fsn is None or not binding.fsn.strip():
            problems.append(f"{where}: code binding {binding.code!r} has no FSN")

    preferred = [d for d in entry.designations if d.use == "preferred"]
    if len(preferred) != 1:
        problems.append(
            f"{where}: expected exactly one preferred designation, found {len(preferred)}"
        )
    else:
        only = preferred[0]
        if only.language != DEFAULT_LANGUAGE:
            problems.append(f"{where}: the preferred designation is not {DEFAULT_LANGUAGE}")
        if only.term != entry.preferred_term:
            problems.append(f"{where}: the preferred designation differs from preferred_term")

    for value in entry.properties.specimen:
        if value.code is None:
            problems.append(
                f"{where}: specimen {value.value!r} has no SNOMED CT code - code it in the "
                "workbook, then run the transform again"
            )
    if entry.specimen_unconstrained and entry.properties.specimen:
        problems.append(
            f"{where}: marked as accepting any specimen but also lists named specimens "
            "(FR-89) - keep one or the other in the workbook"
        )

    for label, values in (
        ("discipline", entry.properties.discipline),
        ("subgroup", entry.properties.subgroup),
    ):
        for value in values:
            if not value.value.strip():
                problems.append(f"{where}: a {label} value is blank")
            if value.code is not None:
                problems.append(f"{where}: {label} {value.value!r} carries a code; expected none")
    return problems


def _dataset_problems(dataset: ImportDataset) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for entry in dataset.entries:
        if entry.business_key in seen:
            problems.append(f"{_where(entry)}: business_key appears more than once")
        seen.add(entry.business_key)
        problems.extend(_entry_problems(entry))
    return problems


def read_import_dataset(path: Path) -> ImportDataset:
    """Reads, validates and returns the dataset at `path`, or raises a `DatasetReadError`.

    Touches no database. The `schema_version` gate runs on the raw document first, so a file from
    a later transform is reported as unsupported rather than as a list of unrelated shape errors.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DatasetUnreadableError(
            f"cannot read the dataset file ({type(exc).__name__})"
        ) from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetUnreadableError(
            f"the dataset file is not UTF-8 JSON ({type(exc).__name__})"
        ) from exc
    if not isinstance(document, dict):
        raise DatasetUnreadableError("the dataset file is not a JSON object")

    found_version = document.get("schema_version")
    if type(found_version) is not int or found_version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            found_version if type(found_version) is int else "<missing or not an integer>"
        )

    try:
        dataset = ImportDataset.model_validate_json(raw)
    except ValidationError as exc:
        raise DatasetInvalidError(_format_validation_errors(exc)) from exc

    problems = _dataset_problems(dataset)
    if problems:
        raise DatasetNotSeedableError(problems)
    return dataset
