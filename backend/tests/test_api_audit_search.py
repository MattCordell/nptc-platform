"""HTTP tests for `nptc.api.routers.audit`'s read route (issue #286,
NFR-12, FR-44, NFR-06).

The domain-level filter logic already has its own exhaustive tests in
`test_audit_search.py`; this module proves the HTTP adapter on top of it -
request/response shape, status codes, the exception-handler mapping in
`nptc.api.errors`, and authorisation - against the real `create_app()`,
matching `test_api_catalogue_bindings.py`'s own precedent.

The negative case is the point (CLAUDE.md): no credential, no permission,
and the MFA step-up each have their own test here, not just the happy
path.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext, append_audit_event
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.identity import close_account
from nptc.auth.permissions import Role
from nptc.db.models.audit import AuditEvent
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
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    """Signs `subject` in, grants `Role.ADMINISTRATOR`, and returns a
    token - matching `test_api_catalogue_bindings.py`'s own helper."""
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
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _create_active_user(api: ApiTestApp, username: str) -> User:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    api.session.add(user)
    api.session.flush()
    return user


def _seed(
    api: Any,
    *,
    actor_user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str = "1",
    action: str = "test.action",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> AuditEvent:
    event = append_audit_event(
        api.session,
        AuditContext(
            actor_user_id=actor_user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
        )
        if actor_user_id is not None
        else AuditContext.system(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    api.session.flush()
    return event


# --- happy path -------------------------------------------------------


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_read_audit_events_returns_the_event_shape(api: ApiTestApp) -> None:
    entity_type = f"test-api-happy-{uuid.uuid4()}"
    actor = _create_active_user(api, "kate-api-audit")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type, action="test.created")
    token = _admin_token(api, subject="sub-audit-happy")

    response = api.get("/audit/events", token=token, params={"entity_type": entity_type})

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["action"] == "test.created"
    assert item["entity_type"] == entity_type
    assert item["actor"]["id"] == str(actor.id)
    assert item["actor"]["display_name"] == "Kate-Api-Audit"
    assert item["actor"]["is_closed"] is False


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filters_narrow_the_result_set_over_http(api: ApiTestApp) -> None:
    entity_type = f"test-api-filter-{uuid.uuid4()}"
    actor = _create_active_user(api, "liam-api-audit")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type, action="test.created")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type, action="test.updated")
    token = _admin_token(api, subject="sub-audit-filter")

    response = api.get(
        "/audit/events",
        token=token,
        params={"entity_type": entity_type, "action": "test.created"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "test.created"


@pytest.mark.integration
def test_pagination_cursor_round_trips_over_http(api: ApiTestApp) -> None:
    entity_type = f"test-api-page-{uuid.uuid4()}"
    actor = _create_active_user(api, "mia-api-audit")
    oldest = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    newest = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    token = _admin_token(api, subject="sub-audit-page")

    first = api.get("/audit/events", token=token, params={"entity_type": entity_type, "limit": 1})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert [item["sequence"] for item in first_body["items"]] == [newest.sequence]
    assert first_body["next_cursor"] == str(newest.sequence)

    second = api.get(
        "/audit/events",
        token=token,
        params={"entity_type": entity_type, "limit": 1, "before": first_body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert [item["sequence"] for item in second_body["items"]] == [oldest.sequence]
    assert second_body["next_cursor"] is None


# --- attribution (NFR-13, NFR-17, NFR-26, NFR-35) --------------------------


@pytest.mark.req("NFR-13")
@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_a_closed_accounts_actor_serves_a_null_display_name_not_a_blank_row(
    api: ApiTestApp,
) -> None:
    entity_type = f"test-api-closed-{uuid.uuid4()}"
    actor = _create_active_user(api, "noah-api-audit")
    event = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    close_account(api.session, actor.id, AuditContext.system())
    api.session.flush()
    token = _admin_token(api, subject="sub-audit-closed")

    response = api.get("/audit/events", token=token, params={"entity_type": entity_type})

    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["sequence"] == event.sequence)
    assert item["actor"]["id"] == str(actor.id)
    assert item["actor"]["display_name"] is None
    assert item["actor"]["is_closed"] is True


@pytest.mark.integration
def test_a_system_events_actor_is_null_over_http(api: ApiTestApp) -> None:
    entity_type = f"test-api-system-{uuid.uuid4()}"
    event = _seed(api, actor_user_id=None, entity_type=entity_type)
    token = _admin_token(api, subject="sub-audit-system")

    response = api.get("/audit/events", token=token, params={"entity_type": entity_type})

    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["sequence"] == event.sequence)
    assert item["actor"] is None


@pytest.mark.req("NFR-26")
@pytest.mark.req("NFR-35")
@pytest.mark.integration
def test_a_withheld_field_appears_only_under_redacted_never_by_value(api: ApiTestApp) -> None:
    entity_type = f"test-api-redacted-{uuid.uuid4()}"
    actor = _create_active_user(api, "olive-api-audit")
    event = _seed(
        api,
        actor_user_id=actor.id,
        entity_type=entity_type,
        action="test.updated",
        before={"status": "active", "_redacted": ["display_name"]},
        after={"status": "suspended", "_redacted": ["display_name"]},
    )
    token = _admin_token(api, subject="sub-audit-redacted")

    response = api.get("/audit/events", token=token, params={"entity_type": entity_type})

    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["sequence"] == event.sequence)
    # The withheld field's name is served (that it changed is not the
    # secret - `nptc.audit.diffing`'s own REDACTED_KEY posture), but never
    # a value under it: the payload set at write time above carries no
    # `display_name` key holding an actual value, and this asserts it is
    # served exactly as stored - nothing added, nothing filled in.
    assert item["before"] == {"status": "active", "_redacted": ["display_name"]}
    assert item["after"] == {"status": "suspended", "_redacted": ["display_name"]}


# --- validation (422) ------------------------------------------------------


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_a_cursor_beyond_the_sequence_range_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-bad-cursor")

    response = api.get("/audit/events", token=token, params={"before": "9223372036854775808"})

    assert response.status_code == 422, response.text


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_entity_id_without_entity_type_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-bad-entity")

    response = api.get("/audit/events", token=token, params={"entity_id": "some-id"})

    assert response.status_code == 422, response.text


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_occurred_from_after_occurred_to_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-bad-range")

    response = api.get(
        "/audit/events",
        token=token,
        params={
            "occurred_from": "2026-01-02T00:00:00Z",
            "occurred_to": "2026-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 422, response.text


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    response = api.get("/audit/events", token=None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_without_the_permission_is_403_with_no_challenge(api: ApiTestApp) -> None:
    token = api.token(subject="sub-audit-no-permission")

    response = api.get("/audit/events", token=token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-admin-no-mfa", with_mfa=False)

    response = api.get("/audit/events", token=token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge
