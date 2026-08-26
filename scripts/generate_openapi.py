#!/usr/bin/env python3
"""Regenerate `docs/api/openapi.json` from the FastAPI route table (issue #143, FR-20).

External consumers depend on this document, and `nptc.api.openapi_document.build_document`
is a pure function of `nptc.api.app.create_app`'s route table - so the committed copy is
either exactly what the app would serve, or it is stale. `--check` is the CI gate
(`.github/workflows/openapi.yml`) and the pre-commit hook; without it, this writes the
regenerated document in place, which is what a contributor runs after changing a route
or a response model.

Usage:
  uv run python scripts/generate_openapi.py            # write docs/api/openapi.json
  uv run python scripts/generate_openapi.py --check     # exit 1 if it would change
"""

from __future__ import annotations

import sys
from pathlib import Path

from nptc.api.openapi_document import build_document, render

ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = ROOT / "docs" / "api" / "openapi.json"

REGENERATE_COMMAND = "uv run python scripts/generate_openapi.py"


def rendered_document() -> str:
    return render(build_document())


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    current = rendered_document()

    if check_only:
        if not OPENAPI_PATH.exists():
            print(f"generate_openapi: {OPENAPI_PATH} does not exist.", file=sys.stderr)
            print(f"Generate it with:\n  {REGENERATE_COMMAND}", file=sys.stderr)
            return 1

        committed = OPENAPI_PATH.read_text(encoding="utf-8")
        if committed != current:
            print(
                f"generate_openapi: {OPENAPI_PATH} is out of date with nptc.api.app.",
                file=sys.stderr,
            )
            print(f"Regenerate it with:\n  {REGENERATE_COMMAND}", file=sys.stderr)
            return 1

        print("generate_openapi: docs/api/openapi.json is up to date.")
        return 0

    OPENAPI_PATH.write_text(current, encoding="utf-8", newline="\n")
    print(f"generate_openapi: wrote {OPENAPI_PATH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
