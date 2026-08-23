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
from typing import Any

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


#: Field names that would be an internal identifier reaching a client
#: (PRD SS6.2, NFR-04). `id` and `entry_id` are the two columns the public
#: read layer actually handles; `*_binding_id` is checked by suffix because
#: `code_binding.replaced_by_binding_id` is the one this API resolves away
#: and a sibling column added later would be the same mistake.
_FORBIDDEN_PROPERTY_NAMES = frozenset({"id", "entry_id"})
_FORBIDDEN_PROPERTY_SUFFIX = "_binding_id"

#: Every component schema reachable from the public catalogue routes. Taken
#: from the document, not hand-listed: a response model added to
#: `routers/catalogue.py` is covered here on the day it is added.
_CATALOGUE_SCHEMA_NAMES = (
    "EntrySummary",
    "EntryDetail",
    "EntryPage",
    "Designation",
    "DesignationList",
    "Binding",
    "BindingList",
    "PropertyValue",
    "PropertyList",
    "SearchHit",
    "SearchPage",
)


@pytest.mark.req("NFR-04")
def test_no_public_catalogue_schema_exposes_an_internal_identifier() -> None:
    """The schema, not a response body - the same reasoning as
    `test_the_session_response_never_exposes_an_internal_id` above. A field
    added to one of these models later is caught here even if no test
    happens to request an entry that would populate it, and unlike the
    whole-body scan in `test_api_public_response_hygiene.py` this needs no
    container and no seeded row to notice.
    """
    spec = _current_spec()
    schemas: dict[str, Any] = spec["components"]["schemas"]  # type: ignore[index,assignment]

    missing = [name for name in _CATALOGUE_SCHEMA_NAMES if name not in schemas]
    assert not missing, (
        f"expected public catalogue schema(s) {missing} in the document - if a model was "
        "renamed, update _CATALOGUE_SCHEMA_NAMES rather than dropping the assertion"
    )

    offenders: list[str] = []
    for name in _CATALOGUE_SCHEMA_NAMES:
        for property_name in schemas[name].get("properties", {}):
            if property_name in _FORBIDDEN_PROPERTY_NAMES or property_name.endswith(
                _FORBIDDEN_PROPERTY_SUFFIX
            ):
                offenders.append(f"{name}.{property_name}")

    assert not offenders, f"internal identifier(s) exposed in the public API schema: {offenders}"
