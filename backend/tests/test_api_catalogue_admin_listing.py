"""HTTP tests for `nptc.api.routers.catalogue_admin`'s all-status listing
and search (issue #266, FR-14, FR-15, FR-16, FR-36, FR-44).

`test_api_catalogue_admin_read.py` proves the single-entry admin read
(issue #228); this module proves its collection counterpart. The point of
every test here is the same one that module states: a draft, deprecated or
withdrawn entry is reachable through `GET /catalogue/admin/entries` and
`GET /catalogue/admin/search` for a caller holding
`catalogue.edit_published`, while the public `/catalogue/entries` and
`/catalogue/search` stay exactly as restricted as
`test_api_public_status_filter.py` already proves - both halves asserted in
the same test wherever practical, so a regression in either direction fails
here rather than passing on the strength of the other.
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
from nptc.catalogue.maintenance import MAINTENANCE_STATUSES
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
    """Matching `test_api_catalogue_admin_read.py`'s own helper of the same
    name - duplicated rather than imported, following this test tree's
    convention that a `test_*.py` module is never imported by another."""
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


def _admin_list(api: ApiTestApp, token: str | None, **params: Any) -> Any:
    return api.get("/catalogue/admin/entries", token=token, params=params)


def _admin_search(api: ApiTestApp, token: str | None, **params: Any) -> Any:
    return api.get("/catalogue/admin/search", token=token, params=params)


# --- MAINTENANCE_STATUSES itself --------------------------------------------


def test_maintenance_statuses_is_every_catalogue_entry_status() -> None:
    """A guard on the constant, matching `test_api_public_status_filter.py`'s
    own guard on `PUBLIC_STATUSES` - if this silently narrowed, every test
    below parametrised or asserted against it would start proving less than
    it looks like it does."""
    assert set(MAINTENANCE_STATUSES) == {status.value for status in CatalogueEntryStatus}


# --- visibility parity: both surfaces asserted together ---------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_hidden_entries_are_in_the_admin_listing_and_absent_from_the_public_one(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-list-visibility")

    admin_response = _admin_list(api, token, after=seeded.before_all, limit=200)
    assert admin_response.status_code == 200, admin_response.text
    admin_keys = {item["business_key"] for item in admin_response.json()["items"]}
    assert set(seeded.hidden) <= admin_keys
    assert seeded.canonical in admin_keys

    public_response = api.get(
        "/catalogue/entries", params={"after": seeded.before_all, "limit": 200}
    )
    assert public_response.status_code == 200, public_response.text
    public_keys = {item["business_key"] for item in public_response.json()["items"]}
    assert not public_keys & set(seeded.hidden)


@pytest.mark.req("FR-14")
@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_hidden_entries_are_in_the_admin_search_and_absent_from_the_public_one(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Searched by `CANONICAL_TERM`, which the three hidden fixtures carry
    near-copies of precisely so this query scores them above the threshold
    (see `public_catalogue_support.seed_public_catalogue`'s own note) - a
    query the hidden entries do not match would prove nothing about the
    status filter."""
    token = _admin_token(api, subject="sub-search-visibility")

    admin_response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)
    assert admin_response.status_code == 200, admin_response.text
    admin_keys = {item["business_key"] for item in admin_response.json()["items"]}
    assert set(seeded.hidden) <= admin_keys
    assert seeded.canonical in admin_keys

    public_response = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 200})
    assert public_response.status_code == 200, public_response.text
    public_keys = {item["business_key"] for item in public_response.json()["items"]}
    assert not public_keys & set(seeded.hidden)


@pytest.mark.req("FR-36")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "deprecated", "withdrawn", "active"])
def test_each_row_carries_its_own_status(
    api: ApiTestApp, seeded: SeededCatalogue, status: str
) -> None:
    """Issue #266's own acceptance criterion: `status` is present on every
    maintenance row, so a caller can tell a draft from an active entry
    without a second call. Parametrised over every `CatalogueEntryStatus`,
    driven off the enum, so a fifth status fails this test rather than
    shipping untested."""
    business_key = {
        "draft": seeded.draft,
        "deprecated": seeded.deprecated,
        "withdrawn": seeded.withdrawn,
        "active": seeded.canonical,
    }[status]
    token = _admin_token(api, subject=f"sub-status-{status}")

    response = _admin_list(api, token, after=seeded.before_all, limit=200)

    assert response.status_code == 200, response.text
    rows = {item["business_key"]: item["status"] for item in response.json()["items"]}
    assert rows[business_key] == status


# --- keyset paging (ADR-0024) ------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_the_listing_pages_with_no_offset_and_a_null_cursor_on_the_last_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-list-paging")

    first = _admin_list(api, token, after=seeded.before_all, limit=1)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"] is not None

    rest = _admin_list(api, token, after=first_body["next_cursor"], limit=200)
    assert rest.status_code == 200, rest.text
    rest_body = rest.json()
    assert first_body["items"][0]["business_key"] not in {
        item["business_key"] for item in rest_body["items"]
    }
    assert rest_body["next_cursor"] is None


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_a_malformed_listing_cursor_is_a_422(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    token = _admin_token(api, subject="sub-list-bad-cursor")

    response = _admin_list(api, token, after="not-a-business-key")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_malformed_search_cursor_is_a_422(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    token = _admin_token(api, subject="sub-search-bad-cursor")

    response = _admin_search(api, token, q=_seed.CANONICAL_TERM, after="not-a-cursor-at-all")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_search_cursor_replayed_under_a_different_query_is_a_422(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-search-cursor-mismatch")

    first = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=1)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    replayed = _admin_search(api, token, q="a completely different query", after=cursor)

    assert replayed.status_code == 422, replayed.text


# --- the status facet is non-degenerate (unlike the public one) ------------


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_status_facet_has_more_than_one_bucket(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The public search's own `status` facet has exactly one bucket
    (`active`) because `PUBLIC_STATUSES` is one status - see
    `nptc.catalogue.facets._core_facets`'s own docstring. This surface's
    facet context is built from `MAINTENANCE_STATUSES` instead, so the same
    facet has real buckets to offer once more than one status is actually
    present in the matched result set."""
    token = _admin_token(api, subject="sub-status-facet")

    response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)

    assert response.status_code == 200, response.text
    facets = {facet["key"]: facet for facet in response.json()["facets"]}
    status_facet = facets["status"]
    assert status_facet["facetable"] is True
    bucket_values = {bucket["value"] for bucket in status_facet["buckets"]}
    assert len(bucket_values) > 1, status_facet
    assert bucket_values <= {status.value for status in CatalogueEntryStatus}


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_filtering_by_a_hidden_status_is_accepted_and_narrows_the_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`?filter.status=draft` is a 422 on the public surface
    (`test_api_public_search.py::test_an_unrecognised_status_value_is_a_422_
    not_an_empty_page` proves `active` is the only value it accepts) - here
    it is a real filter, because `AdminFiltersDep` builds its facet context
    from `MAINTENANCE_STATUSES`."""
    token = _admin_token(api, subject="sub-status-filter")

    response = _admin_list(
        api, token, after=seeded.before_all, limit=200, **{"filter.status": "draft"}
    )

    assert response.status_code == 200, response.text
    keys = {item["business_key"] for item in response.json()["items"]}
    assert keys == {seeded.draft}


# --- authorisation (FR-44, NFR-06, NFR-20) ----------------------------------


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp, seeded: SeededCatalogue, call: Any) -> None:
    response = call(api, None, q=_seed.CANONICAL_TERM)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_observer_is_403_with_no_challenge(
    api: ApiTestApp, seeded: SeededCatalogue, call: Any
) -> None:
    token = _token_with_role(api, subject=f"sub-observer-{call.__name__}", role=Role.OBSERVER)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_reviewer_is_403(api: ApiTestApp, seeded: SeededCatalogue, call: Any) -> None:
    """A Reviewer holds `validation.acknowledge` but not
    `catalogue.edit_published` - proving the gate is this specific
    permission, not "any elevated role"."""
    token = _token_with_role(api, subject=f"sub-reviewer-{call.__name__}", role=Role.REVIEWER)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(
    api: ApiTestApp, seeded: SeededCatalogue, call: Any
) -> None:
    token = _admin_token(api, subject=f"sub-admin-no-mfa-{call.__name__}", with_mfa=False)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge
