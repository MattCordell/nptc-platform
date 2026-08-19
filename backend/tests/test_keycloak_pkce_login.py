"""The authorisation code + PKCE round trip, driven against a real
Keycloak (issue #41, NFR-01, NFR-02).

This is the test ADR-0014 said belonged here: "Exercising a full PKCE
login as the realm's proof - no frontend client application exists until
#41 ... a login flow test belongs to #41."

Two of issue #41's acceptance criteria are only provable against a real
authorisation server, because the checks belong to *it*, not to us:

- a ``code_verifier`` that does not match the original ``code_challenge``
  is refused by the token endpoint;
- replaying an authorisation code that has already been exchanged fails.

Asserting those against a stub would prove only that the stub was written
to agree with the assertion. Everything here therefore runs against the
same pinned image ``deploy/compose.yml`` ships (NFR-39).

No browser is involved: the flow is driven with ``httpx`` by posting the
realm's own login form, which is what a browser would do. The SPA's half
of the same flow is tested in ``frontend/src/auth/*.test.ts``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
import re
import secrets
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from testcontainers.core.container import DockerContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
REALM_DIR = REPO_ROOT / "deploy" / "keycloak" / "realm"

_conftest_spec = importlib.util.spec_from_file_location(
    "keycloak_pkce_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["keycloak_pkce_conftest"] = _conftest
_conftest_spec.loader.exec_module(_conftest)
image_from_compose = _conftest.image_from_compose

CLIENT_ID = "nptc-frontend"
FRONTEND_BASE_URL = "http://frontend.test"
REDIRECT_URI = f"{FRONTEND_BASE_URL}/auth/callback"
#: Test-only, and never a real credential (NFR-26): this account exists
#: for the lifetime of one container that is thrown away afterwards.
TEST_USERNAME = "pkce-test-user"
TEST_PASSWORD = "pkce-test-only-not-a-real-secret"
ADMIN_PASSWORD = "nptc-realm-test-only-not-a-real-secret"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    """A ``(verifier, challenge)`` pair, S256 - the same construction the
    SPA's ``frontend/src/auth/pkce.ts`` performs in the browser."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass
class Realm:
    base_url: str
    discovery: dict[str, object]

    @property
    def authorization_endpoint(self) -> str:
        return str(self.discovery["authorization_endpoint"])

    @property
    def token_endpoint(self) -> str:
        return str(self.discovery["token_endpoint"])

    @property
    def end_session_endpoint(self) -> str:
        return str(self.discovery["end_session_endpoint"])


def _wait_for_discovery(base_url: str, attempts: int = 60, delay: float = 2.0) -> httpx.Response:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            response = httpx.get(
                f"{base_url}/realms/nptc/.well-known/openid-configuration", timeout=5
            )
            if response.status_code == 200:
                return response
        except httpx.TransportError as error:
            last_error = error
        time.sleep(delay)
    raise TimeoutError(f"Keycloak did not serve the nptc realm in time: {last_error}")


def _admin_token(base_url: str) -> str:
    response = httpx.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "grant_type": "password",
        },
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _create_test_user(base_url: str) -> None:
    token = _admin_token(base_url)
    response = httpx.post(
        f"{base_url}/admin/realms/nptc/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": TEST_USERNAME,
            "email": f"{TEST_USERNAME}@example.test",
            "emailVerified": True,
            "enabled": True,
            "credentials": [{"type": "password", "value": TEST_PASSWORD, "temporary": False}],
        },
        timeout=30,
    )
    assert response.status_code in (201, 409), response.text


@pytest.fixture(scope="module")
def realm() -> Iterator[Realm]:
    """One Keycloak per module: starting it costs tens of seconds, and
    every test here is read-only with respect to realm configuration."""
    container = (
        DockerContainer(image_from_compose("keycloak"))
        .with_exposed_ports(8080)
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", ADMIN_PASSWORD)
        .with_env("NPTC_FRONTEND_BASE_URL", FRONTEND_BASE_URL)
        .with_volume_mapping(str(REALM_DIR), "/opt/keycloak/data/import", mode="ro")
        .with_command("start-dev --import-realm")
    )
    with container:
        base_url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8080)}"
        discovery = _wait_for_discovery(base_url).json()
        _create_test_user(base_url)
        yield Realm(base_url=base_url, discovery=discovery)


def _authorize_params(challenge: str, *, state: str, **extra: str) -> dict[str, str]:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(extra)
    return params


class CookieJar(dict[str, str]):
    """Cookies carried by hand across the flow, rather than by httpx.

    Keycloak sets ``KC_RESTART`` and its SSO session cookies with
    ``SameSite=None``, which obliges it to add ``Secure`` - and an
    ordinary cookie jar then refuses to send them back over the
    plain-http localhost the test container serves. httpx stores them and
    silently omits them, so Keycloak answers the login POST with "Restart
    login cookie not found" and treats every later request as having no
    SSO session at all.

    Replaying them verbatim is what a browser against an https deployment
    would do. This substitutes for TLS, not for any part of the flow
    under test.
    """

    def collect(self, response: httpx.Response) -> CookieJar:
        for hop in [*response.history, response]:
            for raw in hop.headers.get_list("set-cookie"):
                name_value = raw.split(";", 1)[0]
                if "=" in name_value:
                    name, value = name_value.split("=", 1)
                    if value:
                        self[name.strip()] = value.strip()
        return self

    @property
    def header(self) -> dict[str, str]:
        return {"Cookie": "; ".join(f"{name}={value}" for name, value in self.items())}


def _login(
    client: httpx.Client,
    realm: Realm,
    challenge: str,
    *,
    state: str,
    jar: CookieJar | None = None,
) -> httpx.Response:
    """Drives the realm's own login page and returns the redirect response
    carrying the ``code`` - what a browser does, without a browser.

    ``follow_redirects=False`` on the form post is essential: the redirect
    target *is* the result, and following it would chase
    ``http://frontend.test``, which does not exist.
    """
    jar = jar if jar is not None else CookieJar()
    page = client.get(
        realm.authorization_endpoint, params=_authorize_params(challenge, state=state)
    )
    page.raise_for_status()
    jar.collect(page)

    form_action = re.search(r'<form id="kc-form-login"[^>]*action="([^"]+)"', page.text)
    assert form_action is not None, "Keycloak login page had no login form"

    submitted = client.post(
        html.unescape(form_action.group(1)),
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        follow_redirects=False,
        headers=jar.header,
    )
    jar.collect(submitted)
    return submitted


def _code_from(response: httpx.Response, *, state: str) -> str:
    assert response.status_code in (302, 303), response.text
    location = response.headers["location"]
    assert location.startswith(REDIRECT_URI), location
    query = parse_qs(urlsplit(location).query)
    assert query.get("state") == [state], f"state not echoed back: {location}"
    assert "code" in query, f"no authorisation code in {location}"
    return query["code"][0]


def _authorization_code(realm: Realm, challenge: str, *, state: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        submitted = _login(client, realm, challenge, state=state)
    return _code_from(submitted, state=state)


def _exchange(realm: Realm, code: str, verifier: str) -> httpx.Response:
    return httpx.post(
        realm.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )


@pytest.mark.integration
@pytest.mark.req("NFR-01")
def test_pkce_authorisation_code_flow_yields_an_access_token(realm: Realm) -> None:
    """The NFR-01 happy path end to end: no client secret is sent at any
    point, and the exchange still succeeds - which is exactly what PKCE
    buys a public client."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    code = _authorization_code(realm, challenge, state=state)
    response = _exchange(realm, code, verifier)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"].lower() == "bearer"
    assert "access_token" in body
    assert "id_token" in body
    assert "client_secret" not in response.request.content.decode()


@pytest.mark.integration
@pytest.mark.req("NFR-01")
def test_mismatched_code_verifier_is_refused(realm: Realm) -> None:
    """Acceptance criterion 3. An attacker who intercepts the code but not
    the verifier cannot complete the exchange - the entire threat PKCE
    exists to close."""
    _verifier, challenge = _pkce_pair()
    wrong_verifier, _challenge = _pkce_pair()

    code = _authorization_code(realm, challenge, state=secrets.token_urlsafe(16))
    response = _exchange(realm, code, wrong_verifier)

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.integration
@pytest.mark.req("NFR-01")
def test_replaying_an_authorisation_code_fails(realm: Realm) -> None:
    """Acceptance criterion 4. The first exchange succeeds; the second,
    with the identical code *and* the correct verifier, must not."""
    verifier, challenge = _pkce_pair()

    code = _authorization_code(realm, challenge, state=secrets.token_urlsafe(16))
    first = _exchange(realm, code, verifier)
    second = _exchange(realm, code, verifier)

    assert first.status_code == 200, first.text
    assert second.status_code == 400, second.text
    assert second.json()["error"] == "invalid_grant"


@pytest.mark.integration
@pytest.mark.req("NFR-01")
def test_authorisation_request_for_an_unregistered_redirect_uri_is_refused(realm: Realm) -> None:
    """The realm's ``redirectUris`` allowlist is load-bearing, not
    decorative: without it a stolen code could be redirected anywhere."""
    _verifier, challenge = _pkce_pair()

    response = httpx.get(
        realm.authorization_endpoint,
        params=_authorize_params(
            challenge,
            state=secrets.token_urlsafe(16),
            redirect_uri="http://attacker.example/auth/callback",
        ),
        follow_redirects=False,
        timeout=30,
    )

    # Keycloak renders its own error page rather than redirecting to an
    # unregistered URI - the one thing it must never do is honour it.
    assert response.status_code == 400, response.status_code
    assert "attacker.example" not in response.headers.get("location", "")


@pytest.mark.integration
@pytest.mark.req("NFR-02")
def test_realm_authenticates_against_its_own_local_user_database(realm: Realm) -> None:
    """NFR-02 observed rather than merely configured: the login below
    succeeds for a user held in Keycloak's own database, with no external
    identity provider registered at all."""
    assert realm.discovery["issuer"] == f"{realm.base_url}/realms/nptc"

    token = _admin_token(realm.base_url)
    providers = httpx.get(
        f"{realm.base_url}/admin/realms/nptc/identity-provider/instances",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    providers.raise_for_status()
    assert providers.json() == []

    verifier, challenge = _pkce_pair()
    code = _authorization_code(realm, challenge, state=secrets.token_urlsafe(16))
    assert _exchange(realm, code, verifier).status_code == 200


@pytest.mark.integration
@pytest.mark.req("NFR-01")
def test_logout_ends_the_session_so_the_next_login_re_authenticates(realm: Realm) -> None:
    """Acceptance criterion 5, the half that is genuinely enforceable
    server-side.

    After RP-initiated logout, a ``prompt=none`` renewal - the SPA's
    silent re-auth (ADR-0021) - must fail with ``login_required`` rather
    than quietly minting a fresh token from a still-live SSO session.

    Note what this does *not* claim: an access token issued before logout
    stays valid until its own ``exp`` (the realm's ``accessTokenLifespan``
    is 300s). ADR-0021 records that residual window as accepted; closing
    it would need a BFF or per-request introspection.
    """
    verifier, challenge = _pkce_pair()
    state = "logout-flow"
    jar = CookieJar()

    with httpx.Client(follow_redirects=True, timeout=30) as client:
        submitted = _login(client, realm, challenge, state=state, jar=jar)
        tokens = _exchange(realm, _code_from(submitted, state=state), verifier).json()

        # Silent renewal works while the SSO session is alive - without
        # this half, the assertion after logout would pass even if
        # prompt=none had never worked in the first place.
        before = client.get(
            realm.authorization_endpoint,
            params=_authorize_params(_pkce_pair()[1], state="renew-before", prompt="none"),
            follow_redirects=False,
            headers=jar.header,
        )
        assert "code=" in before.headers.get("location", ""), before.headers.get("location")

        logout = client.get(
            realm.end_session_endpoint,
            params={
                "id_token_hint": tokens["id_token"],
                "post_logout_redirect_uri": FRONTEND_BASE_URL,
            },
            follow_redirects=False,
            headers=jar.header,
        )
        assert logout.status_code in (302, 303), logout.text
        jar.collect(logout)

        after = client.get(
            realm.authorization_endpoint,
            params=_authorize_params(_pkce_pair()[1], state="renew-after", prompt="none"),
            follow_redirects=False,
            headers=jar.header,
        )

    location = after.headers.get("location", "")
    assert "error=login_required" in location, location
    assert "code=" not in location, location
