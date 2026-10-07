"""The canonical OpenAPI document build and its on-disk serialisation.

`app.openapi()` is a pure function of the route table, so this module has no state. It
fixes two things that would otherwise make "the document" ambiguous between callers:

  * which settings produced it - `ApiSettings` and `AuthSettings` are built with
    `model_construct`, which reads no `NPTC_*` variable and runs no validator, and the
    terminology client is a stub, so no environment variable can break generation; and
  * the exact committed bytes of `docs/api/openapi.json` - `indent=2, ensure_ascii=False`
    plus a single trailing newline, so every reader gets the same file.
"""

from __future__ import annotations

import json
from typing import Any

from nptc.api.app import create_app
from nptc.settings import ApiSettings, AuthSettings
from nptc_shared.terminology import StubTerminologyClient

#: Not a real deployment target: `create_app` requires an origin for CORS, and it never
#: appears in the document. Public so tests build their apps from this constant.
#: `model_construct` skips the origin validator, so this must be a bare origin.
GENERATION_FRONTEND_BASE_URL = "http://localhost:5173"


def build_document() -> dict[str, Any]:
    """The OpenAPI document `create_app()` serves, as a plain JSON-able dict."""
    settings = ApiSettings.model_construct(frontend_base_url=GENERATION_FRONTEND_BASE_URL)
    app = create_app(
        settings=settings,
        auth_settings=AuthSettings.model_construct(),
        terminology_client=StubTerminologyClient(),
    )
    return dict(app.openapi())


def render(document: dict[str, Any]) -> str:
    """The exact text committed to `docs/api/openapi.json`: 2-space indent, no ASCII
    escaping of non-ASCII characters, and a single trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
