"""Builds and writes the import dataset (FR-70, FR-76).

``build_dataset`` consumes ``RunResult.findings`` in process, never the report
file (ADR-0009). Call it only once the caller has confirmed ``result`` carries no
blocking finding (``RunResult.has_blocking_findings``). A caller that doesn't is
at fault; this module does not re-check, in the "true by construction" style
``Finding`` uses. That includes FR-05: ``collision_check.py`` blocks a dataset
whose designations the loader would refuse, until RCPA-QAP resolves them.

One file, ``import-dataset.json``, goes into the same ``--report-dir`` as
``report.json``/``report.md``, so the CLI's "no file outside --report-dir is
touched" invariant holds. The envelope discipline is ``report_writer.py``'s: own
``schema_version``, no clock value, basename-only source, deterministic order,
``encoding="utf-8"``, ``newline="\\n"``, overwrite in place (FR-73).

**What is carried.** ``Length``, ``Version`` and ``History`` are not entry
fields. ``Length`` must not be storable (FR-85) and is computed in the export
layer. ``Version``/``History`` are generated from release membership (FR-59),
which ``baseline_release`` replaces. The existing hand-typed values carry real
provenance, so they are kept verbatim under ``source.legacy_version`` and
``legacy_history``: immutable seeding provenance, not editable fields, so cutover
destroys no information.

**Specimen: the reviewed map decides** (FR-88, FR-89, ADR-0044). A specimen value
keeps the workbook string and takes its code from the reviewed specimen map
(``cell_defects.resolve_specimen_term``). A value the map marks as needing no
specimen yields no value. An unmapped value never reaches this module: it is a
blocking finding (``SPECIMEN_VALUE_UNMAPPED``), so ``build_dataset`` is not called.
``'Any'`` is coded as the specimen root like any other string.

**Specimen display.** The AU preferred term the specimen map check resolved for the code
(``SpecimenMapRun.preferred_terms``), so the list read model shows a SNOMED CT term and not the
workbook's wording. ``None`` when the run had no sweep; the CLI refuses ``--emit-dataset``
without ``--check-terminology``, so an emitted dataset always carries it.

**Other terminology-served enrichment is not done here.** Without
``--check-terminology``, ``edition_hint`` is always ``"unknown"`` and
``fsn``/``au_preferred_term`` come from the published cell text or ``None``.
Filling them from a live sweep's served designations (FR-82) needs the per-code
``SweepResult`` threaded further than ``RunResult`` carries it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nptc_transform import __version__
from nptc_transform.cell_defects import (
    resolve_specimen_term,
    split_compound_value,
    split_specimen_values,
    split_synonyms,
)
from nptc_transform.corrections import apply_corrections, correct_code_cell
from nptc_transform.pipeline import RunResult
from nptc_transform.rows import has_code_binding, seedable_rows
from nptc_transform.workbook import Cell, ColumnRole, Sheet

DATASET_JSON_NAME = "import-dataset.json"

SCHEMA_VERSION = 3

_SNOMED_SYSTEM = "http://snomed.info/sct"
_LANGUAGE_EN_AU = "en-AU"


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
    ``code`` is ``None`` for discipline/subgroup always, and never for a specimen.
    ``display`` is the specimen code's AU preferred term, and ``None`` for the others."""

    value: str
    code: str | None = None
    display: str | None = None


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
    if not has_code_binding(row_cells):
        return ()
    code_cell = row_cells[ColumnRole.CODE]
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


def _build_specimen(
    row_cells: Mapping[ColumnRole, Cell], preferred_terms: Mapping[str, str]
) -> tuple[PropertyValue, ...]:
    """Mirrors ``cell_defects._scan_specimen``. A value the reviewed map does not
    cover is a caller error (the run was blocked), so it raises rather than seeding
    a specimen with no code.
    """
    specimen_cell = row_cells.get(ColumnRole.SPECIMEN)
    if specimen_cell is None:
        return ()
    values: list[PropertyValue] = []
    for value in split_specimen_values(_cell_text(specimen_cell)):
        entry = resolve_specimen_term(value)
        if entry is None:
            raise ValueError(f"specimen {value!r} is not in the specimen map; the run was blocked")
        if entry.code is not None:
            values.append(
                PropertyValue(value=value, code=entry.code, display=preferred_terms.get(entry.code))
            )
    return tuple(values)


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

    Entries are numbered ``NPTC-000001``.. over rows carrying both a preferred
    term and a code binding, in ``(sheet name, row)`` order. A stable order is
    what keeps FR-73's byte-identical guarantee meaningful once the pipeline
    transforms content, not only when it happens to match the workbook's sheet
    order. A row skipped for either reason (FR-100) shifts every later business
    key down by one; no gap is left.
    """
    preferred_terms = dict(result.specimen_map.preferred_terms) if result.specimen_map else {}
    entries: list[ImportEntry] = []
    for sequence, source_row in enumerate(seedable_rows(sheets), start=1):
        row_cells = source_row.cells
        preferred_cell = row_cells[ColumnRole.PREFERRED_TERM]
        specimen = _build_specimen(row_cells, preferred_terms)
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
    return {"value": value.value, "code": value.code, "display": value.display}


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

    Follows ``report_writer.write_report``: same directory, ``encoding="utf-8"``,
    ``newline="\\n"``, never appended or numbered (FR-73).
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / DATASET_JSON_NAME).write_text(
        _render_json(dataset), encoding="utf-8", newline="\n"
    )
