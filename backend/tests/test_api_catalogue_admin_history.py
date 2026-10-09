"""HTTP tests for `GET /catalogue/admin/entries/{business_key}/history` (FR-19, FR-44,
NFR-06).

The public history route 404s every entry that is not `active` (FR-20), so an editor could
not read the history of a draft they had just saved. This route serves the same
`HistoryPage` for an entry of any status to a caller holding `catalogue.edit_published`.

Setup writes go through the real service functions on `api.session` and `flush()`, never
`commit()`: the `app_db` fixture owns the transaction and rolls it back at teardown, as
`test_api_public_entry_history.py` explains. Every assertion is scoped to an entry this
test created, because `backend/tests` shares one Postgres container (CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.permissions import Role
from nptc.catalogue.bindings import create_binding
from nptc.catalogue.designations import add_synonyms
from nptc.catalogue.entries import EntryChanges, create_entry, save_entry
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.user import User


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

_REASON = "seeded for the admin history route test"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _history(api: ApiTestApp, business_key: str, token: str | None, query: str = "") -> Any:
    return api.get(f"/catalogue/admin/entries/{business_key}/history{query}", token=token)


def _new_entry(
    api: ApiTestApp,
    status: CatalogueEntryStatus = CatalogueEntryStatus.DRAFT,
    ctx: AuditContext | None = None,
    preferred_term: str = "Admin history fixture",
) -> CatalogueEntry:
    entry = create_entry(
        api.session,
        ctx or AuditContext.system(),
        preferred_term=preferred_term,
        reason=_REASON,
        status=status,
    )
    api.session.flush()
    return entry


def _create_string_property(api: ApiTestApp, token: str, key: str) -> None:
    response = api.post(
        "/registry/properties",
        token=token,
        json={
            "key": key,
            "label": "Admin history property",
            "datatype": "string",
            "cardinality": "0..1",
            "scope": "both",
            "display_order": 0,
            "constraints": {},
            "reason": _REASON,
        },
    )
    assert response.status_code == 201, response.text


# --- happy paths -----------------------------------------------------------


@pytest.mark.req("FR-19")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("status", list(CatalogueEntryStatus))
def test_an_editor_can_read_the_history_of_an_entry_of_any_status(
    api: ApiTestApp, status: CatalogueEntryStatus
) -> None:
    """Parametrised from the enum, so a fifth status fails here rather than shipping
    untested."""
    entry = _new_entry(api, status)
    token = api.admin_token(subject=f"sub-admin-history-{status.value}")

    response = _history(api, entry.business_key, token)

    assert response.status_code == 200, response.text
    actions = [item["action"] for item in response.json()["items"]]
    assert actions == ["catalogue_entry.created"]


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_a_saved_draft_shows_its_entry_designation_binding_and_property_events(
    api: ApiTestApp,
) -> None:
    """The reported defect: an editor saves a draft and cannot read what they changed."""
    token = api.admin_token(subject="sub-admin-history-draft")
    key = f"history_{uuid.uuid4().hex[:8]}"
    _create_string_property(api, token, key)
    ctx = AuditContext.system()
    entry = _new_entry(api, ctx=ctx)
    save_entry(
        api.session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Admin history fixture renamed"),
        reason="renamed while still a draft",
    )
    add_synonyms(api.session, ctx, entry=entry, terms=["Draft synonym"], reason="added a synonym")
    create_binding(
        api.session,
        ctx,
        entry=entry,
        code="71388002",
        fsn="Procedure (procedure)",
        reason="bound a code",
    )
    api.session.flush()
    put = api.request(
        "PUT",
        f"/catalogue/entries/{entry.business_key}/properties/{key}",
        token=token,
        json={
            "values": [{"value": "a value"}],
            "reason": "recorded a value",
            "expected_row_version": entry.row_version,
        },
    )
    assert put.status_code == 200, put.text

    response = _history(api, entry.business_key, token)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["action"] for item in items} == {
        "catalogue_entry.created",
        "catalogue_entry.updated",
        "designation.created",
        "code_binding.created",
        "property_value.set",
    }
    assert items[0]["action"] == "property_value.set"
    assert items[0]["note"] == "recorded a value"
    assert items[0]["release"] is None


@pytest.mark.req("FR-19")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_changed_by_carries_the_display_name_for_an_authenticated_editor(
    api: ApiTestApp,
) -> None:
    """Every caller of this route is authenticated, so NFR-26's withholding (anonymous
    callers only) does not apply. A system-initiated change still has no one to name, and
    must stay `null` rather than gain a placeholder."""
    actor = User(username="admin-history-actor", display_name="Admin History Actor")
    api.session.add(actor)
    api.session.flush()
    ctx = AuditContext(
        actor_user_id=actor.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )
    entry = _new_entry(api, ctx=ctx)
    save_entry(
        api.session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Admin history fixture renamed by the system"),
        reason="renamed by a system-initiated change",
    )
    api.session.flush()
    token = api.admin_token(subject="sub-admin-history-actor")

    items = _history(api, entry.business_key, token).json()["items"]

    assert [(item["action"], item["changed_by"]) for item in items] == [
        ("catalogue_entry.updated", None),
        ("catalogue_entry.created", "Admin History Actor"),
    ]


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_pages_with_a_keyset_cursor(api: ApiTestApp) -> None:
    ctx = AuditContext.system()
    entry = _new_entry(api, ctx=ctx)
    save_entry(
        api.session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Admin history fixture v2"),
        reason="first edit",
    )
    api.session.flush()
    token = api.admin_token(subject="sub-admin-history-paging")

    first = _history(api, entry.business_key, token, "?limit=1").json()
    assert [item["action"] for item in first["items"]] == ["catalogue_entry.updated"]
    assert first["next_cursor"] is not None

    second = _history(
        api, entry.business_key, token, f"?limit=1&before={first['next_cursor']}"
    ).json()
    assert [item["action"] for item in second["items"]] == ["catalogue_entry.created"]
    assert second["next_cursor"] is None


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_never_leaks_a_raw_value_or_internal_id(api: ApiTestApp) -> None:
    ctx = AuditContext.system()
    entry = _new_entry(api, ctx=ctx, preferred_term="Admin redaction original term")
    save_entry(
        api.session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Admin redaction UNIQUE_CHANGED_VALUE_XYZ"),
        reason="renamed for the redaction check",
    )
    api.session.flush()
    token = api.admin_token(subject="sub-admin-history-redaction")

    body_text = _history(api, entry.business_key, token).text

    assert "UNIQUE_CHANGED_VALUE_XYZ" not in body_text
    assert "Admin redaction original term" not in body_text
    assert str(entry.id) not in body_text
    assert '"changed_fields":["preferred_term"]' in body_text.replace(" ", "")


# --- authorisation (FR-44, NFR-06, NFR-20) ----------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    entry = _new_entry(api)

    response = _history(api, entry.business_key, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_observer_is_403_with_no_challenge(api: ApiTestApp) -> None:
    entry = _new_entry(api)
    token = api.token_for_role(subject="sub-history-observer", role=Role.OBSERVER)

    response = _history(api, entry.business_key, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_reviewer_is_403(api: ApiTestApp) -> None:
    entry = _new_entry(api)
    token = api.token_for_role(subject="sub-history-reviewer", role=Role.REVIEWER)

    response = _history(api, entry.business_key, token)

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    entry = _new_entry(api)
    token = api.admin_token(subject="sub-history-no-mfa", with_mfa=False)

    response = _history(api, entry.business_key, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


# --- domain refusals ---------------------------------------------------


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_unknown_business_key_is_a_404_with_the_generic_body(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-history-404")

    response = _history(api, _seed.unused_business_key(), token)

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "No catalogue entry was found for the given identifier."}


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_a_malformed_business_key_is_a_422(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-history-422-key")

    assert _history(api, "NPTC-abc", token).status_code == 422


@pytest.mark.req("FR-19")
@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    [
        "?before=9223372036854775808",  # one past the bigint range
        "?before=abc",  # not a digit string
        "?limit=0",  # below the page-size floor
    ],
)
def test_a_bad_paging_parameter_is_a_422(api: ApiTestApp, query: str) -> None:
    entry = _new_entry(api)
    token = api.admin_token(subject="sub-history-422-paging")

    assert _history(api, entry.business_key, token, query).status_code == 422


# --- regression: the public contract is unweakened --------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_the_public_history_route_still_404s_a_draft_even_for_an_administrator(
    api: ApiTestApp,
) -> None:
    entry = _new_entry(api)
    token = api.admin_token(subject="sub-history-public-still-hidden")

    response = api.get(f"/catalogue/entries/{entry.business_key}/history", token=token)

    assert response.status_code == 404, response.text
