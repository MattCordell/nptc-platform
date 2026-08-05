"""Unit tests for scripts/doc_impact_gate.py (issue #87 review).

The regression this file exists to prevent: the committed PR template must always
fail this gate, checked directly against the real file rather than a copy - a
future template edit that reintroduces the literal token "no-doc-impact:" in prose
must be caught here, not discovered by a PR silently sailing through review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import doc_impact_gate as gate

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"


# --- the regression itself -----------------------------------------------------------


def test_the_committed_template_fails_the_gate() -> None:
    """The exact bug this module was extracted to fix: an unanchored re.search
    for 'no-doc-impact:' found the token inside the template's own instructional
    comment ("...with no docs change and no `no-doc-impact:` line.") and read the
    word after the colon as a genuine declared reason."""
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert gate.declared_reason(body) is None


def test_template_with_no_impact_box_ticked_and_filled_passes() -> None:
    body = TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "- [ ] `no-doc-impact:`", "- [x] `no-doc-impact:` internal refactor, no behaviour change"
    )
    assert gate.declared_reason(body) == "internal refactor, no behaviour change"


def test_template_with_followup_box_ticked_and_filled_passes() -> None:
    body = TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "- [ ] Follow-up issue #___ because:",
        "- [x] Follow-up issue #142 because: blocked on the schema landing first",
    )
    assert gate.declared_reason(body) == "blocked on the schema landing first"


# --- no-doc-impact: bare line, ticked, and the reason-swallowing regressions -------


def test_bare_line_with_no_checkbox_passes() -> None:
    """CONTRIBUTING.md's documented contract is a bare line, not specifically a
    ticked markdown checkbox - a body written without the PR template's
    scaffolding (e.g. via `gh pr create --body`) must still pass."""
    assert gate.declared_reason("no-doc-impact: quick fix, no behaviour change") == (
        "quick fix, no behaviour change"
    )


def test_ticked_with_backticks_passes() -> None:
    body = "- [x] `no-doc-impact:` internal refactor"
    assert gate.declared_reason(body) == "internal refactor"


def test_ticked_box_with_only_comment_placeholder_fails() -> None:
    body = '- [x] `no-doc-impact:` <!-- reason, e.g. "internal refactor" -->'
    assert gate.declared_reason(body) is None


def test_empty_unticked_box_does_not_swallow_the_next_line() -> None:
    """\\s* before the reason capture used to match across newlines, so an empty/
    unticked box swallowed the next body line as its "reason"."""
    body = "- [ ] `no-doc-impact:`\n- [ ] Follow-up issue #___ because:\n"
    assert gate.declared_reason(body) is None


def test_multiline_wrapped_comment_placeholder_fails() -> None:
    """A placeholder HTML comment wrapped onto a second line leaves a stray
    "<!--" behind after single-line-anchored stripping - that must not read as a
    truthy, non-empty reason."""
    body = '- [ ] `no-doc-impact:` <!--\n  reason, e.g. "internal refactor"\n-->\n'
    assert gate.declared_reason(body) is None


def test_prose_mentioning_no_doc_impact_mid_sentence_is_not_mistaken_for_an_answer() -> None:
    """The general form of the template bug: the token appearing anywhere in the
    body, not at the start of a line, must never be read as the PR author's
    answer - anchoring to line-start is what fixes this class of false positive."""
    body = (
        "## Documentation\n\n"
        "This PR changes docs/**, so there is no `no-doc-impact:` line needed here.\n"
    )
    assert gate.declared_reason(body) is None


# --- follow-up: loosened phrasing --------------------------------------------------


def test_followup_loosened_phrasing_in_free_prose_passes() -> None:
    """Requiring the template's exact 'Follow-up issue #N because:' phrasing
    failed a compliant body describing it differently - CONTRIBUTING.md documents
    this option only as "link a follow-up issue and say why"."""
    body = "Linked follow-up issue #12 - configuration.md will be written there."
    assert gate.declared_reason(body) == "configuration.md will be written there."


def test_followup_with_placeholder_number_never_matches() -> None:
    """The template's own unfilled placeholder (#___) must never match #\\d+."""
    assert gate.declared_reason("- [ ] Follow-up issue #___ because:") is None


# --- CLI -----------------------------------------------------------------------------


def test_main_passes_with_a_real_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("no-doc-impact: internal fix", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["doc_impact_gate.py", str(body_file)])
    assert gate.main() == 0


def test_main_fails_with_no_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("nothing relevant here", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["doc_impact_gate.py", str(body_file)])
    assert gate.main() == 1


def test_main_requires_exactly_one_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["doc_impact_gate.py"])
    assert gate.main() == 2
