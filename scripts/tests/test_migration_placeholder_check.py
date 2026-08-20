"""Unit tests for scripts/migration_placeholder_check.py (issue #191).

The regression this file exists to prevent: a migration generated from
backend/migrations/script.py.mako and committed without filling in its
docstring placeholder must fail the pre-commit hook, not merge silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import migration_placeholder_check as check

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = ROOT / "backend" / "migrations" / "script.py.mako"
REAL_MIGRATION = ROOT / "backend" / "migrations" / "versions" / "0007_designation.py"


def test_the_template_itself_carries_the_placeholder() -> None:
    """Sanity check that the placeholder text still matches the template - if a
    future template edit changes the wording, this test (not a silently-passing
    hook) should be the one to notice."""
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert check.PLACEHOLDER in body


def test_an_unfilled_generated_migration_fails(tmp_path: Path) -> None:
    generated = tmp_path / "0008_scratch.py"
    generated.write_text(
        '"""scratch\n\nIssue #NNN (FR-nn): <FILL IN - which issue and FR/NFR this migration lands>.\n"""\n',
        encoding="utf-8",
    )
    assert check.files_with_placeholder([str(generated)]) == [str(generated)]


def test_a_real_migration_passes() -> None:
    assert check.files_with_placeholder([str(REAL_MIGRATION)]) == []


def test_main_exits_nonzero_on_a_hit(tmp_path: Path) -> None:
    generated = tmp_path / "0008_scratch.py"
    generated.write_text('"""<FILL IN>"""\n', encoding="utf-8")
    sys.argv = ["migration_placeholder_check.py", str(generated)]
    assert check.main() == 1


def test_main_exits_zero_with_no_hits() -> None:
    sys.argv = ["migration_placeholder_check.py", str(REAL_MIGRATION)]
    assert check.main() == 0
