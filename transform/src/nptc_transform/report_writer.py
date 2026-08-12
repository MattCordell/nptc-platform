"""Writes the transform's report files.

Owns the envelope, the writing discipline (FR-73's determinism and
idempotency), and the human-readable grouping by defect class with structured
cell references and a required action per class (FR-72, P0-8). Band
assignment itself (FR-71) is owned by ``Finding.band``, and the action text
per code by ``actions.action_for``; this module only ever reads them.

Five rules keep every run byte-identical for identical input:

1. No clock-derived value anywhere in the output. The run start, duration and
   tool banner go to stderr only (see ``cli.py``); operators get the date
   from the report file's mtime.
2. No absolute paths. ``RunResult.source.filename`` is a basename (see
   ``pipeline.SourceRef``).
3. Every file is written ``encoding="utf-8"``, ``newline="\\n"`` - never the
   platform default, which is ``\\r\\n`` on Windows.
4. Every collection is explicitly sorted before being written - never a
   ``set``, never a ``dict`` relying on insertion order alone. Band counts
   are rendered in ``BAND_REPORT_ORDER``, for the same reason - not
   ``Band``'s own declaration order, which is unrelated to presentation.
5. Defect classes are rendered in an explicit group order
   (``BAND_REPORT_ORDER``, then declared ``FindingCode`` order, then the code
   itself as a final tiebreak for an unregistered code) - never
   ``json.dumps(sort_keys=True)``, which only sorts object *keys*, not array
   elements, so it does not cover this at all. Findings within a group keep
   ``RunResult``'s own canonical order, from a single stable partitioning
   pass - never re-sorted a second time.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from nptc_transform import __version__
from nptc_transform.actions import action_for
from nptc_transform.bands import BAND_REPORT_ORDER, Band, FindingCode, band_for, blocks_import
from nptc_transform.cellref import CellRef
from nptc_transform.findings import Finding
from nptc_transform.misspelling import THRESHOLDS, AuthoritySource
from nptc_transform.pipeline import RunResult

SCHEMA_VERSION = 7

REPORT_JSON_NAME = "report.json"
REPORT_MD_NAME = "report.md"

#: Declared ``FindingCode`` order, computed once - the tiebreak
#: ``_group_findings`` uses so an unregistered code (there should never be
#: one - see ``bands.band_for``) sorts last rather than raising or sorting
#: arbitrarily by insertion order.
_CODE_ORDER: dict[str, int] = {code: index for index, code in enumerate(FindingCode)}


@dataclass(frozen=True)
class _DefectClass:
    """One ``FindingCode``'s findings, grouped for FR-72's rendering.

    Private to this module - nothing else consumes a grouped view of
    findings, so there is no reason to make this a public type.
    """

    band: Band
    code: str
    findings: tuple[Finding, ...]


def _group_findings(findings: tuple[Finding, ...]) -> tuple[_DefectClass, ...]:
    """Partitions ``findings`` by code, in FR-72's group-presentation order.

    A single stable pass: each finding is appended to its code's bucket in
    the order it already appears in (``RunResult.findings`` is sorted by
    ``Finding.sort_key`` before this ever runs), so a group's own findings
    need no second sort. Only the *groups* are sorted, explicitly, by
    ``(BAND_REPORT_ORDER.index(band), declared FindingCode order, code)`` -
    never by dict/set iteration order, which is what
    ``json.dumps(sort_keys=True)`` alone would leave to chance for the array
    this produces.
    """
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.code].append(finding)
    classes = [
        _DefectClass(band=band_for(code), code=code, findings=tuple(items))
        for code, items in grouped.items()
    ]
    classes.sort(
        key=lambda defect_class: (
            BAND_REPORT_ORDER.index(defect_class.band),
            _CODE_ORDER.get(defect_class.code, len(FindingCode)),
            defect_class.code,
        )
    )
    return tuple(classes)


def _terminology_payload(result: RunResult) -> object:
    """The terminology run's provenance block, or ``null`` if none ran.

    ``null`` and "a run that produced no findings" are different facts, and
    conflating them is how a report that never contacted a server comes to
    read as a clean validation. The resolved version URIs are FR-48's
    requirement: a validation you cannot reproduce is not evidence.

    Note what this does to FR-73: two runs against the same workbook stay
    byte-identical only while the server resolves the same edition versions.
    That is the intended reading - the SNOMED release is an input to the run,
    and this block is what records which one it was.
    """
    run = result.terminology
    if run is None:
        return None
    return {
        "codes_checked": run.codes_checked,
        "codes_not_checked": run.codes_not_checked,
        "editions": [
            {"label": edition.label, "resolved_versions": list(edition.resolved_versions)}
            for edition in run.editions
        ],
        "unresolved_fsn_count": run.unresolved_fsn_count,
    }


def _designations_payload(result: RunResult) -> object:
    """FR-97's provenance block, or ``null`` if reconciliation never ran.

    ``label_confirmations`` is not decoration: it is the only per-row request
    this tool ever issues (``client.py``'s own ``validate_code`` docstring
    reserves it for exactly this pass), and printing the count is what makes
    "the delta is the workload" auditable rather than merely asserted. A run
    where it approaches ``labels_reconciled`` is a run where something is
    wrong with the server's designation serving, and nothing else here would
    show it.
    """
    run = result.designations
    if run is None:
        return None
    return {
        "labels_reconciled": run.labels_reconciled,
        "labels_not_reconciled": run.labels_not_reconciled,
        "label_confirmations": run.label_confirmations,
    }


def _misspellings_payload(result: RunResult) -> object:
    """FR-79's provenance block, or ``null`` if the pass never ran at all
    (it always runs when the pipeline does, unlike ``terminology``/
    ``designations`` - see ``RunResult.misspellings``'s docstring - so in
    practice this is only ``null`` for a ``RunResult`` built by hand, e.g. in
    a test).

    ``thresholds`` is ``misspelling.THRESHOLDS`` echoed verbatim, not
    restated: a reader must never have to cross-reference the source to know
    what produced ``PROBABLE_MISSPELLING``/``INCONSISTENT_SPELLING``.
    """
    run = result.misspellings
    if run is None:
        return None
    return {
        "cells_scanned": run.cells_scanned,
        "tokens_considered": run.tokens_considered,
        "probable_misspelling_count": run.probable_misspelling_count,
        "inconsistent_spelling_count": run.inconsistent_spelling_count,
        "authority_source": str(run.authority_source),
        "thresholds": dict(THRESHOLDS),
    }


def _drift_payload(result: RunResult) -> object:
    """FR-75's provenance block, or ``null`` if the pass never ran at all -
    the same ``None``-vs-zero-findings distinction ``_designations_payload``
    makes, and for the same reason: a clean run and a run that never contacted
    the server must not read identically.
    """
    run = result.drift
    if run is None:
        return None
    return {
        "rows_examined": run.rows_examined,
        "rows_excluded": run.rows_excluded,
        "term_specimen_not_modelled_count": run.term_specimen_not_modelled_count,
        "term_specimen_differs_count": run.term_specimen_differs_count,
        "term_timing_not_modelled_count": run.term_timing_not_modelled_count,
        "specimen_table_entries_unresolved": run.specimen_table_entries_unresolved,
        "specimen_column_values_unmapped": run.specimen_column_values_unmapped,
        "describe_requests": run.describe_requests,
        "classification_requests": run.classification_requests,
        "resolved_versions": list(run.resolved_versions),
    }


def _location_payload(location: CellRef) -> dict[str, object]:
    return {
        "sheet": location.sheet,
        "column": location.column_letter,
        "row": location.row,
        "ref": str(location),
    }


def _defect_class_payload(defect_class: _DefectClass) -> dict[str, object]:
    """One group's payload: ``code``/``band``/``action`` live here once, not
    per finding - what "organised by defect class" means structurally.
    ``blocks_import`` is denormalised deliberately: a consumer must never
    re-implement ``blocks_import()`` from a band string of its own.
    """
    return {
        "band": str(defect_class.band),
        "blocks_import": blocks_import(defect_class.band),
        "code": defect_class.code,
        "action": action_for(defect_class.code),
        "finding_count": len(defect_class.findings),
        "findings": [
            {
                "location": _location_payload(finding.location),
                "message": finding.message,
            }
            for finding in defect_class.findings
        ],
    }


def _report_payload(result: RunResult) -> dict[str, object]:
    band_counts = result.band_counts
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": __version__,
        "source": {
            "filename": result.source.filename,
            "sha256": result.source.sha256,
        },
        "mode": str(result.mode),
        "finding_count": len(result.findings),
        "blocking": result.has_blocking_findings,
        "band_counts": {str(band): band_counts[band] for band in Band},
        "terminology": _terminology_payload(result),
        "designations": _designations_payload(result),
        "misspellings": _misspellings_payload(result),
        "drift": _drift_payload(result),
        "defect_classes": [
            _defect_class_payload(defect_class) for defect_class in _group_findings(result.findings)
        ],
    }


def _render_json(result: RunResult) -> str:
    payload = _report_payload(result)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _escape_cell(value: str) -> str:
    """Makes ``value`` safe to interpolate into a Markdown table cell.

    A finding's location or message is workbook-derived text, so it can contain
    the two characters that break a table row: ``|`` (splits the row into extra
    columns, silently truncating the rest) and a line break (ends the row
    mid-cell, and would put a literal ``\\r\\n`` into the file on Windows,
    violating rule 3 above). Both are escaped rather than stripped so the
    defect stays visible to the operator.

    Not used for the Cell column: that value is wrapped in a code span
    (``_code_span``), and backslash escapes are inert inside one per
    CommonMark, so a backslash-escaped backtick would still close the span.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _code_span(value: str) -> str:
    """Wraps ``value`` in a Markdown code span, CommonMark-correct even when
    ``value`` itself contains a backtick (legal in an Excel sheet name -
    Excel forbids only ``: \\ / ? * [ ]``).

    Backslash escapes are inert inside a code span, so the only way to put a
    literal backtick inside one is a fence - a run of backticks - longer than
    any backtick run already in ``value``. A leading/trailing space pads the
    span when ``value`` itself starts or ends with a backtick, so that
    backtick isn't read as part of the fence.
    """
    longest_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * (longest_run + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _render_terminology(result: RunResult) -> list[str]:
    """The human-readable half of the provenance block above.

    Says "not run" explicitly rather than omitting the section: a reader
    scanning report.md for whether the codes were checked must not have to
    infer it from the absence of terminology findings.
    """
    run = result.terminology
    if run is None:
        return ["- Terminology validation: `not run`", ""]
    lines = [
        f"- Terminology validation: {run.codes_checked} code(s) checked, "
        f"{run.codes_not_checked} not checked",
    ]
    if run.unresolved_fsn_count:
        # Not decoration: a nonzero count here means the FR-99 semantic-tag
        # check could not run for that many concepts at all (no identifiable
        # FSN designation came back), which would otherwise pass silently
        # and permanently with nothing to show it never ran.
        lines.append(
            f"- {run.unresolved_fsn_count} concept(s) had no identifiable FSN designation; "
            "the FR-99 semantic-tag check could not run for them"
        )
    lines.extend(
        [
            "",
            "| Edition | Resolved version(s) |",
            "|---|---|",
        ]
    )
    lines.extend(
        f"| {_escape_cell(edition.label)} "
        f"| {_escape_cell(', '.join(edition.resolved_versions) or '(not reported)')} |"
        for edition in run.editions
    )
    lines.append("")
    return lines


def _render_designations(result: RunResult) -> list[str]:
    """The human-readable half of FR-97's provenance block.

    Says "not run" explicitly, for the same reason ``_render_terminology``
    does: a reader must not have to infer it from the absence of a
    ``LABEL_*`` finding, which a clean workbook produces just as often as a
    reconciliation pass that never ran at all.
    """
    run = result.designations
    if run is None:
        return ["- Designation reconciliation: `not run`", ""]
    return [
        f"- Designation reconciliation: {run.labels_reconciled} label(s) reconciled, "
        f"{run.labels_not_reconciled} not reconciled, {run.label_confirmations} "
        "confirmed against the server (FR-97)",
        "",
    ]


def _render_misspellings(result: RunResult) -> list[str]:
    """The human-readable half of FR-79's provenance block.

    Says "not run" explicitly for the same reason ``_render_designations``
    does - and when the authority whitelist was empty (``WORKBOOK_ONLY``,
    ``results=None`` upstream), states the precision caveat explicitly
    rather than letting a reader assume every run has the same reliability.
    """
    run = result.misspellings
    if run is None:
        return ["- Misspelling detection: `not run`", ""]
    lines = [
        f"- Misspelling detection: {run.cells_scanned} cell(s) scanned, "
        f"{run.tokens_considered} comparable token occurrence(s) considered, "
        f"{run.probable_misspelling_count} probable misspelling(s), "
        f"{run.inconsistent_spelling_count} inconsistent spelling(s) (FR-79)",
    ]
    if run.authority_source is AuthoritySource.WORKBOOK_ONLY:
        lines.append(
            "- No terminology sweep was available for this run: the authority "
            "whitelist is empty, so precision is lower than a run with "
            "`--check-terminology` - a genuine SNOMED-served spelling with no "
            "corpus support of its own may be flagged that a sweep-backed run "
            "would have recognised as authoritative and left alone."
        )
    lines.append("")
    return lines


def _render_drift(result: RunResult) -> list[str]:
    """The human-readable half of FR-75's provenance block.

    Says "not run" explicitly for the same reason ``_render_designations``
    does, and calls out the two provenance counters only when nonzero - a
    reader must not have to hunt for them in ``report.json`` when there is
    nothing to say.
    """
    run = result.drift
    if run is None:
        return ["- Semantic drift review: `not run`", ""]
    lines = [
        f"- Semantic drift review: {run.rows_examined} row(s) examined, "
        f"{run.rows_excluded} not examined, "
        f"{run.term_specimen_not_modelled_count} unmodelled specimen assertion(s), "
        f"{run.term_specimen_differs_count} differing specimen assertion(s), "
        f"{run.term_timing_not_modelled_count} unmodelled timing assertion(s) (FR-75)",
    ]
    if run.specimen_table_entries_unresolved:
        lines.append(
            f"- {run.specimen_table_entries_unresolved} specimen-table concept(s) could not be "
            "resolved against the server; those group(s) fall back to their hand-typed terms only"
        )
    if run.specimen_column_values_unmapped:
        lines.append(
            f"- {run.specimen_column_values_unmapped} distinct `Specimen` column value(s) map to "
            "no group in the specimen table - a coverage gap, never fed back into classification"
        )
    lines.append("")
    return lines


def _render_defect_classes(classes: tuple[_DefectClass, ...]) -> list[str]:
    """FR-72's grouped findings section: band, then defect class, then a
    ``| Cell | Detail |`` table - code and band are the enclosing headings,
    the required action its own paragraph above the table, so neither is
    repeated per row the way the old flat table did.

    Bands/codes with zero findings are omitted entirely - the opposite rule
    to the provenance sections above, and deliberately so: the band-count
    table already states the zero, so there is no "not run vs found nothing"
    ambiguity here for an empty section to guard against, and one would be
    pure noise.
    """
    lines = ["## Findings by defect class", ""]
    if not classes:
        lines.extend(["No findings.", ""])
        return lines
    lines.extend(
        [
            "Blocking classes first. Every finding cites the cell it came from as "
            "`Sheet!ColumnRow` - open that sheet and cell in the published workbook.",
            "",
        ]
    )
    for band in BAND_REPORT_ORDER:
        band_classes = [defect_class for defect_class in classes if defect_class.band is band]
        if not band_classes:
            continue
        heading = f"### {band}"
        if blocks_import(band):
            heading += " - blocks import"
        lines.append(heading)
        lines.append("")
        for defect_class in band_classes:
            lines.append(f"#### `{defect_class.code}` - {len(defect_class.findings)} finding(s)")
            lines.append("")
            lines.append(f"**Required action:** {action_for(defect_class.code)}")
            lines.append("")
            lines.append("| Cell | Detail |")
            lines.append("|---|---|")
            for finding in defect_class.findings:
                # The pipe is escaped before the fence goes on, not by
                # `_escape_cell` - table-cell splitting happens before a code
                # span's contents are parsed, so a backslash-escaped `|`
                # still protects the row even though it renders literally.
                ref = str(finding.location).replace("|", "\\|")
                lines.append(f"| {_code_span(ref)} | {_escape_cell(finding.message)} |")
            lines.append("")
    return lines


def _render_markdown(result: RunResult) -> str:
    band_counts = result.band_counts
    lines = [
        "# Transform report",
        "",
        f"- Source: `{result.source.filename}` (sha256 `{result.source.sha256}`)",
        f"- Mode: `{result.mode}`",
        f"- Findings: {len(result.findings)}",
        f"- Blocking: `{result.has_blocking_findings}`",
        "",
        "| Band | Count |",
        "|---|---|",
    ]
    lines.extend(f"| {band} | {band_counts[band]} |" for band in BAND_REPORT_ORDER)
    lines.append("")
    lines.extend(_render_terminology(result))
    lines.extend(_render_designations(result))
    lines.extend(_render_misspellings(result))
    lines.extend(_render_drift(result))
    lines.extend(_render_defect_classes(_group_findings(result.findings)))
    return "\n".join(lines)


def write_report(result: RunResult, report_dir: Path) -> None:
    """Writes ``report.json`` and ``report.md`` into ``report_dir``, overwriting in place.

    Never appends and never numbers a file (``report-2.json``) - overwriting
    is what makes re-running against the same input, or into the same
    directory, a byte-identical no-op.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / REPORT_JSON_NAME).write_text(_render_json(result), encoding="utf-8", newline="\n")
    (report_dir / REPORT_MD_NAME).write_text(
        _render_markdown(result), encoding="utf-8", newline="\n"
    )
