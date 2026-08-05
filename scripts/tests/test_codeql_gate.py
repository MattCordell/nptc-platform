"""Unit tests for scripts/codeql_gate.py (issue #87 review: extracted from an
inline codeql.yml heredoc that had no committed test/fixture and duplicated its
SARIF-parsing logic in two places that could drift without anything noticing)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codeql_gate as gate


def _sarif(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"runs": runs}


def _rule(rule_id: str, severity: str | None = None) -> dict[str, Any]:
    props = {"security-severity": severity} if severity is not None else {}
    return {"id": rule_id, "properties": props}


def _result(rule_id: str, message: str = "", locations: list[Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ruleId": rule_id, "message": {"text": message}}
    result["locations"] = (
        locations
        if locations is not None
        else [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "app.py"},
                    "region": {"startLine": 10},
                }
            }
        ]
    )
    return result


def _write_sarif(path: Path, runs: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(_sarif(runs)), encoding="utf-8")


# --- extract_findings ---------------------------------------------------------------


def test_extract_findings_reads_rule_metadata_from_driver() -> None:
    sarif = _sarif(
        [
            {
                "tool": {"driver": {"rules": [_rule("py/sql-injection", "9.8")]}},
                "results": [_result("py/sql-injection", "SQL injection risk")],
            }
        ]
    )
    findings = gate.extract_findings(sarif)
    assert len(findings) == 1
    assert findings[0].rule_id == "py/sql-injection"
    assert findings[0].severity == 9.8
    assert findings[0].message == "SQL injection risk"
    assert findings[0].path == "app.py"
    assert findings[0].line == 10


def test_extract_findings_reads_rule_metadata_from_extensions() -> None:
    """Most security-extended rules ship as a tool extension, not on the driver -
    a lookup that only checks the driver would silently gate on nothing."""
    sarif = _sarif(
        [
            {
                "tool": {
                    "driver": {"rules": []},
                    "extensions": [{"rules": [_rule("py/clear-text-logging", "7.5")]}],
                },
                "results": [_result("py/clear-text-logging")],
            }
        ]
    )
    findings = gate.extract_findings(sarif)
    assert findings[0].severity == 7.5


def test_extract_findings_handles_rule_with_no_severity() -> None:
    sarif = _sarif(
        [
            {
                "tool": {"driver": {"rules": [_rule("py/unused-import")]}},
                "results": [_result("py/unused-import")],
            }
        ]
    )
    findings = gate.extract_findings(sarif)
    assert findings[0].severity is None


def test_extract_findings_handles_empty_locations_list_without_raising() -> None:
    """SARIF permits "locations": [] (CodeQL emits it for some non-location-bound
    results) - result.get("locations", [{}])[0] raises IndexError on that, since
    the default only applies when the key is absent, not when it's empty."""
    sarif = _sarif(
        [
            {
                "tool": {"driver": {"rules": [_rule("py/some-rule", "9.0")]}},
                "results": [_result("py/some-rule", "msg", locations=[])],
            }
        ]
    )
    findings = gate.extract_findings(sarif)
    assert findings[0].path == "?"
    assert findings[0].line == "?"
    assert findings[0].severity == 9.0


def test_extract_findings_handles_missing_locations_key() -> None:
    sarif = _sarif(
        [
            {
                "tool": {"driver": {"rules": []}},
                "results": [{"ruleId": "py/x", "message": {"text": "m"}}],
            }
        ]
    )
    findings = gate.extract_findings(sarif)
    assert findings[0].path == "?"


def test_extract_findings_empty_sarif_yields_no_findings() -> None:
    assert gate.extract_findings(_sarif([])) == []


# --- find_sarif_files / load_findings ------------------------------------------------


def test_find_sarif_files_and_load_findings_across_multiple_files(tmp_path: Path) -> None:
    """A second .sarif file (an extra query suite, a second matrix language) must
    not have its findings silently skipped - iterate all matches, not just one."""
    _write_sarif(
        tmp_path / "python.sarif",
        [{"tool": {"driver": {"rules": [_rule("py/a", "9.0")]}}, "results": [_result("py/a")]}],
    )
    _write_sarif(
        tmp_path / "javascript.sarif",
        [{"tool": {"driver": {"rules": [_rule("js/b", "8.0")]}}, "results": [_result("js/b")]}],
    )
    sarif_paths = gate.find_sarif_files(tmp_path)
    assert len(sarif_paths) == 2
    findings = gate.load_findings(sarif_paths)
    assert {f.rule_id for f in findings} == {"py/a", "js/b"}


def test_find_sarif_files_returns_empty_list_when_none_present(tmp_path: Path) -> None:
    assert gate.find_sarif_files(tmp_path) == []


# --- render_summary -------------------------------------------------------------------


def test_render_summary_lists_every_finding() -> None:
    findings = [
        gate.Finding("py/a", 9.0, "app.py", 10, "bad thing"),
        gate.Finding("py/b", None, "app.py", 20, "another thing"),
    ]
    out = gate.render_summary([Path("python.sarif")], findings)
    assert "2 finding(s)" in out
    assert "`py/a` app.py:10 - bad thing" in out
    assert "`py/b` app.py:20 - another thing" in out


def test_render_summary_zero_findings() -> None:
    out = gate.render_summary([Path("python.sarif")], [])
    assert "0 finding(s)" in out


# --- cmd_summarize --------------------------------------------------------------------


def test_cmd_summarize_writes_and_appends_to_summary_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sarif_dir = tmp_path / "sarif-results"
    sarif_dir.mkdir()
    _write_sarif(
        sarif_dir / "python.sarif",
        [{"tool": {"driver": {"rules": []}}, "results": [_result("py/a", "found it")]}],
    )
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("existing content\n", encoding="utf-8")

    result = gate.cmd_summarize(sarif_dir, summary_path)

    assert result == 0
    content = summary_path.read_text(encoding="utf-8")
    assert "existing content" in content
    assert "found it" in content
    assert "found it" in capsys.readouterr().out


def test_cmd_summarize_fails_when_no_sarif_files_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = gate.cmd_summarize(empty_dir, tmp_path / "summary.md")
    assert result == 1


# --- cmd_check_severity ---------------------------------------------------------------


def test_cmd_check_severity_fails_on_a_high_severity_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sarif(
        tmp_path / "python.sarif",
        [
            {
                "tool": {"driver": {"rules": [_rule("py/sql-injection", "9.8")]}},
                "results": [_result("py/sql-injection", "SQL injection risk")],
            }
        ],
    )
    result = gate.cmd_check_severity(tmp_path, threshold=7.0)
    assert result == 1
    assert "py/sql-injection" in capsys.readouterr().out


def test_cmd_check_severity_passes_when_below_threshold(tmp_path: Path) -> None:
    _write_sarif(
        tmp_path / "python.sarif",
        [{"tool": {"driver": {"rules": [_rule("py/low", "3.2")]}}, "results": [_result("py/low")]}],
    )
    assert gate.cmd_check_severity(tmp_path, threshold=7.0) == 0


def test_cmd_check_severity_passes_when_severity_unscored(tmp_path: Path) -> None:
    _write_sarif(
        tmp_path / "python.sarif",
        [
            {
                "tool": {"driver": {"rules": [_rule("py/unused-import")]}},
                "results": [_result("py/unused-import")],
            }
        ],
    )
    assert gate.cmd_check_severity(tmp_path, threshold=7.0) == 0


def test_cmd_check_severity_fails_when_no_sarif_files_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert gate.cmd_check_severity(empty_dir, threshold=7.0) == 1


def test_cmd_check_severity_at_exact_threshold_fails(tmp_path: Path) -> None:
    """>= threshold, not > - a finding exactly at 7.0 must gate, matching the
    docstring's stated ">= --threshold" contract."""
    _write_sarif(
        tmp_path / "python.sarif",
        [{"tool": {"driver": {"rules": [_rule("py/x", "7.0")]}}, "results": [_result("py/x")]}],
    )
    assert gate.cmd_check_severity(tmp_path, threshold=7.0) == 1


# --- main / CLI -------------------------------------------------------------------


def test_main_summarize_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sarif_dir = tmp_path / "sarif-results"
    sarif_dir.mkdir()
    _write_sarif(sarif_dir / "python.sarif", [])
    summary_path = tmp_path / "summary.md"
    monkeypatch.setattr(
        sys, "argv", ["codeql_gate.py", "summarize", str(sarif_dir), str(summary_path)]
    )
    assert gate.main() == 0
    assert summary_path.exists()


def test_main_check_severity_subcommand_default_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sarif(
        tmp_path / "python.sarif",
        [{"tool": {"driver": {"rules": [_rule("py/a", "9.9")]}}, "results": [_result("py/a")]}],
    )
    monkeypatch.setattr(sys, "argv", ["codeql_gate.py", "check-severity", str(tmp_path)])
    assert gate.main() == 1


def test_main_check_severity_subcommand_custom_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sarif(
        tmp_path / "python.sarif",
        [{"tool": {"driver": {"rules": [_rule("py/a", "5.0")]}}, "results": [_result("py/a")]}],
    )
    monkeypatch.setattr(
        sys, "argv", ["codeql_gate.py", "check-severity", str(tmp_path), "--threshold", "9.0"]
    )
    assert gate.main() == 0
