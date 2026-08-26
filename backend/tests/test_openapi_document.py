"""`docs/api/openapi.json` is the API's committed contract (issue #41, #143).

CONTRIBUTING.md's documentation-impact table routes "an API endpoint or
schema" to this file. A committed document nobody checks drifts within two
PRs, so this asserts it still matches what `create_app()` actually serves -
and the failure message says exactly how to regenerate it. Issue #143 adds
three more checks external consumers rely on: the document is a *legal*
OpenAPI 3.1 document (not just JSON FastAPI happened to produce), every
SNOMED CT code field is declared `type: string` at the schema level (FR-06 -
belt-and-braces alongside the whole-body regex scan in
`test_api_public_response_hygiene.py`), and the running app serves exactly
what is committed.

No container and no network: `app.openapi()` is a pure function of the route
table, and `openapi_spec_validator` bundles the OpenAPI meta-schema in the
package rather than fetching it.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from nptc.api.app import create_app
from nptc.api.openapi_document import GENERATION_FRONTEND_BASE_URL, build_document, render
from nptc.settings import ApiSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "docs" / "api" / "openapi.json"

REGENERATE = "Regenerate it with:\n  uv run python scripts/generate_openapi.py"


def _current_spec() -> dict[str, Any]:
    return build_document()


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


@pytest.mark.req("FR-20")
def test_document_validates_against_the_openapi_31_meta_schema() -> None:
    """A generated client can only be built off a document that is itself legal
    OpenAPI 3.1 - not merely JSON that FastAPI happened to produce."""
    validate(_current_spec())


@pytest.mark.req("FR-20")
def test_meta_schema_validation_actually_rejects_an_invalid_document() -> None:
    """Positive control: without this, a validator call that silently no-ops
    (wrong function, wrong version, swallowed exception) would pass forever."""
    with pytest.raises(OpenAPIValidationError):
        validate({"openapi": "3.1.0", "info": {"title": "incomplete"}, "paths": {}})


#: A property or parameter named exactly "code", or ending "_code" -
#: `Binding.code` and `Binding.replaced_by_code` today, and whatever a future
#: SNOMED-carrying field or path/query parameter is called tomorrow. Matched
#: by name in the document itself, not hand-listed, so a new one is covered
#: the day it is added.
_CODE_PROPERTY_PATTERN = re.compile(r"(?:^code$)|(?:_code$)")


def _is_string_or_nullable_string(schema: dict[str, Any]) -> bool:
    declared_type = schema.get("type")
    if declared_type == "string":
        return True
    # Legal OpenAPI 3.1 (JSON Schema 2020-12) also allows a nullable string as
    # a `type` array - Pydantic v2 emits `anyOf` today, but a schema written
    # or generated the other legal way must not silently read as "not a
    # string".
    if isinstance(declared_type, list):
        return set(declared_type) <= {"string", "null"} and "string" in declared_type

    branches = schema.get("anyOf")
    if not branches:
        return False
    types = {branch.get("type") for branch in branches}
    return "string" in types and types <= {"string", "null"}


def _code_schema_properties(schemas: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """`(label, schema)` for every component schema property matching
    `_CODE_PROPERTY_PATTERN` - e.g. `Binding.code`."""
    for schema_name, schema in schemas.items():
        for property_name, property_schema in schema.get("properties", {}).items():
            if _CODE_PROPERTY_PATTERN.search(property_name):
                yield f"{schema_name}.{property_name}", property_schema


def _code_operation_parameters(paths: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """`(label, schema)` for every operation parameter matching
    `_CODE_PROPERTY_PATTERN` - e.g. a hypothetical `GET
    /catalogue/bindings/{code}`. A parameter is inline `{name, schema}` on
    every route in this API (no `$ref`), which is what makes reading
    `parameter["schema"]` directly correct here."""
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method == "parameters" or not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                name = parameter.get("name", "")
                if _CODE_PROPERTY_PATTERN.search(name):
                    yield f"{method.upper()} {path} :: {name}", parameter.get("schema", {})


@pytest.mark.req("FR-06")
def test_every_snomed_code_field_is_declared_a_json_string() -> None:
    """FR-06: a SNOMED CT identifier must never be serialisable as a JSON
    number - the schema-level counterpart to the response-body regex scan in
    `test_api_public_response_hygiene.py`. This one catches a field or
    parameter that is declared but never populated/exercised in any test
    fixture, too."""
    spec = _current_spec()
    schemas: dict[str, Any] = spec["components"]["schemas"]  # type: ignore[index,assignment]
    paths: dict[str, Any] = spec["paths"]  # type: ignore[assignment]

    checked = 0
    offenders: list[str] = []
    for label, schema in itertools.chain(
        _code_schema_properties(schemas), _code_operation_parameters(paths)
    ):
        checked += 1
        if not _is_string_or_nullable_string(schema):
            offenders.append(label)

    # A positive control for the pattern itself: if this hits zero, the
    # regex (or the schema it's matched against) has drifted and the
    # assertion below would be vacuously true.
    assert checked > 0, "expected at least one *_code property or parameter in the document"
    assert not offenders, f"SNOMED code field(s) not typed as a JSON string (FR-06): {offenders}"


@pytest.mark.req("FR-20")
def test_served_document_matches_the_committed_document() -> None:
    """The served copy and the committed copy must be the same build - not
    merely equal as parsed JSON, but byte-identical once rendered the same way
    (issue #143's acceptance criterion 4)."""
    app = create_app(settings=ApiSettings(frontend_base_url=GENERATION_FRONTEND_BASE_URL))
    with TestClient(app) as client:
        served = client.get("/api/v1/openapi.json").json()

    committed_text = OPENAPI_PATH.read_text(encoding="utf-8")
    assert served == json.loads(committed_text)
    assert render(served) == committed_text
