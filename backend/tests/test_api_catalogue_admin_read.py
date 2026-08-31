"""HTTP tests for `nptc.api.routers.catalogue_admin` (issue #228, FR-17,
FR-36).

`routers/catalogue.py`'s public detail route only ever serves an `active`
entry - a `draft`/`deprecated`/`withdrawn` one 404s identically to a
`business_key` that was never minted (FR-20's own deliberate contract,
proven in `test_api_public_status_filter.py`). This module proves the
authenticated counterpart: `GET /catalogue/admin/entries/{business_key}`
serves any status to a caller holding `catalogue.edit_published`, in the
same `EntryDetail` shape, while the public route's contract stays
untouched for everyone else.

The negative case is the point (CLAUDE.md): every authorisation refusal
(no credential, no permission, no MFA step-up) and every domain refusal
(unknown key, malformed key) has its own test here, following
`test_api_catalogue_designations.py`'s own shape exactly.
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
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.db.models.catalogue_entry import CatalogueEntryStatus
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
_seed = _load("public_catalogue_support")

build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp
seed_public_catalogue = _seed.seed_public_catalogue
SeededCatalogue = _seed.SeededCatalogue


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


def _token_with_role(api: ApiTestApp, *, subject: str, role: Role, with_mfa: bool = True) -> str:
    """Matching `test_api_catalogue_designations.py`'s own helper of the
    same name - duplicated rather than imported, following this test
    tree's convention that a `test_*.py` module is never imported by
    another (only the `_load`-by-path support modules are)."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    return _token_with_role(api, subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa)


def _admin_read(api: ApiTestApp, business_key: str, token: str | None) -> Any:
    return api.get(f"/catalogue/admin/entries/{business_key}", token=token)


# --- happy paths -----------------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.req("FR-17")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "deprecated", "withdrawn", "active"])
def test_an_administrator_can_load_an_entry_of_any_status(
    api: ApiTestApp, seeded: SeededCatalogue, status: str
) -> None:
    """Parametrised over every `CatalogueEntryStatus`, driven off the enum
    (not a list retyped here), so a fifth status fails this test rather
    than shipping untested - matching `test_api_public_status_filter.py`'s
    own exhaustiveness discipline."""
    assert {s.value for s in CatalogueEntryStatus} == {"draft", "deprecated", "withdrawn", "active"}
    business_key = {
        "draft": seeded.draft,
        "deprecated": seeded.deprecated,
        "withdrawn": seeded.withdrawn,
        "active": seeded.canonical,
    }[status]
    token = _admin_token(api, subject=f"sub-admin-read-{status}")

    response = _admin_read(api, business_key, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_key"] == business_key
    assert body["status"] == status


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_a_drafts_full_detail_is_populated_not_a_bare_summary(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The acceptance criterion in full: an edit screen loading a draft
    needs designations, bindings *and* properties, not just the entry-level
    fields every status shares."""
    token = _admin_token(api, subject="sub-admin-draft-detail")

    response = _admin_read(api, seeded.draft, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["designations"], "expected the seeded draft synonym"
    assert body["bindings"], "expected the seeded draft binding"
    assert body["properties"], "expected the seeded draft property value"
    assert {d["term"] for d in body["designations"]} == {_seed.DRAFT_SYNONYM}
    assert {b["code"] for b in body["bindings"]} == {_seed.DRAFT_CODE}


# --- authorisation (FR-44, NFR-06, NFR-20) ----------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    response = _admin_read(api, seeded.draft, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_observer_is_403_with_no_challenge(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """FR-44: authorised against the permission, not the role - Observer
    holds neither `catalogue.edit_published` nor any permission close to
    it."""
    token = _token_with_role(api, subject="sub-observer", role=Role.OBSERVER)

    response = _admin_read(api, seeded.draft, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_reviewer_is_403(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    """A Reviewer holds `validation.acknowledge` (issue #224) but not
    `catalogue.edit_published` - proving the gate is this specific
    permission, not "any elevated role"."""
    token = _token_with_role(api, subject="sub-reviewer", role=Role.REVIEWER)

    response = _admin_read(api, seeded.draft, token)

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-admin-no-mfa", with_mfa=False)

    response = _admin_read(api, seeded.draft, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


# --- domain refusals ---------------------------------------------------


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_unknown_business_key_is_a_404_with_the_generic_body(api: ApiTestApp) -> None:
    """Same fixed body the public route's own 404 carries (they share the
    same `EntryNotFoundError` handler) - no echo of the key, no hint
    whether it was ever minted."""
    token = _admin_token(api, subject="sub-admin-404")

    response = _admin_read(api, _seed.unused_business_key(), token)

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "No catalogue entry was found for the given identifier."}


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_a_malformed_business_key_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-admin-422")

    response = _admin_read(api, "NPTC-abc", token)

    assert response.status_code == 422, response.text


# --- regression: the public contract is unweakened --------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_the_public_route_still_404s_a_draft_even_for_an_administrator(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The whole point of putting this behind its own URL rather than
    widening the public one: an Administrator's token does not make the
    *public* `/catalogue/entries/{key}` route see a draft. The admin
    capability lives only at `/catalogue/admin/entries/{key}`."""
    token = _admin_token(api, subject="sub-admin-public-still-hidden")

    response = api.get(f"/catalogue/entries/{seeded.draft}", token=token)

    assert response.status_code == 404, response.text
