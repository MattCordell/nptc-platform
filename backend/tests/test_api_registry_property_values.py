"""HTTP tests for `GET /registry/properties/{key}/values` (issue #247,
FR-10, FR-52, FR-90).

Follows `test_api_terminology.py`'s own precedent: the domain logic already
has its own unit tests (`test_catalogue_property_value_sources.py`); this
module proves the HTTP adapter - status codes, the `nptc.api.errors`
mapping, and `Permission.REGISTRY_READ` authorisation - against the real
`create_app()`, with `api.terminology` (a `StubTerminologyClient`) standing
in for the terminology server (NFR-37) and `api.session` for a real
Postgres-backed `LocalCode` table (migration 0011's own seeded `discipline`
codes).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked, revoke_all_roles_unchecked
from nptc.auth.permissions import Role
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.terminology import (
    SNOMED_CT_AU,
    ExpandedConcept,
    Expansion,
    Operation,
    TerminologyOutcomeError,
    TerminologyTransportError,
)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

_SPECIMEN_ECL = "<123038009"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _role_token(api: ApiTestApp, *, subject: str, role: Role) -> str:
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    revoke_all_roles_unchecked(api.session, target_user_id=user.id, audit=AuditContext.system())
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    return api.token(subject=subject)


def _seed(api: ApiTestApp) -> None:
    seed_system_properties(api.session)
    api.session.flush()


def _get_values(api: ApiTestApp, key: str, token: str | None, **params: Any) -> Any:
    return api.get(f"/registry/properties/{key}/values", token=token, params=params)


def _expansion(codes_and_displays: list[tuple[str, str]]) -> Expansion:
    concepts = tuple(
        ExpandedConcept(code=code, system="http://snomed.info/sct", display=display)
        for code, display in codes_and_displays
    )
    return Expansion(concepts=concepts, total=len(concepts), offset=0)


# --- happy paths --------------------------------------------------------


@pytest.mark.req("FR-10")
@pytest.mark.req("FR-52")
@pytest.mark.integration
def test_specimen_lists_snomed_concepts_via_one_expand_call(api: ApiTestApp) -> None:
    _seed(api)
    api.terminology.seed_expansion(
        _SPECIMEN_ECL,
        _expansion([("122192001", "Acanthamoeba culture")]),
        edition=SNOMED_CT_AU,
    )
    token = _role_token(api, subject="sub-values-specimen", role=Role.PROVISIONAL)

    response = _get_values(api, "specimen", token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"items": [{"code": "122192001", "display": "Acanthamoeba culture"}], "total": 1}
    assert [r.operation for r in api.terminology.requests] == [Operation.EXPAND]


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_specimen_filter_query_param_narrows_the_expand_call(api: ApiTestApp) -> None:
    _seed(api)
    api.terminology.seed_expansion(
        _SPECIMEN_ECL,
        _expansion([("122192001", "Acanthamoeba culture")]),
        edition=SNOMED_CT_AU,
        filter="acantha",
    )
    token = _role_token(api, subject="sub-values-specimen-filter", role=Role.PROVISIONAL)

    response = _get_values(api, "specimen", token, filter="acantha")

    assert response.status_code == 200, response.text
    assert [item["code"] for item in response.json()["items"]] == ["122192001"]


@pytest.mark.req("FR-10")
@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_discipline_lists_local_codes_with_no_terminology_call(api: ApiTestApp) -> None:
    """Exercises migration 0011's own seed data - the acceptance criterion,
    verbatim: "the same response shape, with no terminology-server call at
    all"."""
    _seed(api)
    token = _role_token(api, subject="sub-values-discipline", role=Role.PROVISIONAL)

    response = _get_values(api, "discipline", token)

    assert response.status_code == 200, response.text
    codes = {item["code"] for item in response.json()["items"]}
    assert "chemical_pathology" in codes
    assert api.terminology.requests == ()


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_response_shape_is_identical_for_both_binding_targets(api: ApiTestApp) -> None:
    _seed(api)
    api.terminology.seed_expansion(_SPECIMEN_ECL, _expansion([]), edition=SNOMED_CT_AU)
    token = _role_token(api, subject="sub-values-shape-parity", role=Role.PROVISIONAL)

    specimen = _get_values(api, "specimen", token)
    discipline = _get_values(api, "discipline", token)

    assert specimen.status_code == discipline.status_code == 200
    assert specimen.json().keys() == discipline.json().keys() == {"items", "total"}
    for item in discipline.json()["items"]:
        assert item.keys() == {"code", "display"}


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_discipline_count_query_param_pages_results(api: ApiTestApp) -> None:
    _seed(api)
    token = _role_token(api, subject="sub-values-paging", role=Role.PROVISIONAL)

    response = _get_values(api, "discipline", token, count=1)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] > 1


# --- errors --------------------------------------------------------------


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_unknown_key_is_404(api: ApiTestApp) -> None:
    token = _role_token(api, subject="sub-values-unknown-key", role=Role.PROVISIONAL)

    response = _get_values(api, "not_a_real_property", token)

    assert response.status_code == 404, response.text


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_non_code_property_is_422(api: ApiTestApp) -> None:
    """`usage_guidance` (`nptc.db.bootstrap`) is `datatype == "string"` - it
    has no bound value source at all."""
    _seed(api)
    token = _role_token(api, subject="sub-values-non-code", role=Role.PROVISIONAL)

    response = _get_values(api, "usage_guidance", token)

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_negative_offset_is_422(api: ApiTestApp) -> None:
    _seed(api)
    token = _role_token(api, subject="sub-values-negative-offset", role=Role.PROVISIONAL)

    response = _get_values(api, "specimen", token, offset=-1)

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_terminology_unavailable_is_503(api: ApiTestApp) -> None:
    _seed(api)
    api.terminology.seed_error(Operation.EXPAND, TerminologyTransportError("connection refused"))
    token = _role_token(api, subject="sub-values-unavailable", role=Role.PROVISIONAL)

    response = _get_values(api, "specimen", token)

    assert response.status_code == 503, response.text


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_unclassified_terminology_failure_is_502(api: ApiTestApp) -> None:
    _seed(api)
    api.terminology.seed_error(
        Operation.EXPAND, TerminologyOutcomeError("server refused the request")
    )
    token = _role_token(api, subject="sub-values-upstream", role=Role.PROVISIONAL)

    response = _get_values(api, "specimen", token)

    assert response.status_code == 502, response.text


# --- authorisation (FR-44) ------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_no_credential_is_401(api: ApiTestApp) -> None:
    _seed(api)
    response = _get_values(api, "specimen", None)
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_without_registry_read_is_403(api: ApiTestApp) -> None:
    """`Role.OBSERVER` is the one authenticated role without
    `Permission.REGISTRY_READ` (ADR-0028) - see
    `test_api_registry_properties.py`'s identical precedent."""
    _seed(api)
    token = _role_token(api, subject="sub-values-observer", role=Role.OBSERVER)

    response = _get_values(api, "specimen", token)

    assert response.status_code == 403, response.text
