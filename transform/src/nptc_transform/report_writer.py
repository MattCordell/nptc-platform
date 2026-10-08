"""Writes the transform's report files.

Owns the envelope, the writing discipline (FR-73's determinism and
idempotency), and the human-readable grouping by defect class with structured
cell references and a required action per class (FR-72). Band assignment
(FR-71) belongs to ``Finding.band`` and the action text to
``actions.action_for``; this module only reads them.

Five rules keep every run byte-identical for identical input:

1. No clock-derived value in the output. The run start, duration and tool
   banner go to stderr only (see ``cli.py``); operators get the date from the
   report file's mtime.
2. No absolute paths. ``RunResult.source.filename`` is a basename (see
   ``pipeline.SourceRef``).
3. Every file is written ``encoding="utf-8"``, ``newline="\\n"``, never the
   platform default, which is ``\\r\\n`` on Windows.
4. Every collection is explicitly sorted: never a ``set``, never a ``dict``
   relying on insertion order. Band counts use ``BAND_REPORT_ORDER`` for the
   same reason, not ``Band``'s declaration order.
5. Defect classes render in an explicit group order (``BAND_REPORT_ORDER``, then
   declared ``FindingCode`` order, then the code as a tiebreak for an
   unregistered code). ``json.dumps(sort_keys=True)`` does not cover this: it
   sorts object *keys*, not array elements. Findings within a group keep
   ``RunResult``'s canonical order from one stable partitioning pass, never
   re-sorted.
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

#: Bumped when a new ``FindingCode`` can appear in ``defect_classes`` as well as
#: when the shape changes, so a consumer pinned to the old vocabulary can tell.
SCHEMA_VERSION = 10

REPORT_JSON_NAME = "report.json"
REPORT_MD_NAME = "report.md"

#: Declared ``FindingCode`` order, the tiebreak ``_group_findings`` uses so an
#: unregistered code (there should be none; see ``bands.band_for``) sorts last
#: rather than raising or following insertion order.
_CODE_ORDER: dict[str, int] = {code: index for index, code in enumerate(FindingCode)}


@dataclass(frozen=True)
class _DefectClass:
    """One ``FindingCode``'s findings, grouped for FR-72's rendering."""

    band: Band
    code: str
    findings: tuple[Finding, ...]


def _group_findings(findings: tuple[Finding, ...]) -> tuple[_DefectClass, ...]:
    """Partitions ``findings`` by code, in FR-72's group-presentation order.

    One stable pass: each finding joins its code's bucket in the order it already
    has (``RunResult.findings`` is sorted by ``Finding.sort_key`` first), so a
    group needs no second sort. Only the *groups* are sorted, by
    ``(BAND_REPORT_ORDER.index(band), declared FindingCode order, code)``, never
    by dict or set iteration order, which ``json.dumps(sort_keys=True)`` would
    leave to chance for the array this produces.
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

    ``null`` and "a run that produced no findings" are different facts; conflating
    them makes a report that never contacted a server read as a clean validation.
    The resolved version URIs are FR-48's requirement: a validation you cannot
    reproduce is not evidence.

    Two runs against the same workbook are byte-identical (FR-73) only while the
    server resolves the same edition versions. That is intended: the SNOMED
    release is an input to the run, and this block records which one.
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

    ``label_confirmations`` is the only per-row request this tool issues
    (``client.py``'s ``validate_code`` reserves it for this pass), and reporting
    the count makes "the delta is the workload" auditable. A count approaching
    ``labels_reconciled`` means the server's designation serving is wrong, which
    nothing else here would show.
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
    """FR-79's provenance block, or ``null`` if the pass never ran. Unlike
    ``terminology``/``designations`` it always runs with the pipeline (see
    ``RunResult.misspellings``), so ``null`` only occurs for a hand-built
    ``RunResult``, such as in a test.

    ``thresholds`` is ``misspelling.THRESHOLDS`` echoed, so a reader need not
    consult the source to know what produced ``PROBABLE_MISSPELLING`` or
    ``INCONSISTENT_SPELLING``.
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
    """FR-75's provenance block, or ``null`` if the pass never ran: the same
    ``None``-versus-zero-findings distinction as ``_designations_payload``, so a
    clean run and one that never contacted the server do not read alike.
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
        "describe_requests": run.describe_requests,
        "classification_requests": run.classification_requests,
        "resolved_versions": list(run.resolved_versions),
    }


def _specimen_map_payload(result: RunResult) -> object:
    """The specimen map check's provenance block, or ``null`` if it never ran."""
    run = result.specimen_map
    if run is None:
        return None
    return {
        "codes_checked": run.codes_checked,
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
    """One group's payload: ``code``, ``band`` and ``action`` appear once here, not
    per finding. ``blocks_import`` is denormalised on purpose so a consumer never
    re-implements ``blocks_import()`` from a band string.
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
        "specimen_map": _specimen_map_payload(result),
        "defect_classes": [
            _defect_class_payload(defect_class) for defect_class in _group_findings(result.findings)
        ],
    }


def _render_json(result: RunResult) -> str:
    payload = _report_payload(result)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _escape_cell(value: str) -> str:
    """Makes ``value`` safe to interpolate into a Markdown table cell.

    A finding's location or message is workbook-derived text, so it can hold the
    two characters that break a table row: ``|`` (splits the row into extra
    columns) and a line break (ends the row mid-cell, and would put a literal
    ``\\r\\n`` in the file on Windows, violating rule 3). Both are escaped, not
    stripped, so the defect stays visible.

    Not used for the Cell column, which is wrapped in a code span
    (``_code_span``): backslash escapes are inert inside one (CommonMark), so an
    escaped backtick would still close the span.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _code_span(value: str) -> str:
    """Wraps ``value`` in a Markdown code span, CommonMark-correct when ``value``
    contains a backtick (legal in an Excel sheet name, which forbids only
    ``: \\ / ? * [ ]``).

    Backslash escapes are inert inside a code span, so a literal backtick needs a
    fence longer than any backtick run in ``value``. A space pads the span when
    ``value`` starts or ends with a backtick, so it is not read as part of the
    fence.
    """
    longest_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * (longest_run + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _render_terminology(result: RunResult) -> list[str]:
    """The human-readable half of the provenance block above.

    Says "not run" rather than omitting the section, so a reader scanning
    report.md need not infer it from the absence of terminology findings.
    """
    run = result.terminology
    if run is None:
        return ["- Terminology validation: `not run`", ""]
    lines = [
        f"- Terminology validation: {run.codes_checked} distinct code(s) checked, "
        f"{run.codes_not_checked} binding(s) not checked",
    ]
    if run.unresolved_fsn_count:
        # A nonzero count means the FR-99 semantic-tag check could not run for
        # that many concepts (no identifiable FSN came back), which would
        # otherwise pass silently and permanently.
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

    Says "not run" for the reason ``_render_terminology`` does: a clean workbook
    and a pass that never ran both produce no ``LABEL_*`` finding.
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

    Says "not run" as ``_render_designations`` does. When the authority whitelist
    was empty (``WORKBOOK_ONLY``, ``results=None`` upstream) it states the
    precision caveat, so a reader does not assume every run is equally reliable.
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

    Says "not run" as ``_render_designations`` does, and shows the two provenance
    counters only when nonzero, so a reader need not hunt for them in
    ``report.json``.
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
    lines.append("")
    return lines


def _render_specimen_map(result: RunResult) -> list[str]:
    """The human-readable half of the specimen map check's provenance block."""
    run = result.specimen_map
    if run is None:
        return ["- Specimen map check: `not run`", ""]
    return [
        f"- Specimen map check: {run.codes_checked} code(s) checked against "
        "`<<123038009` in SNOMED CT-AU (FR-88)",
        "",
    ]


def _render_defect_classes(classes: tuple[_DefectClass, ...]) -> list[str]:
    """FR-72's grouped findings section: band, then defect class, then a
    ``| Cell | Detail |`` table. Code and band are the enclosing headings and the
    required action a paragraph above the table, so neither repeats per row.

    Bands and codes with zero findings are omitted, the opposite of the
    provenance sections above and deliberately: the band-count table already
    states the zero, so there is no "not run versus found nothing" ambiguity for
    an empty section to guard against.
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
                # The pipe is escaped here, not by `_escape_cell`: table-cell
                # splitting happens before a code span is parsed, so the table
                # parser consumes the backslash first, as it does for `\|` in a
                # normal cell.
                #
                # A literal `\` needs no escape: CellRef.sheet cannot contain
                # one, since Excel and openpyxl both reject a backslash in a
                # worksheet title.
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
    lines.extend(_render_specimen_map(result))
    lines.extend(_render_defect_classes(_group_findings(result.findings)))
    return "\n".join(lines)


def write_report(result: RunResult, report_dir: Path) -> None:
    """Writes ``report.json`` and ``report.md`` into ``report_dir``, overwriting in place.

    Never appends and never numbers a file (``report-2.json``): overwriting is
    what makes a re-run against the same input or directory a byte-identical
    no-op.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / REPORT_JSON_NAME).write_text(_render_json(result), encoding="utf-8", newline="\n")
    (report_dir / REPORT_MD_NAME).write_text(
        _render_markdown(result), encoding="utf-8", newline="\n"
    )
