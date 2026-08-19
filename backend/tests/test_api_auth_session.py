"""`GET /api/v1/auth/me` and the auth dependency chain behind it over real
HTTP (issue #41, NFR-01, NFR-07, NFR-20, FR-44).

These are the first tests in the repo to exercise `nptc.api`'s production
app rather than a throwaway one built in a test module. Every case below
runs the real `TokenVerifier` -> `resolve_user_for_claims` ->
`principal_for` chain; only the database connection and the IdP's network
location are substituted.

Marked `integration`: identity resolution writes `app_user`/
`user_identity`/`audit_event` rows, so a real PostgreSQL is required
(NFR-39 - not an in-memory substitute).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from nptc.auth.permissions import Permission, Role

# Registered in sys.modules before exec_module - see
# test_authz_negative_http.py for why @dataclass requires it.
_support_spec = importlib.util.spec_from_file_location(
    "api_app_support", Path(__file__).parent / "api_app_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
sys.modules["api_app_support"] = _support
_support_spec.loader.exec_module(_support)

build_api_test_app = _support.build_api_test_app
ApiTestApp = _support.ApiTestApp


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


# --- anonymous ------------------------------------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_anonymous_caller_gets_the_public_surface_not_a_401(api: ApiTestApp) -> None:
    """A signed-out visitor asking "who am I?" is an ordinary request, not
    a credential failure - the SPA makes it on every cold load."""
    response = api.get("/auth/me")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is False
    assert body["user"] is None
    assert body["roles"] == []
    # PRD Section 4.1: the anonymous visitor still has the public read
    # surface, so an empty permission list here would be wrong.
    assert Permission.CATALOGUE_BROWSE.value in body["permissions"]
    assert "WWW-Authenticate" not in response.headers


# --- a valid token --------------------------------------------------------


@pytest.mark.req("NFR-01")
@pytest.mark.integration
def test_valid_token_resolves_to_an_internal_user(api: ApiTestApp) -> None:
    """The NFR-01 round trip observed end to end: a token minted by the
    IdP arrives as a Bearer credential and comes back as a resolved
    internal account with derived permissions."""
    response = api.get("/auth/me", token=api.token(subject="sub-happy-path"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] is not None
    # PRD Section 4.3: a newly registered user *is* Provisional.
    assert body["roles"] == [Role.PROVISIONAL.value]


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_response_never_carries_the_internal_user_id(api: ApiTestApp) -> None:
    """The defect NFR-04 exists to prevent: `app_user.id` reaching a
    client. `UserRef` excludes it structurally, and this asserts the
    endpoint did not reintroduce it."""
    response = api.get("/auth/me", token=api.token(subject="sub-no-internal-id"))

    assert response.status_code == 200, response.text
    user_id = api.session.execute(
        text("SELECT id FROM app_user ORDER BY created_at DESC LIMIT 1")
    ).scalar_one()
    assert str(user_id) not in response.text
    assert "id" not in response.json()["user"]


# --- refusals: the principal failure modes --------------------------------


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_expired_token_is_refused_with_401_and_a_challenge(api: ApiTestApp) -> None:
    response = api.get("/auth/me", token=api.token(subject="sub-expired", expires_in=-60))

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    # The diagnostic message belongs in the log, not the body.
    assert "exp" not in response.text.lower()


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_token_for_another_audience_is_refused(api: ApiTestApp) -> None:
    response = api.get(
        "/auth/me", token=api.token(subject="sub-wrong-aud", audience="some-other-api")
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_token_from_another_issuer_is_refused(api: ApiTestApp) -> None:
    response = api.get(
        "/auth/me", token=api.token(subject="sub-wrong-iss", issuer="https://evil.example")
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_garbage_token_is_refused_rather_than_treated_as_anonymous(api: ApiTestApp) -> None:
    """The distinction that matters: presenting a bad credential is a 401,
    not a silent downgrade to the anonymous view. Degrading would make a
    forged token indistinguishable from no token in every log built from
    the result."""
    response = api.get("/auth/me", token="not-a-jwt")

    assert response.status_code == 401, response.text
    # Not merely "not authenticated" - the anonymous *body* must not be
    # served at all, or a client could not tell a forged token from none.
    assert "authenticated" not in response.json()


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_non_bearer_authorization_header_is_refused(api: ApiTestApp) -> None:
    """A present-but-unreadable credential is refused, not quietly ignored
    in favour of the public view."""
    response = api.client.get(
        "/api/v1/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}
    )

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_empty_bearer_token_is_refused(api: ApiTestApp) -> None:
    response = api.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer   "})

    assert response.status_code == 401, response.text


# --- MFA (NFR-06) ---------------------------------------------------------


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_acr_claim_drives_mfa_satisfied(api: ApiTestApp) -> None:
    """`mfa_satisfied` is derived from the verified `acr` claim, never
    asserted by the caller."""
    without = api.get("/auth/me", token=api.token(subject="sub-acr-none"))
    with_mfa = api.get("/auth/me", token=api.token(subject="sub-acr-2", extra_claims={"acr": "2"}))

    assert without.json()["mfa_satisfied"] is False
    assert with_mfa.json()["mfa_satisfied"] is True


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_mfa_acr_values_setting_is_actually_consulted(app_db: Connection) -> None:
    """A regression guard for a wiring bug, not just for the behaviour.

    `current_principal` used to call `get_auth_settings()` directly rather
    than taking it via `Depends`, so `app.dependency_overrides` never
    applied and the harness's `mfa_acr_values=` argument was inert - the
    MFA tests passed only because the process default happened to be
    `{"2"}`. Configuring a level the token does *not* carry is what proves
    the setting is read: with the bug, `acr: "2"` would still satisfy MFA
    here.
    """
    for api in build_api_test_app(app_db, mfa_acr_values=frozenset({"3"})):
        response = api.get(
            "/auth/me", token=api.token(subject="sub-acr-setting", extra_claims={"acr": "2"})
        )

        assert response.status_code == 200, response.text
        assert response.json()["mfa_satisfied"] is False
