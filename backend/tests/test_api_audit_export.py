"""HTTP tests for `nptc.api.routers.audit`'s export route (issue #286,
NFR-12, FR-44, NFR-06, NFR-10).

Matching `test_api_audit_search.py`'s own precedent: the domain-level
streaming logic already has its own tests in `test_audit_search.py`
(`test_stream_audit_events_yields_oldest_first_with_hash_chain_fields`),
so this module proves the HTTP adapter - content type, filename, the
NDJSON body shape, and authorisation - against the real `create_app()`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.identity import close_account


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

_audit_support = _load("audit_api_support")
_admin_token = _audit_support.admin_token
_create_active_user = _audit_support.create_active_user
_seed = _audit_support.seed_event


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _ndjson_rows(response: Any) -> list[dict[str, Any]]:
    lines = [line for line in response.text.splitlines() if line]
    return [json.loads(line) for line in lines]


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_export_returns_ndjson_content_type_and_attachment_header(api: ApiTestApp) -> None:
    entity_type = f"test-export-shape-{uuid.uuid4()}"
    actor = _create_active_user(api, "peter-api-audit-export")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    token = _admin_token(api, subject="sub-audit-export-shape")

    response = api.get("/audit/events/export", token=token, params={"entity_type": entity_type})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert 'filename="audit-events.ndjson"' in response.headers["content-disposition"]


@pytest.mark.req("NFR-12")
@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_export_streams_one_json_object_per_line_oldest_first_with_hashes(
    api: ApiTestApp,
) -> None:
    entity_type = f"test-export-order-{uuid.uuid4()}"
    actor = _create_active_user(api, "quinn-api-audit-export")
    first = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    second = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    token = _admin_token(api, subject="sub-audit-export-order")

    response = api.get("/audit/events/export", token=token, params={"entity_type": entity_type})

    rows = _ndjson_rows(response)
    assert [row["sequence"] for row in rows] == [first.sequence, second.sequence]
    for row in rows:
        assert len(row["prev_hash"]) == 64
        assert len(row["entry_hash"]) == 64


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_export_filters_narrow_the_result_set(api: ApiTestApp) -> None:
    entity_type = f"test-export-filter-{uuid.uuid4()}"
    actor = _create_active_user(api, "rex-api-audit-export")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type, action="test.created")
    _seed(api, actor_user_id=actor.id, entity_type=entity_type, action="test.updated")
    token = _admin_token(api, subject="sub-audit-export-filter")

    response = api.get(
        "/audit/events/export",
        token=token,
        params={"entity_type": entity_type, "action": "test.created"},
    )

    rows = _ndjson_rows(response)
    assert len(rows) == 1
    assert rows[0]["action"] == "test.created"


@pytest.mark.req("NFR-13")
@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_export_closed_accounts_actor_is_not_blank(api: ApiTestApp) -> None:
    entity_type = f"test-export-closed-{uuid.uuid4()}"
    actor = _create_active_user(api, "sam-api-audit-export")
    event = _seed(api, actor_user_id=actor.id, entity_type=entity_type)
    close_account(api.session, actor.id, AuditContext.system())
    api.session.flush()
    token = _admin_token(api, subject="sub-audit-export-closed")

    response = api.get("/audit/events/export", token=token, params={"entity_type": entity_type})

    row = next(r for r in _ndjson_rows(response) if r["sequence"] == event.sequence)
    assert row["actor"]["id"] == str(actor.id)
    assert row["actor"]["display_name"] is None
    assert row["actor"]["is_closed"] is True


@pytest.mark.req("NFR-26")
@pytest.mark.req("NFR-35")
@pytest.mark.integration
def test_export_serves_a_withheld_field_only_under_redacted(api: ApiTestApp) -> None:
    entity_type = f"test-export-redacted-{uuid.uuid4()}"
    actor = _create_active_user(api, "tara-api-audit-export")
    event = _seed(
        api,
        actor_user_id=actor.id,
        entity_type=entity_type,
        action="test.updated",
        before={"status": "active", "_redacted": ["display_name"]},
        after={"status": "suspended", "_redacted": ["display_name"]},
    )
    token = _admin_token(api, subject="sub-audit-export-redacted")

    response = api.get("/audit/events/export", token=token, params={"entity_type": entity_type})

    row = next(r for r in _ndjson_rows(response) if r["sequence"] == event.sequence)
    assert row["before"] == {"status": "active", "_redacted": ["display_name"]}
    assert row["after"] == {"status": "suspended", "_redacted": ["display_name"]}


# --- validation (422) ------------------------------------------------------


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_export_entity_id_without_entity_type_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-export-bad-entity")

    response = api.get("/audit/events/export", token=token, params={"entity_id": "some-id"})

    assert response.status_code == 422, response.text


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_export_occurred_from_after_occurred_to_is_a_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-export-bad-range")

    response = api.get(
        "/audit/events/export",
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
def test_export_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    response = api.get("/audit/events/export", token=None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_export_authenticated_without_the_permission_is_403_with_no_challenge(
    api: ApiTestApp,
) -> None:
    token = api.token(subject="sub-audit-export-no-permission")

    response = api.get("/audit/events/export", token=token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_export_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-audit-export-admin-no-mfa", with_mfa=False)

    response = api.get("/audit/events/export", token=token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge
