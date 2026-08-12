"""Tests for the FR-72 required-action registry (``actions.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nptc_transform.actions import ACTION_BY_CODE, action_for
from nptc_transform.bands import Band, FindingCode, band_for

_RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "operations" / "runbooks" / "transform.md"


@pytest.mark.req("FR-72")
def test_registry_has_an_action_for_every_finding_code() -> None:
    """The one guarantee this module exists to provide - mirrors
    ``test_bands.test_registry_classifies_every_finding_code``. Enforced at
    import time in ``actions.py`` too; this test keeps that invariant visible."""
    assert set(ACTION_BY_CODE) == set(FindingCode)


@pytest.mark.req("FR-72")
def test_every_registered_action_is_non_empty() -> None:
    for code in FindingCode:
        assert action_for(code).strip()


@pytest.mark.req("FR-72")
def test_an_unregistered_code_falls_back_to_its_bands_text_not_a_blank_line() -> None:
    """The fallback FR-72's own failure mode guards against: an unregistered
    code already fails safe to ``Band.DATA_DEFECT`` (``bands.band_for``), so
    the reader must still get "fix at source, blocked" - never an empty
    action line under a heading."""
    action = action_for("NOT_A_REAL_CODE")
    assert action.strip()
    assert band_for("NOT_A_REAL_CODE") is Band.DATA_DEFECT
    assert "blocked" in action


@pytest.mark.parametrize(
    ("code", "expect_blocked"),
    [
        (FindingCode.INVISIBLE_CHARACTER, False),
        (FindingCode.WHITESPACE_ONLY_CELL, True),
        (FindingCode.CODE_NOT_FOUND, True),
        (FindingCode.LABEL_DESIGNATION_DRIFT, False),
        (FindingCode.PROBABLE_MISSPELLING, False),
    ],
)
@pytest.mark.req("FR-72")
def test_action_text_states_whether_the_import_is_blocked(
    code: FindingCode, expect_blocked: bool
) -> None:
    """House style: a blocking band's action says the import is blocked; a
    non-blocking band's says explicitly there is nothing to do, out loud -
    an editor scanning 200 findings needs to know what to skip as much as
    what to fix."""
    action = action_for(code)
    if expect_blocked:
        assert "blocked" in action
    else:
        assert "No action required" in action
        assert "not blocked" in action


@pytest.mark.req("FR-72")
def test_ascii_only() -> None:
    """NFR-38: this prose is copy-pasted from Markdown into other tools, so
    no smart quotes, em-dashes or non-breaking spaces are allowed to sneak
    in via a careless edit."""
    for code, action in ACTION_BY_CODE.items():
        assert action.isascii(), f"{code}: {action!r} contains a non-ASCII character"


@pytest.mark.req("FR-72")
def test_every_action_matches_the_runbooks_table() -> None:
    """The runbook's ``| Defect class | Band | Required action |`` table
    (``docs/operations/runbooks/transform.md``, "The report files (FR-72)")
    is a second, hand-maintained copy of this registry's prose - a doc/code
    drift risk this repo treats as worth guarding, not shrugging off as a
    brittle test. Fails loudly, naming the code, if a future edit changes
    one copy without the other."""
    runbook_text = _RUNBOOK.read_text(encoding="utf-8")
    for code, action in ACTION_BY_CODE.items():
        assert action in runbook_text, f"{code}'s action text is stale in the runbook"
