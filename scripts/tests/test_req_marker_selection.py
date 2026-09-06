"""Unit tests for the repo root `conftest.py`'s `--req=ID` option (issue
#165 review): CLAUDE.md's documented `uv run pytest -m "req('FR-07')"`
does not work (pytest's `-m` grammar has no call syntax - see that
conftest's own docstring), so `--req` is the real selection mechanism.
Exercised here via `pytester`, a real (isolated, subprocess) pytest run
against synthetic tests, rather than against this repo's own suite - so
these tests do not need updating every time a requirement or test is
added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SYNTHETIC_TESTS = """
import pytest

@pytest.mark.req("FR-01")
def test_fr01():
    pass

@pytest.mark.req("FR-02")
def test_fr02():
    pass

@pytest.mark.req("FR-01")
@pytest.mark.req("FR-02")
def test_both():
    pass

def test_unmarked():
    pass
"""

_ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"


@pytest.fixture
def req_option_project(pytester: pytest.Pytester) -> pytest.Pytester:
    """A throwaway pytest project carrying this repo's own `req` marker
    registration and root `conftest.py` hooks, plus the four synthetic
    tests above."""
    pytester.makeini(
        """
        [pytest]
        markers =
            req(id): the FR-nn/NFR-nn requirement ID this test verifies
        """
    )
    pytester.makeconftest(_ROOT_CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(test_synthetic=_SYNTHETIC_TESTS)
    return pytester


def test_req_option_selects_only_the_matching_id(req_option_project: pytest.Pytester) -> None:
    result = req_option_project.runpytest("--req=FR-01", "-v")

    result.assert_outcomes(passed=2, deselected=2)
    result.stdout.fnmatch_lines(["*test_fr01*", "*test_both*"])


def test_req_option_matches_a_test_carrying_more_than_one_id(
    req_option_project: pytest.Pytester,
) -> None:
    result = req_option_project.runpytest("--req=FR-02")

    result.assert_outcomes(passed=2, deselected=2)


def test_no_req_option_runs_everything(req_option_project: pytest.Pytester) -> None:
    result = req_option_project.runpytest()

    result.assert_outcomes(passed=4)


def test_req_option_with_no_matching_test_selects_nothing(
    req_option_project: pytest.Pytester,
) -> None:
    result = req_option_project.runpytest("--req=NFR-99")

    result.assert_outcomes(deselected=4)
