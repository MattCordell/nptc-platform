"""Writes the transform's report files.

Owns the envelope and the writing discipline (FR-73's determinism and
idempotency), not the report *content* - the human-readable grouping by
defect class with cell references is P0-8's.

Four rules keep every run byte-identical for identical input:

1. No clock-derived value anywhere in the output. The run start, duration and
   tool banner go to stderr only (see ``cli.py``); operators get the date
   from the report file's mtime.
2. No absolute paths. ``RunResult.source.filename`` is a basename (see
   ``pipeline.SourceRef``).
3. Every file is written ``encoding="utf-8"``, ``newline="\\n"`` - never the
   platform default, which is ``\\r\\n`` on Windows.
4. Every collection is explicitly sorted before being written - never a
   ``set``, never a ``dict`` relying on insertion order alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from nptc_transform import __version__
from nptc_transform.pipeline import RunResult

SCHEMA_VERSION = 1

REPORT_JSON_NAME = "report.json"
REPORT_MD_NAME = "report.md"


def _report_payload(result: RunResult) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": __version__,
        "source": {
            "filename": result.source.filename,
            "sha256": result.source.sha256,
        },
        "mode": str(result.mode),
        "finding_count": len(result.findings),
        "findings": [
            {
                "code": finding.code,
                "location": finding.location,
                "message": finding.message,
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


def _render_markdown(result: RunResult) -> str:
    lines = [
        "# Transform report",
        "",
        f"- Source: `{result.source.filename}` (sha256 `{result.source.sha256}`)",
        f"- Mode: `{result.mode}`",
        f"- Findings: {len(result.findings)}",
        "",
    ]
    if result.findings:
        lines.append("| Location | Code | Message |")
        lines.append("|---|---|---|")
        for finding in result.findings:
            lines.append(
                f"| {_escape_cell(finding.location)} "
                f"| {_escape_cell(finding.code)} "
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
