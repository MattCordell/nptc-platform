"""`docs/api/openapi.json` is the API's committed contract (issue #41).

CONTRIBUTING.md's documentation-impact table routes "an API endpoint or
schema" to this file. A committed document nobody checks drifts within two
PRs, so this asserts it still matches what `create_app()` actually serves -
and the failure message says exactly how to regenerate it.

No container and no network: `app.openapi()` is a pure function of the route
table.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nptc.api.app import create_app
from nptc.settings import ApiSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "docs" / "api" / "openapi.json"

REGENERATE = (
    "Regenerate it with:\n"
    '  uv run python -c "import json,pathlib;'
    "from nptc.api.app import create_app;"
    "from nptc.settings import ApiSettings;"
    "pathlib.Path('docs/api/openapi.json').write_text("
    "json.dumps(create_app(settings=ApiSettings("
    "frontend_base_url='http://localhost:5173')).openapi(), indent=2, "
    "ensure_ascii=False) + chr(10), encoding='utf-8', newline=chr(10))\""
)


def _current_spec() -> dict[str, object]:
    app = create_app(settings=ApiSettings(frontend_base_url="http://localhost:5173"))
    return dict(app.openapi())


@pytest.mark.req("NFR-01")
def test_committed_openapi_document_matches_the_app() -> None:
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert committed == _current_spec(), (
        f"docs/api/openapi.json is out of date with nptc.api.app.\n{REGENERATE}"
    )


@pytest.mark.req("NFR-04")
def test_the_session_response_never_exposes_an_internal_id() -> None:
    """The schema, not just one response body: a field added to `UserRef`
    later would be caught here even if no test happened to call the
    endpoint with a resolved user."""
    spec = _current_spec()
    schemas = spec["components"]["schemas"]  # type: ignore[index]

    assert "id" not in schemas["UserRef"]["properties"]  # type: ignore[index]
    assert "user_id" not in schemas["SessionResponse"]["properties"]  # type: ignore[index]
