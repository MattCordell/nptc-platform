#!/usr/bin/env python3
"""Documentation-impact PR-body gate (issue #87 review).

Enforces CONTRIBUTING.md's "Documentation is part of the change" contract for a PR
that changes anything outside docs/** and *.md: it must either update documentation
in the diff (checked separately, in the workflow, via `git diff`), or this body must
declare a real reason via one of two lines:

  no-doc-impact: <reason>
  Follow-up issue #N <separator> <reason>

Both patterns anchor to the start of a line (optionally after a `- [ ]`/`- [x]`
checkbox and/or backticks), rather than searching for the token anywhere in the
body. That anchoring matters: the committed PR_TEMPLATE.md's own instructional
comment ("...with no docs change and no `no-doc-impact:` line.") contains the
literal string mid-sentence - an unanchored `re.search` finds that occurrence
first and reads "line." as a genuine declared reason, letting the unmodified
template pass the gate it exists to enforce. Anchoring to line-start rejects
that: the sentence doesn't start with the token, only the real answer line does.

Usage:
  uv run python scripts/doc_impact_gate.py <path-to-a-file-holding-the-pr-body>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `[^\S\n]*` (not `\s*`) between tokens: matches horizontal whitespace only, never
# a newline - `\s*` there is exactly the bug that let an empty/unticked box
# swallow the next body line as its "reason" in an earlier version of this gate.
NO_IMPACT_PATTERN = re.compile(
    r"^[^\S\n]*(?:-[^\S\n]*\[[ xX]\][^\S\n]*)?`?no-doc-impact:`?[^\S\n]*(?P<reason>[^\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)

# Deliberately not line-anchored: CONTRIBUTING.md documents this option only as
# "link a follow-up issue and say why", not fixed phrasing, so a compliant body
# describing it in a sentence (e.g. "Linked follow-up issue #12 - configuration.md
# will be written there.") must still be recognised. The template's own unfilled
# placeholder ("Follow-up issue #___ because:") never matches - `#___` isn't
# `#\d+` - so this can't be fooled by the boilerplate the way NO_IMPACT_PATTERN was.
FOLLOWUP_PATTERN = re.compile(
    r"follow-?up[^\n]*#\d+[^\n]*?(?:[-:]\s*|because:?\s*)(?P<reason>[^\n]*)",
    re.IGNORECASE,
)


def _clean_reason(raw: str) -> str | None:
    """Strip HTML comments and backticks from a captured reason; reject it if
    what survives is empty or still carries a stray, unterminated comment
    delimiter (a placeholder comment wrapped onto a later line leaves one behind,
    which would otherwise read as a truthy, non-empty "reason")."""
    reason = re.sub(r"<!--.*?-->", "", raw)
    reason = reason.replace("`", "").strip()
    if not reason or "<!--" in reason or "-->" in reason:
        return None
    return reason


def declared_reason(body: str) -> str | None:
    """The reason a PR body declares for its documentation impact, checking the
    no-doc-impact line first and the follow-up-issue line second - or None if
    neither carries a real one."""
    for pattern in (NO_IMPACT_PATTERN, FOLLOWUP_PATTERN):
        match = pattern.search(body)
        if match:
            reason = _clean_reason(match.group("reason"))
            if reason:
                return reason
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: doc_impact_gate.py <path-to-pr-body-file>", file=sys.stderr)
        return 2

    body = Path(sys.argv[1]).read_text(encoding="utf-8")
    reason = declared_reason(body)

    if reason:
        print(f"Documentation impact declared: {reason}")
        return 0

    print("::error::This PR changes source outside docs/**/*.md with no docs update, and")
    print("no 'no-doc-impact: <reason>' or 'Follow-up issue #N ...' line carries a real")
    print("reason (not just the template's own instructional text). See the")
    print("documentation-impact table in CONTRIBUTING.md.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
