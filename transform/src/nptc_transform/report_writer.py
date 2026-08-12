"""Writes the transform's report files.

Owns the envelope and the writing discipline (FR-73's determinism and
idempotency), not the report *content* - the human-readable grouping by
defect class with cell references is P0-8's. Band assignment itself (FR-71)
is owned by ``Finding.band``; this module only ever reads it.

Four rules keep every run byte-identical for identical input:

1. No clock-derived value anywhere in the output. The run start, duration and
   tool banner go to stderr only (see ``cli.py``); operators get the date
   from the report file's mtime.
2. No absolute paths. ``RunResult.source.filename`` is a basename (see
   ``pipeline.SourceRef``).
3. Every file is written ``encoding="utf-8"``, ``newline="\\n"`` - never the
   platform default, which is ``\\r\\n`` on Windows.
4. Every collection is explicitly sorted before being written - never a
   ``set``, never a ``dict`` relying on insertion order alone. Band counts
   are rendered in ``Band``'s declaration order, for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from nptc_transform import __version__
from nptc_transform.bands import Band
from nptc_transform.misspelling import THRESHOLDS, AuthoritySource
from nptc_transform.pipeline import RunResult

SCHEMA_VERSION = 6

REPORT_JSON_NAME = "report.json"
REPORT_MD_NAME = "report.md"


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
        "findings": [
            {
                "code": finding.code,
                "location": finding.location,
                "message": finding.message,
                "band": str(finding.band),
            }
            for finding in result.findings
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
    """
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


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
    lines.extend(f"| {band} | {band_counts[band]} |" for band in Band)
    lines.append("")
    lines.extend(_render_terminology(result))
    lines.extend(_render_designations(result))
    lines.extend(_render_misspellings(result))
    lines.extend(_render_drift(result))
    if result.findings:
        lines.append("| Location | Code | Band | Message |")
        lines.append("|---|---|---|---|")
        for finding in result.findings:
            lines.append(
                f"| {_escape_cell(finding.location)} "
                f"| {_escape_cell(finding.code)} "
                f"| {_escape_cell(str(finding.band))} "
                f"| {_escape_cell(finding.message)} |"
            )
    else:
        lines.append("No findings.")
    lines.append("")
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
