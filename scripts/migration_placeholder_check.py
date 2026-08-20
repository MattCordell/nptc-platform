#!/usr/bin/env python3
"""Pre-commit gate: fail if a committed migration still carries
`backend/migrations/script.py.mako`'s unfilled `<FILL IN` placeholder (issue #191).

The mako template's docstring skeleton prompts every new migration's author for the
issue/FR reference and the design rationale, so the convention in CONTRIBUTING.md's
"A schema change's prose has one home each" is the default rather than something an
author has to remember. Nothing else stopped an unedited placeholder from landing on
`main` looking authored - this script is that stop.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLACEHOLDER = "<FILL IN"


def files_with_placeholder(paths: list[str]) -> list[str]:
    return [path for path in paths if PLACEHOLDER in Path(path).read_text(encoding="utf-8")]


def main() -> int:
    hits = files_with_placeholder(sys.argv[1:])
    if not hits:
        return 0

    print("::error::Unfilled script.py.mako placeholder ('<FILL IN') in:", file=sys.stderr)
    for path in hits:
        print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
