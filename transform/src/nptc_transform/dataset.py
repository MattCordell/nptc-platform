"""Builds and writes the import dataset (FR-70, FR-76, issue #31, P0-9).

``build_dataset`` consumes ``RunResult.findings`` in process - never the
report file - the coupling ADR-0009 already committed to. It must only be
called once a caller has already confirmed ``result`` carries no blocking
finding (``RunResult.has_blocking_findings``); a caller that doesn't is a bug
in that caller, not something this module re-checks, the same "true by
construction" style ``Finding`` itself uses (PRD:310's "the seeded baseline
cannot be created until RCPA-QAP resolves those collisions editorially").

One file, ``import-dataset.json``, written into the same ``--report-dir`` as
``report.json``/``report.md`` - the CLI's "no file outside --report-dir is
ever touched" invariant survives untouched. Same envelope discipline as
``report_writer.py``: own ``schema_version``, no clock value, basename-only
source, every collection explicitly sorted or built in a deterministic
order, ``encoding="utf-8"``, ``newline="\\n"``, overwrite in place (FR-73).

**What is carried, and what is not.** ``Length``, ``Version`` and ``History``
are not carried as entry fields: ``Length`` MUST NOT be storable (FR-85) - it
is computed in the export layer, not here - and ``Version``/``History`` MUST
be generated from release membership (FR-59), which is precisely what
``baseline_release`` replaces. The *existing* hand-typed ``Version``/
``History`` values carry real provenance, so they are preserved verbatim
under ``source.legacy_version``/``legacy_history`` - immutable seeding
provenance, never an editable field, so no information is destroyed at
cutover.

**Specimen: verbatim always, code only where certain** (FR-88/FR-92
precedent). A specimen value's ``code`` is populated only on an *exact*
``SPECIMEN_TABLE`` surface-form match (``cell_defects.resolve_specimen_term``)
- never the word-boundary substring heuristic ``semantic_drift.py`` uses for
its own free-text review, which is calibrated for a different, lower-stakes
purpose. An unmapped value is still seeded, verbatim, with no code
(``SPECIMEN_VALUE_UNMAPPED``, informational). ``'Any'`` sets
``specimen_unconstrained: true`` and yields no specimen value for itself
(FR-89), but does not discard any other value the same cell asserts: the
published data is not guaranteed to keep 'Any' from co-occurring with a
named specimen on one row, and this module must seed exactly what the report
already describes for that cell, never less.

**Terminology-served enrichment is out of scope for this issue.** Without
``--check-terminology``, ``edition_hint`` is always ``"unknown"`` and
``fsn``/``au_preferred_term`` come from the published cell text/``None``.
Populating these from a live sweep's served designations (FR-82) needs the
per-code ``SweepResult`` threaded through further than ``RunResult`` carries
it today, and is deferred to a follow-up issue (see the PR body) rather than
widening this one's scope.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nptc_transform import __version__
from nptc_transform.cell_defects import (
    resolve_specimen_term,
    resolves_code_column,
    split_compound_value,
    split_specimen_values,
    split_synonyms,
)
from nptc_transform.corrections import apply_corrections, correct_code_cell
from nptc_transform.pipeline import RunResult
from nptc_transform.rows import group_rows
from nptc_transform.workbook import Cell, ColumnRole, Sheet

DATASET_JSON_NAME = "import-dataset.json"

SCHEMA_VERSION = 1

_SNOMED_SYSTEM = "http://snomed.info/sct"
_LANGUAGE_EN_AU = "en-AU"
_SPECIMEN_ANY = "any"


@dataclass(frozen=True)
class Designation:
    """One seeded designation - the preferred term itself, or a synonym
    split out of the ``RCPA Synonyms`` cell (FR-04)."""

    term: str
    use: str
    language: str
    status: str


@dataclass(frozen=True)
class CodeBinding:
    """One seeded code binding, built from ``Cell.text`` alone - never
    ``Cell.raw``, and never passed through ``int``/``float`` (FR-06)."""

    system: str
    code: str
    fsn: str | None
    au_preferred_term: str | None
    edition_hint: str
    status: str


@dataclass(frozen=True)
class PropertyValue:
    """One property value - a discipline, subgroup or specimen assertion.
    ``code`` is ``None`` for discipline/subgroup always, and for a specimen
    value with no exact ``SPECIMEN_TABLE`` match (FR-88)."""

    value: str
    code: str | None = None


@dataclass(frozen=True)
class EntryProperties:
    discipline: tuple[PropertyValue, ...]
    subgroup: tuple[PropertyValue, ...]
    specimen: tuple[PropertyValue, ...]
    usage_guidance: str | None


@dataclass(frozen=True)
class EntrySource:
    """Where this entry came from, plus the hand-typed provenance FR-59/
    FR-85 no longer let ``Version``/``History`` be stored as editable
    fields (see the module docstring)."""

    sheet: str
    row: int
    legacy_version: str | None
    legacy_history: str | None


@dataclass(frozen=True)
class ImportEntry:
    business_key: str
    source: EntrySource
    preferred_term: str
    status: str
    specimen_unconstrained: bool
    designations: tuple[Designation, ...]
    code_bindings: tuple[CodeBinding, ...]
    properties: EntryProperties


@dataclass(frozen=True)
class BaselineRelease:
    """FR-76's synthetic baseline release - it qualifies the *release*, not
    the data: a machine-generated ``Release`` standing in for a curation
    cycle that never happened, because it is the left-hand side FR-60 diffs
    the first genuinely new release against."""

    name: str
    note: str


@dataclass(frozen=True)
class ImportDataset:
    source_filename: str
    source_sha256: str
    baseline_release: BaselineRelease
    entries: tuple[ImportEntry, ...]


def _cell_text(cell: Cell) -> str:
    return apply_corrections(cell.text)


def _optional_cell_text(cell: Cell | None) -> str | None:
    return _cell_text(cell) if cell is not None else None


def _build_designations(row_cells: Mapping[ColumnRole, Cell]) -> tuple[Designation, ...]:
    designations: list[Designation] = []
    preferred_cell = row_cells.get(ColumnRole.PREFERRED_TERM)
    if preferred_cell is not None:
        designations.append(
            Designation(
                term=_cell_text(preferred_cell),
                use="preferred",
                language=_LANGUAGE_EN_AU,
                status="active",
            )
        )
    synonyms_cell = row_cells.get(ColumnRole.SYNONYMS)
    if synonyms_cell is not None:
        for synonym in split_synonyms(_cell_text(synonyms_cell)):
            designations.append(
                Designation(term=synonym, use="synonym", language=_LANGUAGE_EN_AU, status="active")
            )
    return tuple(designations)


def _build_code_bindings(row_cells: Mapping[ColumnRole, Cell]) -> tuple[CodeBinding, ...]:
    code_cell = row_cells.get(ColumnRole.CODE)
    if code_cell is None:
        return ()
    fsn_cell = row_cells.get(ColumnRole.FSN)
    return (
        CodeBinding(
            system=_SNOMED_SYSTEM,
            code=correct_code_cell(_cell_text(code_cell)),
            fsn=_optional_cell_text(fsn_cell),
            au_preferred_term=None,
            edition_hint="unknown",
            status="active",
        ),
    )


def _build_specimen(row_cells: Mapping[ColumnRole, Cell]) -> tuple[tuple[PropertyValue, ...], bool]:
    """Mirrors ``cell_defects._scan_specimen`` exactly: 'Any' sets
    ``specimen_unconstrained`` but never short-circuits the remaining values
    in the cell. The published data is not guaranteed to keep 'Any' from
    co-occurring with a named specimen on the same row, so a co-occurring
    named value is seeded alongside it rather than silently discarded - the
    report already carries a finding for every value in the cell, named or
    not, and this must seed exactly what the report describes.
    """
    specimen_cell = row_cells.get(ColumnRole.SPECIMEN)
    if specimen_cell is None:
        return (), False
    values: list[PropertyValue] = []
    unconstrained = False
    for value in split_specimen_values(_cell_text(specimen_cell)):
        if value.casefold() == _SPECIMEN_ANY:
            unconstrained = True
            continue
        group = resolve_specimen_term(value)
        values.append(PropertyValue(value=value, code=group.specimen_code if group else None))
    return tuple(values), unconstrained


def _build_compound_property(
    row_cells: Mapping[ColumnRole, Cell], role: ColumnRole
) -> tuple[PropertyValue, ...]:
    cell = row_cells.get(role)
    if cell is None:
        return ()
    return tuple(PropertyValue(value=value) for value in split_compound_value(_cell_text(cell)))


def _business_key(sequence: int) -> str:
    return f"NPTC-{sequence:06d}"


def build_dataset(
    sheets: tuple[Sheet, ...], result: RunResult, *, release_name: str
) -> ImportDataset:
    """Builds the import dataset from ``sheets`` and ``result`` (FR-70, FR-76).

    Entries are numbered ``NPTC-000001``.. sequentially over rows carrying a
    preferred term, in ``(sheet name, row)`` order - a stable order across
    runs is what keeps FR-73's byte-identical guarantee meaningful once the
    pipeline actually transforms content, not just when it happens to agree
    with the workbook's own sheet order.
    """
    entries: list[ImportEntry] = []
    sequence = 0
    codeable = sorted(
        (sheet for sheet in sheets if resolves_code_column(sheet)), key=lambda sheet: sheet.name
    )
    for source_row in group_rows(codeable):
        row_cells = source_row.cells
        preferred_cell = row_cells.get(ColumnRole.PREFERRED_TERM)
        if preferred_cell is None:
            # A row that resolves a code binding with no preferred term is
            # MISSING_PREFERRED_TERM (cell_defects.py) - data-defect, so it
            # already blocked emission before build_dataset was called; a
            # row with neither a code nor a preferred term is simply not a
            # SPIA data row at all. Either way, there is nothing to seed.
            continue
        sequence += 1
        specimen, unconstrained = _build_specimen(row_cells)
        entries.append(
            ImportEntry(
                business_key=_business_key(sequence),
                source=EntrySource(
                    sheet=source_row.sheet,
                    row=source_row.row,
                    legacy_version=_optional_cell_text(row_cells.get(ColumnRole.VERSION)),
                    legacy_history=_optional_cell_text(row_cells.get(ColumnRole.HISTORY)),
                ),
                preferred_term=_cell_text(preferred_cell),
                status="active",
                specimen_unconstrained=unconstrained,
                designations=_build_designations(row_cells),
                code_bindings=_build_code_bindings(row_cells),
                properties=EntryProperties(
                    discipline=_build_compound_property(row_cells, ColumnRole.DISCIPLINE),
                    subgroup=_build_compound_property(row_cells, ColumnRole.SUBGROUP),
                    specimen=specimen,
                    usage_guidance=_optional_cell_text(row_cells.get(ColumnRole.GUIDANCE)),
                ),
            )
        )
    return ImportDataset(
        source_filename=result.source.filename,
        source_sha256=result.source.sha256,
        baseline_release=BaselineRelease(
            name=release_name,
            note="Synthetic baseline release representing the state at seeding (FR-76).",
        ),
        entries=tuple(entries),
    )


def _designation_payload(designation: Designation) -> dict[str, object]:
    return {
        "term": designation.term,
        "use": designation.use,
        "language": designation.language,
        "status": designation.status,
    }


def _code_binding_payload(binding: CodeBinding) -> dict[str, object]:
    return {
        "system": binding.system,
        "code": binding.code,
        "fsn": binding.fsn,
        "au_preferred_term": binding.au_preferred_term,
        "edition_hint": binding.edition_hint,
        "status": binding.status,
    }


def _property_value_payload(value: PropertyValue) -> dict[str, object]:
    return {"value": value.value, "code": value.code}


def _entry_payload(entry: ImportEntry) -> dict[str, object]:
    return {
        "business_key": entry.business_key,
        "source": {
            "sheet": entry.source.sheet,
            "row": entry.source.row,
            "legacy_version": entry.source.legacy_version,
            "legacy_history": entry.source.legacy_history,
        },
        "preferred_term": entry.preferred_term,
        "status": entry.status,
        "specimen_unconstrained": entry.specimen_unconstrained,
        "designations": [_designation_payload(d) for d in entry.designations],
        "code_bindings": [_code_binding_payload(b) for b in entry.code_bindings],
        "properties": {
            "discipline": [_property_value_payload(v) for v in entry.properties.discipline],
            "subgroup": [_property_value_payload(v) for v in entry.properties.subgroup],
            "specimen": [_property_value_payload(v) for v in entry.properties.specimen],
            "usage_guidance": entry.properties.usage_guidance,
        },
    }


def _dataset_payload(dataset: ImportDataset) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": __version__,
        "source": {"filename": dataset.source_filename, "sha256": dataset.source_sha256},
        "baseline_release": {
            "name": dataset.baseline_release.name,
            "note": dataset.baseline_release.note,
        },
        "entries": [_entry_payload(entry) for entry in dataset.entries],
    }


def _render_json(dataset: ImportDataset) -> str:
    payload = _dataset_payload(dataset)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def write_dataset(dataset: ImportDataset, report_dir: Path) -> None:
    """Writes ``import-dataset.json`` into ``report_dir``, overwriting in place.

    Mirrors ``report_writer.write_report``'s own discipline exactly: same
    directory, ``encoding="utf-8"``, ``newline="\\n"``, never appended or
    numbered (FR-73).
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / DATASET_JSON_NAME).write_text(
        _render_json(dataset), encoding="utf-8", newline="\n"
    )
