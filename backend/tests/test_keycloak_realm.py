"""Realm-as-code acceptance criteria for issue #40 (NFR-03).

Offline group: fast assertions over the committed
deploy/keycloak/realm/nptc-realm.json and the compose.yml bind mount that
imports it - no Docker needed, runs in the `python` job's coverage gate.

Integration group (@pytest.mark.integration): starts the pinned Keycloak
image for real and asserts against its discovery document and admin API -
"not an in-memory substitute" (NFR-39) applied to Keycloak, not just
Postgres. See docs/adr/0014-keycloak-realm-as-code.md for the design this
proves.

`image_from_compose` is imported from conftest.py by file path, not by
`import conftest`: backend/tests has no `__init__.py` and pytest runs with
`--import-mode=importlib` (CLAUDE.md's testing conventions), under which a
bare `import conftest` from a sibling test module raises
`ModuleNotFoundError` - pytest loads conftest.py through its own mechanism,
which does not register it under an importable `conftest` name.
"""

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from testcontainers.core.container import DockerContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
REALM_DIR = DEPLOY_DIR / "keycloak" / "realm"
REALM_FILE = REALM_DIR / "nptc-realm.json"

_conftest_spec = importlib.util.spec_from_file_location(
    "_test_keycloak_realm_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
image_from_compose = _conftest.image_from_compose
compose_config = _conftest.compose_config

#: Keys that must never appear anywhere in a committed realm file (NFR-26,
#: NFR-35). Not merely "must be empty" - a maintainer re-exporting the realm
#: from a running Keycloak brings these keys back populated, and their mere
#: presence at all is the signal something was exported rather than
#: hand-authored, regardless of whether a given value happens to be empty.
BANNED_KEYS = {"secret", "credentials", "password", "adminpassword", "clientsecret"}
#: A JWT (three base64url segments) or a long hex/base64 run - the shape a
#: real bearer token or generated secret takes, as opposed to the short
#: identifiers (protocol names, mapper types) this file actually contains.
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_LONG_OPAQUE_RE = re.compile(r"^[A-Za-z0-9+/_-]{32,}={0,2}$")


@pytest.fixture(scope="module")
def realm() -> dict[str, Any]:
    # json.loads, not yaml.safe_load: the realm file is authored as JSON, and
    # YAML 1.1 is a superset in the wrong direction here - it accepts
    # trailing commas in flow collections and silently last-wins on
    # duplicate keys, both of which Keycloak's own JSON parser rejects or
    # handles differently. A malformed realm file must fail this test the
    # same way it would fail import, not merely the way YAML tolerates it.
    return dict(json.loads(REALM_FILE.read_text(encoding="utf-8")))


def _iter_key_value_pairs(node: Any, parent_key: str | None = None) -> list[tuple[str, Any]]:
    """Walks the full tree, including list elements - a secret-shaped string
    living inside an array (redirectUris, webOrigins, defaultClientScopes, or
    any protocolMapper config's value list) must be reachable too, not just
    dict values. List scalars are yielded under their parent key, since a
    list has no key of its own to report."""
    pairs: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            pairs.append((key, value))
            pairs.extend(_iter_key_value_pairs(value, parent_key=key))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict | list):
                pairs.extend(_iter_key_value_pairs(item, parent_key=parent_key))
            elif parent_key is not None:
                pairs.append((parent_key, item))
    return pairs


@pytest.mark.req("NFR-03")
def test_compose_bind_mount_points_at_the_file_under_test() -> None:
    """Parses compose.yml rather than hardcoding the path twice, so a moved
    realm directory breaks this test instead of silently diverging from
    what compose actually imports."""
    volumes = compose_config()["services"]["keycloak"]["volumes"]
    import_mounts = [v for v in volumes if v.split(":")[1] == "/opt/keycloak/data/import"]

    assert len(import_mounts) == 1
    source = import_mounts[0].split(":")[0]
    assert (DEPLOY_DIR / source).resolve() == REALM_DIR.resolve()


@pytest.mark.req("NFR-03")
def test_compose_imports_the_realm_on_startup() -> None:
    command = compose_config()["services"]["keycloak"]["command"]

    assert "--import-realm" in command


@pytest.mark.req("NFR-26")
def test_no_secret_material_anywhere_in_the_realm(realm: dict[str, Any]) -> None:
    """The principal failure mode: a maintainer re-exports the realm from a
    running Keycloak, and the export brings back a client secret and
    whatever test users they were poking at."""
    pairs = _iter_key_value_pairs(realm)

    offending_keys = {key for key, _ in pairs if key.lower() in BANNED_KEYS}
    assert offending_keys == set()

    for key, value in pairs:
        if not isinstance(value, str):
            continue
        assert not _JWT_RE.match(value), f"{key!r} looks like a bearer token: {value!r}"
        assert not _LONG_OPAQUE_RE.match(value), f"{key!r} looks like a generated secret: {value!r}"


@pytest.mark.req("NFR-26")
def test_no_users_in_the_realm(realm: dict[str, Any]) -> None:
    assert not realm.get("users")


@pytest.mark.req("NFR-02")
def test_no_external_identity_federation(realm: dict[str, Any]) -> None:
    assert realm["identityProviders"] == []
    assert not realm.get("userFederationProviders")


@pytest.mark.req("NFR-02")
def test_local_registration_is_enabled(realm: dict[str, Any]) -> None:
    assert realm["registrationAllowed"] is True
    assert realm["resetPasswordAllowed"] is True
    assert realm["loginWithEmailAllowed"] is True


def _client(realm: dict[str, Any], client_id: str) -> dict[str, Any]:
    matches = [c for c in realm["clients"] if c["clientId"] == client_id]
    assert len(matches) == 1, f"expected exactly one {client_id!r} client"
    return matches[0]


@pytest.mark.req("NFR-03")
def test_frontend_client_is_public_pkce_only(realm: dict[str, Any]) -> None:
    frontend = _client(realm, "nptc-frontend")

    assert frontend["publicClient"] is True
    assert frontend["attributes"]["pkce.code.challenge.method"] == "S256"
    assert frontend["implicitFlowEnabled"] is False
    assert frontend["directAccessGrantsEnabled"] is False
    assert frontend["serviceAccountsEnabled"] is False
    assert frontend["fullScopeAllowed"] is False


@pytest.mark.req("NFR-03")
def test_frontend_client_has_no_wildcard_host_redirect_uri(realm: dict[str, Any]) -> None:
    frontend = _client(realm, "nptc-frontend")

    for uri in frontend["redirectUris"]:
        assert "://*" not in uri, f"wildcard-host redirect URI: {uri!r}"


@pytest.mark.req("NFR-07")
def test_frontend_client_carries_the_api_audience_mapper(realm: dict[str, Any]) -> None:
    """No shared `nptc-api-audience` client scope: declaring a top-level
    `clientScopes` array in this realm file was found (empirically, against
    a scratch Keycloak realm) to replace Keycloak's built-in default scopes
    wholesale rather than add to them, silently dropping `profile`/`email`/
    `web-origins` from every client that referenced them. A client-level
    protocol mapper on nptc-frontend gets the same audience claim without
    that collateral damage - see ADR-0014."""
    frontend = _client(realm, "nptc-frontend")
    mappers = [
        m
        for m in frontend.get("protocolMappers", [])
        if m["protocolMapper"] == "oidc-audience-mapper"
    ]

    assert len(mappers) == 1
    mapper = mappers[0]
    assert mapper["config"]["included.client.audience"] == "nptc-api"
    assert mapper["config"]["access.token.claim"] == "true"


@pytest.mark.req("NFR-03")
def test_api_client_is_audience_only(realm: dict[str, Any]) -> None:
    api = _client(realm, "nptc-api")

    assert api["publicClient"] is True
    assert api["standardFlowEnabled"] is False
    assert api["implicitFlowEnabled"] is False
    assert api["directAccessGrantsEnabled"] is False
    assert api["serviceAccountsEnabled"] is False
    assert api["redirectUris"] == []


@pytest.mark.req("FR-44")
def test_no_application_roles_declared_in_the_realm(realm: dict[str, Any]) -> None:
    """Decision 1 (docs/adr/0014-keycloak-realm-as-code.md): Keycloak
    authenticates, the platform authorises (NFR-07/FR-44). Application
    roles belong in the platform DB (#42/#44), never here - so this file
    must never declare a `roles`/`realmRoles` block at all."""
    assert "roles" not in realm
    assert "realmRoles" not in realm


def _wait_for_discovery_document(
    base_url: str, attempts: int = 60, delay: float = 2.0
) -> httpx.Response:
    """Polls the realm's own discovery document rather than a log line or
    the /health/ready management endpoint: a log message is fragile across
    Keycloak versions, and this directly tests the acceptance criterion
    ("--import-realm" actually imported nptc, not merely that the process
    started)."""
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
    raise TimeoutError(
        f"Keycloak did not serve the nptc realm discovery document in time: {last_error}"
    )


@pytest.mark.integration
@pytest.mark.req("NFR-03")
def test_keycloak_imports_the_realm_and_serves_discovery() -> None:
    image = image_from_compose("keycloak")
    frontend_base_url = "http://frontend.test"

    container = (
        DockerContainer(image)
        .with_exposed_ports(8080)
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", "nptc-realm-test-only-not-a-real-secret")
        .with_env("NPTC_FRONTEND_BASE_URL", frontend_base_url)
        .with_volume_mapping(str(REALM_DIR), "/opt/keycloak/data/import", mode="ro")
        .with_command("start-dev --import-realm")
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8080)
        base_url = f"http://{host}:{port}"

        discovery = _wait_for_discovery_document(base_url)
        body = discovery.json()

        assert body["issuer"] == f"{base_url}/realms/nptc"
        assert "S256" in body["code_challenge_methods_supported"]

        admin_token_response = httpx.post(
            f"{base_url}/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "username": "admin",
                "password": "nptc-realm-test-only-not-a-real-secret",
                "grant_type": "password",
            },
            timeout=30,
        )
        admin_token_response.raise_for_status()
        admin_token = admin_token_response.json()["access_token"]

        clients_response = httpx.get(
            f"{base_url}/admin/realms/nptc/clients",
            params={"clientId": "nptc-frontend"},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=30,
        )
        clients_response.raise_for_status()
        clients = clients_response.json()

        assert len(clients) == 1
        client = clients[0]
        assert client["publicClient"] is True
        assert client["attributes"]["pkce.code.challenge.method"] == "S256"

        # ${NPTC_FRONTEND_BASE_URL} is the realm's only placeholder and the
        # one thing the offline group cannot check - if Keycloak's ${VAR}
        # substitution doesn't fire (wrong syntax, unset var, a version
        # change), the imported client keeps the literal placeholder text
        # and #41's login breaks with an "Invalid redirect_uri" this test
        # would otherwise still call green.
        assert client["rootUrl"] == frontend_base_url
        assert client["redirectUris"] == [f"{frontend_base_url}/*"]
        assert client["webOrigins"] == [frontend_base_url]
