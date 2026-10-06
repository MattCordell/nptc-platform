"""The terms-of-use endpoints and the write-path gate, over real HTTP (NFR-45, NFR-47, NFR-08,
ADR-0043).

Runs the production app, so every request goes through the real token verifier, identity
resolution and permission derivation. Only the database connection, the IdP's address and the
directory the terms files are read from are substituted. That directory holds two versions, so a
test can move the current version forward and back (`NPTC_TERMS_CURRENT_VERSION`).

Marked `integration`: acceptance and audit rows need a real PostgreSQL (NFR-39).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.api.dependencies import get_token_verifier
from nptc.api.errors import TERMS_ACCEPTANCE_REQUIRED_CODE, TERMS_VERSION_STALE_CODE
from nptc.api.terms_gate import EXEMPT_ROUTES
from nptc.auth.permissions import Permission, Role
from nptc.db.models.audit import AuditEvent
from nptc.db.models.terms_acceptance import TermsAcceptance
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc.terms import documents


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
_inventory = _load("route_inventory_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp
mutating_routes_with_endpoints = _inventory.mutating_routes_with_endpoints

FIRST = "2026-10-06"
SECOND = "2027-01-15"
_ACCEPT_PATH = "/auth/terms/acceptance"
_REASON = "Created for the terms gate test."


@pytest.fixture
def terms_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    versions = tmp_path / "versions"
    versions.mkdir()
    for version in (FIRST, SECOND):
        (versions / f"{version}.md").write_text(
            f"---\nversion: {version}\neffective: {version}\n---\n\n# Terms {version}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(documents, "_versions_dir", lambda: versions)
    documents.load_terms_document.cache_clear()
    yield
    documents.load_terms_document.cache_clear()


@pytest.fixture
def api(app_db: Connection, terms_files: None) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _subject() -> str:
    return f"sub-terms-{uuid.uuid4().hex[:10]}"


def _unaccepted_admin(api: ApiTestApp) -> str:
    """An administrator with MFA who has not accepted any terms, so a refusal can only be the
    terms gate and never a missing permission."""
    return api.token_for_role(subject=_subject(), role=Role.ADMINISTRATOR, accept_terms=False)


def _user_for(api: ApiTestApp, subject: str) -> User:
    return api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()


def _write(api: ApiTestApp, token: str) -> Any:
    """An administrator's write that succeeds with a 201 once the gate lets it through."""
    key = f"terms_{uuid.uuid4().hex[:8]}"
    return api.post(
        "/registry/properties",
        token=token,
        json={
            "key": key,
            "label": key.replace("_", " ").title(),
            "datatype": "string",
            "cardinality": "0..1",
            "scope": "both",
            "display_order": 0,
            "reason": _REASON,
        },
    )


def _accept(api: ApiTestApp, token: str | None, version: str) -> Any:
    return api.post(_ACCEPT_PATH, token=token, json={"version": version})


def _acceptances(api: ApiTestApp, user_id: uuid.UUID) -> list[str]:
    return list(
        api.session.execute(
            select(TermsAcceptance.version)
            .where(TermsAcceptance.user_id == user_id)
            .order_by(TermsAcceptance.id)
        ).scalars()
    )


def _assert_terms_refusal(response: Any) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    assert isinstance(body["detail"], str) and body["detail"]
    # A 403 is not an authentication matter (ADR-0043), so no step-up challenge.
    assert "WWW-Authenticate" not in response.headers
    for role in Role:
        assert role.value not in response.text
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", response.text
    )


# --- reading the terms ----------------------------------------------------


@pytest.mark.req("NFR-47")
@pytest.mark.integration
def test_an_anonymous_caller_reads_the_current_terms_as_not_accepted(api: ApiTestApp) -> None:
    response = api.get("/auth/terms")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == FIRST
    assert body["effective_date"] == FIRST
    assert "# Terms 2026-10-06" in body["text"]
    assert body["accepted"] is False


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_the_current_terms_report_whether_the_caller_has_accepted(api: ApiTestApp) -> None:
    token = _unaccepted_admin(api)

    before = api.get("/auth/terms", token=token).json()
    _accept(api, token, FIRST)
    after = api.get("/auth/terms", token=token).json()

    assert (before["accepted"], after["accepted"]) == (False, True)


@pytest.mark.req("NFR-47")
@pytest.mark.integration
def test_an_earlier_version_is_served_by_its_id_without_a_credential(api: ApiTestApp) -> None:
    api.set_api_settings(terms_current_version=SECOND)

    response = api.get(f"/auth/terms/{FIRST}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == FIRST
    assert "# Terms 2026-10-06" in body["text"]
    assert api.get("/auth/terms").json()["version"] == SECOND


@pytest.mark.req("NFR-47")
@pytest.mark.integration
@pytest.mark.parametrize("version", ["2099-01-01", "latest", "2026-10-6", "..%2Fx"])
def test_an_unknown_terms_version_is_a_404(api: ApiTestApp, version: str) -> None:
    response = api.get(f"/auth/terms/{version}")

    assert response.status_code == 404, response.text
    assert set(response.json()) == {"detail"}


# --- accepting ------------------------------------------------------------


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_accepting_records_one_row_and_one_audit_event_naming_the_version(
    api: ApiTestApp,
) -> None:
    subject = _subject()
    token = api.token_for_role(subject=subject, role=Role.MEMBER, accept_terms=False)
    user = _user_for(api, subject)
    events_before = api.session.execute(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "terms_acceptance.created")
    ).scalar_one()

    response = _accept(api, token, FIRST)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == FIRST
    assert response.json()["accepted_at"]
    assert _acceptances(api, user.id) == [FIRST]
    events = (
        api.session.execute(
            select(AuditEvent).where(AuditEvent.action == "terms_acceptance.created")
        )
        .scalars()
        .all()
    )
    assert len(events) == events_before + 1
    event = events[-1]
    assert event.entity_type == "terms_acceptance"
    assert event.actor_user_id == user.id
    assert event.after is not None and event.after["version"] == FIRST


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_accepting_the_same_version_twice_writes_nothing_new(api: ApiTestApp) -> None:
    subject = _subject()
    token = api.token_for_role(subject=subject, role=Role.MEMBER, accept_terms=False)
    user = _user_for(api, subject)
    _accept(api, token, FIRST)
    events = (
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "terms_acceptance.created")
    )
    events_after_first = api.session.execute(events).scalar_one()

    second = _accept(api, token, FIRST)

    assert second.status_code == 200, second.text
    assert _acceptances(api, user.id) == [FIRST]
    assert api.session.execute(events).scalar_one() == events_after_first


@pytest.mark.req("NFR-47")
@pytest.mark.integration
@pytest.mark.parametrize("version", [SECOND, "2099-01-01", "not-a-version", ""])
def test_accepting_a_version_that_is_not_current_is_a_409_carrying_the_current_one(
    api: ApiTestApp, version: str
) -> None:
    """Without this the user would be recorded as accepting a text they were never shown."""
    subject = _subject()
    token = api.token_for_role(subject=subject, role=Role.MEMBER, accept_terms=False)
    user = _user_for(api, subject)

    response = _accept(api, token, version)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == TERMS_VERSION_STALE_CODE
    assert body["current_version"] == FIRST
    assert body["detail"]
    assert _acceptances(api, user.id) == []


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_accepting_as_an_anonymous_caller_is_a_401_not_a_403(api: ApiTestApp) -> None:
    response = _accept(api, None, FIRST)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.integration
def test_accepting_without_a_version_is_a_422(api: ApiTestApp) -> None:
    token = api.token_for_role(subject=_subject(), role=Role.MEMBER, accept_terms=False)

    response = api.post(_ACCEPT_PATH, token=token, json={})

    assert response.status_code == 422, response.text


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_later_acceptance_keeps_the_earlier_record(api: ApiTestApp) -> None:
    subject = _subject()
    token = api.token_for_role(subject=subject, role=Role.MEMBER, accept_terms=False)
    user = _user_for(api, subject)
    _accept(api, token, FIRST)
    api.set_api_settings(terms_current_version=SECOND)

    _accept(api, token, SECOND)

    assert _acceptances(api, user.id) == [FIRST, SECOND]


# --- the gate -------------------------------------------------------------


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_contribution_is_refused_until_the_new_version_is_accepted(api: ApiTestApp) -> None:
    """The principal failure mode: a user who accepted version N is refused once N+1 is current,
    and can contribute again after accepting it."""
    token = api.token_for_role(subject=_subject(), role=Role.ADMINISTRATOR, accept_terms=False)
    _accept(api, token, FIRST)
    assert _write(api, token).status_code == 201

    api.set_api_settings(terms_current_version=SECOND)
    refused = _write(api, token)
    _assert_terms_refusal(refused)

    assert _accept(api, token, SECOND).status_code == 200
    assert _write(api, token).status_code == 201


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_version_rolled_back_to_asks_for_acceptance_again(api: ApiTestApp) -> None:
    """The check is equality, so acceptance of a later version does not carry back to an
    earlier one."""
    token = api.token_for_role(subject=_subject(), role=Role.ADMINISTRATOR, accept_terms=False)
    _accept(api, token, FIRST)
    api.set_api_settings(terms_current_version=SECOND)
    _accept(api, token, SECOND)
    assert _write(api, token).status_code == 201

    api.set_api_settings(terms_current_version=FIRST)

    _assert_terms_refusal(_write(api, token))


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_who_never_accepted_is_refused(api: ApiTestApp) -> None:
    _assert_terms_refusal(_write(api, _unaccepted_admin(api)))


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_every_mutating_route_off_the_exempt_list_is_refused(api: ApiTestApp) -> None:
    """The default is refusal: the sweep walks the real route table, so a route added later is
    covered without anyone listing it."""
    token = _unaccepted_admin(api)
    routes = mutating_routes_with_endpoints(api.app)
    swept = [key for key in routes if (key.method, key.path) not in EXEMPT_ROUTES]

    assert len(swept) > 10, "the sweep found too few routes to prove anything"
    for key in swept:
        path = re.sub(r"\{[^}]+\}", "x", key.path)
        response = api.request(key.method, path, token=token)
        assert response.status_code == 403 and response.json().get("code") == (
            TERMS_ACCEPTANCE_REQUIRED_CODE
        ), f"{key}: {response.status_code} {response.text}"


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_route_added_after_start_up_is_refused_without_being_listed(api: ApiTestApp) -> None:
    @api.app.post("/api/v1/added-later")
    def _added_later() -> dict[str, bool]:
        return {"ok": True}

    response = api.request("POST", "/added-later", token=_unaccepted_admin(api))

    _assert_terms_refusal(response)


@pytest.mark.integration
def test_every_exempt_entry_names_a_real_mutating_route(api: ApiTestApp) -> None:
    """A stale entry would exempt nothing and stop proving anything."""
    real = {(key.method, key.path) for key in mutating_routes_with_endpoints(api.app)}

    assert real >= EXEMPT_ROUTES


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_the_exempt_accept_route_stays_open_to_a_user_who_has_not_accepted(
    api: ApiTestApp,
) -> None:
    assert _accept(api, _unaccepted_admin(api), FIRST).status_code == 200


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_reads_stay_open_to_a_user_who_has_not_accepted(api: ApiTestApp) -> None:
    token = _unaccepted_admin(api)

    for path in ("/auth/me", "/auth/terms", "/registry/properties", "/catalogue/search?q=a"):
        response = api.get(path, token=token)
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_an_anonymous_write_is_still_a_401_not_a_terms_refusal(api: ApiTestApp) -> None:
    response = api.post("/registry/properties", json={})

    assert response.status_code == 401, response.text
    assert "code" not in response.json()


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_bad_token_on_a_write_is_still_a_401(api: ApiTestApp) -> None:
    response = api.post("/registry/properties", token="not-a-token", json={})

    assert response.status_code == 401, response.text
    assert "code" not in response.json()


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_the_gate_resolves_no_caller_on_a_read(api: ApiTestApp) -> None:
    """A public read that never looked at the credential before must not start to fail on a bad
    one."""

    @api.app.get("/api/v1/open-read")
    def _open_read() -> dict[str, bool]:
        return {"ok": True}

    response = api.get("/open-read", token="not-a-token")

    assert response.status_code == 200, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_suspended_user_gets_the_permission_refusal_not_the_terms_one(
    api: ApiTestApp,
) -> None:
    """A suspended account holds only the anonymous permissions. Its write is refused for that
    reason, and the terms refusal would hide it."""
    subject = _subject()
    token = api.token_for_role(subject=subject, role=Role.ADMINISTRATOR, accept_terms=False)
    _user_for(api, subject).status = "suspended"
    api.session.flush()

    response = _write(api, token)

    assert response.status_code == 403, response.text
    assert "code" not in response.json()


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_user_without_the_permission_who_accepted_gets_the_permission_refusal(
    api: ApiTestApp,
) -> None:
    """Accepting the terms grants no right to write: the permission check still runs."""
    token = api.token_for_role(subject=_subject(), role=Role.OBSERVER, replace_roles=True)

    response = _write(api, token)

    assert response.status_code == 403, response.text
    assert "code" not in response.json()
    assert Permission.REGISTRY_MANAGE.value not in response.text


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_write_verifies_the_token_once_for_the_gate_and_the_route(api: ApiTestApp) -> None:
    """The gate resolves the caller on a write, and the route's own principal reuses that result
    rather than verifying the token and resolving the identity again."""
    real = api.app.dependency_overrides[get_token_verifier]()
    verified: list[str] = []

    class _CountingVerifier:
        def verify(self, token: str) -> Any:
            verified.append(token)
            return real.verify(token)

    api.app.dependency_overrides[get_token_verifier] = lambda: _CountingVerifier()
    token = api.token(subject=_subject())

    api.post(_ACCEPT_PATH, token=token, json={"version": FIRST})

    assert verified == [token]
