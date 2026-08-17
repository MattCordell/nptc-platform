"""A real Keycloak-minted token verified end-to-end (issue #43, NFR-07).

`test_auth_token_verification.py` proves `TokenVerifier` against tokens
this test suite signs itself; this file proves it against a token the
committed realm's own audience mapper, issuer and JWKS actually produced -
following the admin-API pattern `test_keycloak_realm.py` already
established (`image_from_compose`, `_wait_for_discovery_document`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from nptc.auth.errors import TokenAudienceError
from nptc.auth.tokens import TokenVerifier
from nptc.settings import AuthSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
REALM_DIR = REPO_ROOT / "deploy" / "keycloak" / "realm"

_conftest_spec = importlib.util.spec_from_file_location(
    "_test_keycloak_jwt_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
image_from_compose = _conftest.image_from_compose

_realm_spec = importlib.util.spec_from_file_location(
    "_test_keycloak_jwt_realm_helpers", Path(__file__).parent / "test_keycloak_realm.py"
)
assert _realm_spec is not None and _realm_spec.loader is not None
_realm_helpers = importlib.util.module_from_spec(_realm_spec)
_realm_spec.loader.exec_module(_realm_helpers)
_wait_for_discovery_document = _realm_helpers._wait_for_discovery_document

_ADMIN_PASSWORD = "nptc-realm-test-only-not-a-real-secret"
_TEST_USER_PASSWORD = "nptc-jwt-test-only-not-a-real-secret"


def _admin_token(base_url: str) -> str:
    response = httpx.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": _ADMIN_PASSWORD,
            "grant_type": "password",
        },
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _enable_direct_access_grants_for_password_login(base_url: str, admin_token: str) -> None:
    """A test-only affordance for minting a token without a browser -
    flips `directAccessGrantsEnabled` on **this disposable container's**
    running copy of the `nptc-frontend` client only. The *committed* realm
    file keeps this flag off (see test_keycloak_realm.py's offline
    `test_frontend_client_is_public_pkce_only`) - that flow is exactly
    what #41's PKCE-only login exists to require, and this mutation never
    touches the file, only this throwaway container's runtime state."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    clients_response = httpx.get(
        f"{base_url}/admin/realms/nptc/clients",
        params={"clientId": "nptc-frontend"},
        headers=headers,
        timeout=30,
    )
    clients_response.raise_for_status()
    client = clients_response.json()[0]
    client["directAccessGrantsEnabled"] = True
    update_response = httpx.put(
        f"{base_url}/admin/realms/nptc/clients/{client['id']}",
        json=client,
        headers=headers,
        timeout=30,
    )
    update_response.raise_for_status()


def _create_test_user(base_url: str, admin_token: str, *, username: str) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}
    create_response = httpx.post(
        f"{base_url}/admin/realms/nptc/users",
        json={
            "username": username,
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": _TEST_USER_PASSWORD, "temporary": False}],
        },
        headers=headers,
        timeout=30,
    )
    create_response.raise_for_status()


def _password_grant_token(base_url: str, *, username: str) -> str:
    response = httpx.post(
        f"{base_url}/realms/nptc/protocol/openid-connect/token",
        data={
            "client_id": "nptc-frontend",
            "username": username,
            "password": _TEST_USER_PASSWORD,
            "grant_type": "password",
            "scope": "openid",
        },
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


@pytest.mark.integration
@pytest.mark.req("NFR-07")
def test_a_real_keycloak_minted_token_verifies() -> None:
    image = image_from_compose("keycloak")
    frontend_base_url = "http://frontend.test"

    container = (
        DockerContainer(image)
        .with_exposed_ports(8080)
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", _ADMIN_PASSWORD)
        .with_env("NPTC_FRONTEND_BASE_URL", frontend_base_url)
        .with_volume_mapping(str(REALM_DIR), "/opt/keycloak/data/import", mode="ro")
        .with_command("start-dev --import-realm")
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8080)
        base_url = f"http://{host}:{port}"
        _wait_for_discovery_document(base_url)

        admin_token = _admin_token(base_url)
        _enable_direct_access_grants_for_password_login(base_url, admin_token)
        _create_test_user(base_url, admin_token, username="jwt-test-user")

        token = _password_grant_token(base_url, username="jwt-test-user")

        issuer = f"{base_url}/realms/nptc"
        settings = AuthSettings(oidc_issuer=issuer, oidc_audience="nptc-api")
        verifier = TokenVerifier.from_settings(settings)

        claims = verifier.verify(token)

        assert claims.issuer == issuer
        assert claims.preferred_username == "jwt-test-user"

        wrong_audience_settings = AuthSettings(oidc_issuer=issuer, oidc_audience="some-other-api")
        wrong_audience_verifier = TokenVerifier.from_settings(wrong_audience_settings)

        with pytest.raises(TokenAudienceError):
            wrong_audience_verifier.verify(token)
