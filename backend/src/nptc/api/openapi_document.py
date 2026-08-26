"""The canonical OpenAPI document build and its on-disk serialisation (issue #143).

`app.openapi()` is a pure function of the route table (FastAPI builds and caches it), so
this module has no state of its own: it just fixes the two things that would otherwise
make "the document" ambiguous between callers -

  * which `ApiSettings` produced it - `frontend_base_url` only affects the CORS
    middleware (see `create_app`), never a field in the document itself, so a fixed
    placeholder value keeps generation independent of the machine it runs on; and
  * the exact bytes committed to `docs/api/openapi.json` - `indent=2, ensure_ascii=False`
    plus a single trailing newline, so `scripts/generate_openapi.py`, the drift test in
    `backend/tests/test_openapi_document.py`, and (eventually) issue #147's TypeScript
    client generation all read the same file the same way.
"""

from __future__ import annotations

import json
from typing import Any

from nptc.api.app import create_app
from nptc.settings import ApiSettings

#: Not a real deployment target - `create_app` requires *some* origin to configure
#: CORS, and that origin never appears in the OpenAPI document itself. Fixed here so
#: `build_document()` gives the same result regardless of the caller's environment.
_GENERATION_FRONTEND_BASE_URL = "http://localhost:5173"


def build_document() -> dict[str, Any]:
    """The OpenAPI document `create_app()` serves, as a plain JSON-able dict."""
    app = create_app(settings=ApiSettings(frontend_base_url=_GENERATION_FRONTEND_BASE_URL))
    return dict(app.openapi())


def render(document: dict[str, Any]) -> str:
    """The exact text committed to `docs/api/openapi.json`: 2-space indent, no ASCII
    escaping of non-ASCII characters, and a single trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
