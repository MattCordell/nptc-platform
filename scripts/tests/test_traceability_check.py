"""Unit tests for scripts/traceability_check.py (Foundation issue F-5).

Exercises the checker's logic against synthetic fixtures rather than the real
requirements.yaml, so these tests do not need updating every time a
requirement is added, retitled or its status changes.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import traceability_check as tc


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every module-level path constant at a scratch directory."""
    requirements_file = tmp_path / "requirements.yaml"
    test_dir = tmp_path / "tests"
    test_dir.mkdir()

    monkeypatch.setattr(tc, "REQUIREMENTS_FILE", requirements_file)
    monkeypatch.setattr(tc, "TRACEABILITY_REPORT", tmp_path / "traceability.md")
    monkeypatch.setattr(tc, "TEST_DIRS", [test_dir])


def _write_requirements(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_load_requirements_parses_valid_yaml() -> None:
    _write_requirements(
        tc.REQUIREMENTS_FILE,
        """\
        - id: "FR-01"
          priority: MUST
          phase: p1
          title: "Example requirement"
          status: planned
          notes: ""
        """,
    )
    requirements, errors = tc.load_requirements()
    assert errors == []
    assert requirements["FR-01"].priority == "MUST"


@pytest.mark.parametrize(
    ("field", "value"),
    [("priority", "SHOULD-ISH"), ("phase", "p9"), ("status", "done")],
)
def test_load_requirements_flags_invalid_enum_value(field: str, value: str) -> None:
    entry = {"priority": "MUST", "phase": "p1", "status": "planned"}
    entry[field] = value
    body = '- id: "FR-01"\n' + "\n".join(f"  {k}: {v}" for k, v in entry.items()) + "\n  title: t\n"
    _write_requirements(tc.REQUIREMENTS_FILE, body)
    _, errors = tc.load_requirements()
    assert any(field in error for error in errors)


def test_load_requirements_flags_duplicate_id() -> None:
    _write_requirements(
        tc.REQUIREMENTS_FILE,
        """\
        - id: "FR-01"
          priority: MUST
          phase: p1
          title: "First"
          status: planned
          notes: ""
        - id: "FR-01"
          priority: MUST
          phase: p1
          title: "Duplicate"
          status: planned
          notes: ""
        """,
    )
    _, errors = tc.load_requirements()
    assert any("duplicate" in error for error in errors)


def test_collect_test_markers_finds_decorator_form() -> None:
    (tc.TEST_DIRS[0] / "test_example.py").write_text(
        textwrap.dedent(
            """\
            import pytest

            @pytest.mark.req("FR-01")
            def test_thing() -> None:
                assert True
            """
        ),
        encoding="utf-8",
    )
    refs = tc.collect_test_markers()
    assert "FR-01" in refs
    assert refs["FR-01"][0].endswith("test_example.py:3")


def test_collect_test_markers_finds_module_level_pytestmark() -> None:
    (tc.TEST_DIRS[0] / "test_example.py").write_text(
        'import pytest\n\npytestmark = pytest.mark.req("NFR-08")\n',
        encoding="utf-8",
    )
    refs = tc.collect_test_markers()
    assert "NFR-08" in refs


def test_run_checks_flags_unknown_id_in_test_marker() -> None:
    requirements = {
        "FR-01": tc.Requirement("FR-01", "MUST", "p1", "t", "planned", ""),
    }
    errors = tc.run_checks(requirements, {"FR-99": ["a.py:1"]})
    assert any("FR-99" in error and "not in requirements.yaml" in error for error in errors)


def test_run_checks_flags_implemented_without_test() -> None:
    requirements = {
        "FR-01": tc.Requirement("FR-01", "MUST", "p1", "t", "implemented", ""),
    }
    errors = tc.run_checks(requirements, {})
    assert any("FR-01" in error and "no test carries" in error for error in errors)


def test_run_checks_passes_a_fully_covered_requirement() -> None:
    requirements = {
        "FR-01": tc.Requirement("FR-01", "MUST", "p1", "t", "implemented", ""),
    }
    errors = tc.run_checks(requirements, {"FR-01": ["backend/tests/test_x.py:5"]})
    assert errors == []


def test_run_checks_passes_implemented_via_evidence_path(tmp_path: Path) -> None:
    evidence_file = tmp_path / "ci.yml"
    evidence_file.write_text("", encoding="utf-8")
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37", "MUST", "foundation", "t", "implemented", "", evidence="ci.yml"
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {})
    assert errors == []


def test_run_checks_flags_implemented_with_neither_test_nor_evidence() -> None:
    requirements = {
        "FR-01": tc.Requirement("FR-01", "MUST", "p1", "t", "implemented", ""),
    }
    errors = tc.run_checks(requirements, {})
    assert any(
        "FR-01" in error and "no test carries" in error and "no evidence" in error
        for error in errors
    )


def test_run_checks_flags_evidence_path_that_does_not_exist() -> None:
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37",
            "MUST",
            "foundation",
            "t",
            "implemented",
            "",
            evidence="does/not/exist.yml",
        ),
    }
    errors = tc.run_checks(requirements, {})
    assert any("NFR-37" in error and "does not exist" in error for error in errors)


def test_run_checks_resolves_evidence_fragment_present_in_the_file(tmp_path: Path) -> None:
    evidence_file = tmp_path / "ci.yml"
    evidence_file.write_text("jobs:\n  transform-offline:\n    steps: []\n", encoding="utf-8")
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37",
            "MUST",
            "foundation",
            "t",
            "implemented",
            "",
            evidence="ci.yml#transform-offline",
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {})
    assert errors == []


def test_run_checks_flags_evidence_fragment_missing_from_the_file(tmp_path: Path) -> None:
    evidence_file = tmp_path / "ci.yml"
    evidence_file.write_text("jobs:\n  renamed-job:\n    steps: []\n", encoding="utf-8")
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37",
            "MUST",
            "foundation",
            "t",
            "implemented",
            "",
            evidence="ci.yml#transform-offline",
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {})
    assert any(
        "NFR-37" in error and "transform-offline" in error and "not found" in error
        for error in errors
    )


def test_run_checks_flags_evidence_path_with_nothing_before_the_fragment() -> None:
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37", "MUST", "foundation", "t", "implemented", "", evidence="#transform-offline"
        ),
    }
    errors = tc.run_checks(requirements, {})
    assert any("NFR-37" in error and "no path before the #fragment" in error for error in errors)


def test_run_checks_flags_evidence_path_with_a_backslash() -> None:
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37",
            "MUST",
            "foundation",
            "t",
            "implemented",
            "",
            evidence="docs\\governance\\hazard-log.md",
        ),
    }
    errors = tc.run_checks(requirements, {})
    assert any("NFR-37" in error and "backslash" in error for error in errors)


def test_run_checks_flags_evidence_path_that_escapes_the_repo_root(tmp_path: Path) -> None:
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37", "MUST", "foundation", "t", "implemented", "", evidence="../outside.yml"
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {})
    assert any("NFR-37" in error and "escapes the repository root" in error for error in errors)


def test_run_checks_flags_evidence_path_that_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "somedir").mkdir()
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37", "MUST", "foundation", "t", "implemented", "", evidence="somedir"
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {})
    assert any("NFR-37" in error and "not a file" in error for error in errors)


def test_run_checks_allows_both_a_test_marker_and_an_evidence_path(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("evidence for the other half", encoding="utf-8")
    requirements = {
        "FR-01": tc.Requirement(
            "FR-01", "MUST", "p1", "t", "implemented", "", evidence="README.md"
        ),
    }
    with mock.patch.object(tc, "ROOT", tmp_path):
        errors = tc.run_checks(requirements, {"FR-01": ["backend/tests/test_x.py:5"]})
    assert errors == []


def test_render_report_escapes_a_literal_pipe_in_a_title() -> None:
    requirements = {
        "FR-84": tc.Requirement(
            "FR-84", "MUST", "p1", "Subsumed by 71388002 |Procedure|", "planned", ""
        ),
    }
    report = tc.render_report(requirements, {})
    table_row = next(line for line in report.splitlines() if line.startswith("| FR-84"))
    assert "71388002 \\|Procedure\\|" in table_row
    # 7 columns -> 8 unescaped-pipe delimiters; the title's own pipes must not add more.
    unescaped_pipes = re.findall(r"(?<!\\)\|", table_row)
    assert len(unescaped_pipes) == 8


def test_render_report_includes_the_evidence_column() -> None:
    requirements = {
        "NFR-37": tc.Requirement(
            "NFR-37",
            "MUST",
            "foundation",
            "t",
            "implemented",
            "",
            evidence=".github/workflows/ci.yml#transform-offline",
        ),
    }
    report = tc.render_report(requirements, {})
    assert "| Evidence |" in report
    assert ".github/workflows/ci.yml#transform-offline" in report


def test_render_report_includes_every_requirement_and_sorts_fr_before_nfr() -> None:
    requirements = {
        "NFR-02": tc.Requirement("NFR-02", "MUST", "p1", "Second", "planned", ""),
        "FR-10": tc.Requirement("FR-10", "MUST", "p1", "First", "planned", ""),
    }
    report = tc.render_report(requirements, {})
    assert report.index("FR-10") < report.index("NFR-02")
    assert "Total requirements: 2" in report


def test_render_report_counts_requirements_with_evidence() -> None:
    requirements = {
        "NFR-02": tc.Requirement("NFR-02", "MUST", "p1", "Second", "planned", ""),
        "FR-10": tc.Requirement(
            "FR-10", "MUST", "p1", "First", "implemented", "", evidence="README.md"
        ),
    }
    report = tc.render_report(requirements, {})
    assert "With evidence: 1" in report
