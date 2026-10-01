"""The canonical OpenAPI document build and its on-disk serialisation.

`app.openapi()` is a pure function of the route table, so this module has no state. It
fixes the two things that would otherwise make "the document" ambiguous between callers:

  * which `ApiSettings` produced it - `frontend_base_url` only affects the CORS
    middleware, never a field in the document, so a fixed placeholder keeps generation
    independent of the machine it runs on. The settings come from `model_construct`, which
    reads no `NPTC_*` variable and runs no validator, so an exported variable cannot
    break generation; and
  * the exact committed bytes of `docs/api/openapi.json` - `indent=2, ensure_ascii=False`
    plus a single trailing newline, so `scripts/generate_openapi.py`, the drift test in
    `backend/tests/test_openapi_document.py` and the frontend's `generate:api` all read
    the same file the same way.
"""

from __future__ import annotations

import json
from typing import Any

from nptc.api.app import create_app
from nptc.settings import ApiSettings

#: Not a real deployment target: `create_app` requires some origin for CORS, and it
#: never appears in the document. Public so that
#: `test_served_document_matches_the_committed_document` builds its own app from this
#: constant rather than a second copy of the literal.
GENERATION_FRONTEND_BASE_URL = "http://localhost:5173"


def build_document() -> dict[str, Any]:
    """The OpenAPI document `create_app()` serves, as a plain JSON-able dict."""
    settings = ApiSettings.model_construct(frontend_base_url=GENERATION_FRONTEND_BASE_URL)
    app = create_app(settings=settings)
    return dict(app.openapi())


def render(document: dict[str, Any]) -> str:
    """The exact text committed to `docs/api/openapi.json`: 2-space indent, no ASCII
    escaping of non-ASCII characters, and a single trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
