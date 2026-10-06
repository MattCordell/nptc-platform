"""The terms refusals as the OpenAPI document declares them (NFR-45, ADR-0043).

The gate is an app-level dependency, which cannot declare a response, so
`nptc.api.terms_gate.declare_terms_refusal` adds the 403 body to the document. This keeps the
generated client typed for it.

No database: the document is a pure function of the route table.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nptc.api.openapi_document import build_document
from nptc.api.prefix import API_PREFIX
from nptc.api.terms_gate import EXEMPT_ROUTES

_REF = "#/components/schemas/TermsAcceptanceRequiredResponse"
_SAFE = {"get", "head", "options"}


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return build_document()


def _mentions_terms_refusal(response: dict[str, Any]) -> bool:
    return _REF in json.dumps(response.get("content", {}))


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
    ]


@pytest.mark.req("NFR-45")
def test_the_refusal_body_is_a_schema_with_a_machine_code(document: dict[str, Any]) -> None:
    schema = document["components"]["schemas"]["TermsAcceptanceRequiredResponse"]

    assert set(schema["required"]) == {"detail", "code"}
    assert schema["properties"]["code"]["const"] == "terms_acceptance_required"


@pytest.mark.req("NFR-45")
def test_every_gated_mutating_operation_declares_the_terms_refusal(
    document: dict[str, Any],
) -> None:
    exempt = {(method.lower(), f"{API_PREFIX}{path}") for method, path in EXEMPT_ROUTES}
    gated = [
        (method, path, operation)
        for method, path, operation in _operations(document)
        if method not in _SAFE and (method, path) not in exempt
    ]

    assert len(gated) > 10
    missing = [
        f"{method.upper()} {path}"
        for method, path, operation in gated
        if not _mentions_terms_refusal(operation["responses"].get("403", {}))
    ]
    assert not missing, f"no terms refusal declared on: {missing}"


@pytest.mark.req("NFR-45")
def test_a_route_that_already_declared_a_403_keeps_that_body_beside_the_terms_one(
    document: dict[str, Any],
) -> None:
    response = document["paths"][f"{API_PREFIX}/registry/properties"]["post"]["responses"]["403"]
    refs = json.dumps(response["content"])

    assert "#/components/schemas/ErrorResponse" in refs
    assert _REF in refs


@pytest.mark.req("NFR-45")
def test_the_exempt_and_read_operations_do_not_declare_the_terms_refusal(
    document: dict[str, Any],
) -> None:
    exempt = {(method.lower(), f"{API_PREFIX}{path}") for method, path in EXEMPT_ROUTES}
    ungated = [
        (method, path, operation)
        for method, path, operation in _operations(document)
        if method in _SAFE or (method, path) in exempt
    ]

    assert ungated
    for method, path, operation in ungated:
        assert not _mentions_terms_refusal(operation["responses"].get("403", {})), (
            f"{method.upper()} {path} must stay open without acceptance"
        )


@pytest.mark.req("NFR-47")
def test_the_accept_route_declares_the_stale_version_conflict(document: dict[str, Any]) -> None:
    response = document["paths"][f"{API_PREFIX}/auth/terms/acceptance"]["post"]["responses"]["409"]

    assert "#/components/schemas/TermsVersionStaleResponse" in json.dumps(response["content"])
    stale = document["components"]["schemas"]["TermsVersionStaleResponse"]
    assert set(stale["required"]) == {"detail", "code", "current_version"}
