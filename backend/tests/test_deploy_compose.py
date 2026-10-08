"""Shape of the deployable stack (NFR-41): the services, their startup order,
which credential each one holds, and that `.env.example` documents every
variable compose reads.

Pure parsing of `deploy/compose.yml`, the Dockerfiles and `.env.example`, so
none of it needs Docker. Whether the stack actually comes up is checked by
running it, not here.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.yml"
ENV_EXAMPLE = REPO_ROOT / "deploy" / ".env.example"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = REPO_ROOT / "frontend" / "Dockerfile"
CADDYFILE = REPO_ROOT / "deploy" / "caddy" / "Caddyfile"

_conftest_spec = importlib.util.spec_from_file_location(
    "_test_deploy_compose_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
compose_config = _conftest.compose_config

# Not `ApiSettings(...)`, which would also read every other `NPTC_*` variable in the
# developer's shell.
_hermetic_spec = importlib.util.spec_from_file_location(
    "_test_deploy_compose_hermetic_settings",
    Path(__file__).parent / "hermetic_settings_support.py",
)
assert _hermetic_spec is not None and _hermetic_spec.loader is not None
_hermetic = importlib.util.module_from_spec(_hermetic_spec)
_hermetic_spec.loader.exec_module(_hermetic)
hermetic_api_settings = _hermetic.hermetic_api_settings

_ENV_LINE_RE = re.compile(r"^(#?)\s*([A-Z][A-Z0-9_]*)=(.*)$")
_COMPOSE_VARIABLE_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")


def _services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = compose_config()["services"]
    return services


def _env_example() -> tuple[dict[str, str], set[str]]:
    """`(active values, every name mentioned, active or commented out)`."""
    active: dict[str, str] = {}
    mentioned: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match is None:
            continue
        commented, name, value = match.groups()
        mentioned.add(name)
        if not commented:
            active[name] = value
    return active, mentioned


@pytest.mark.req("NFR-41")
def test_stack_has_the_database_identity_migration_api_and_web_services() -> None:
    assert set(_services()) == {"postgres", "keycloak", "migrate", "backend", "web"}


@pytest.mark.req("NFR-41")
def test_postgres_pins_utf8_at_initialisation() -> None:
    environment = _services()["postgres"]["environment"]

    assert environment["POSTGRES_INITDB_ARGS"] == "--encoding=UTF8"


@pytest.mark.req("NFR-41")
def test_only_migrate_receives_the_owner_dsn() -> None:
    services = _services()

    assert "NPTC_MIGRATION_DATABASE_URL" in services["migrate"]["environment"]
    for name in ("backend", "web", "keycloak"):
        assert "NPTC_MIGRATION_DATABASE_URL" not in services[name].get("environment", {}), name


@pytest.mark.req("NFR-41")
def test_no_service_loads_a_whole_env_file() -> None:
    """`env_file: .env` would hand the owner DSN to every service that used it."""
    for name, service in _services().items():
        assert "env_file" not in service, name


@pytest.mark.req("NFR-41")
def test_backend_connects_as_the_least_privilege_login_to_the_compose_database() -> None:
    dsn = _services()["backend"]["environment"]["NPTC_DATABASE_URL"]

    assert dsn.startswith("postgresql+psycopg://nptc_app_login:")
    assert "@postgres:5432/" in dsn


@pytest.mark.req("FR-13")
def test_backend_reconciles_indexes_as_the_indexer_login_to_the_compose_database() -> None:
    dsn = _services()["backend"]["environment"]["NPTC_INDEXER_DATABASE_URL"]

    assert dsn.startswith("postgresql+psycopg://nptc_indexer:")
    assert "@postgres:5432/" in dsn


@pytest.mark.req("FR-13")
def test_migrate_receives_the_indexer_password_and_backend_does_not() -> None:
    """The backend gets the DSN built from the password, never the bare variable that
    `provision_login` reads, so the rotation input stays with the one-shot service."""
    services = _services()

    assert "NPTC_INDEXER_DB_PASSWORD" in services["migrate"]["environment"]
    assert "NPTC_INDEXER_DB_PASSWORD" not in services["backend"]["environment"]


@pytest.mark.req("NFR-41")
def test_backend_reads_signing_keys_from_the_internal_keycloak_address() -> None:
    """The issuer stays browser-facing while the API skips discovery."""
    environment = _services()["backend"]["environment"]

    assert environment["NPTC_JWKS_URL"].startswith("http://keycloak:8080/realms/nptc/")
    assert "localhost" in environment["NPTC_OIDC_ISSUER"]


@pytest.mark.req("NFR-24")
@pytest.mark.parametrize("web_address", ["172.18.0.4", "172.31.255.2", "192.168.16.3", "10.1.2.3"])
def test_backend_trusts_the_forwarded_address_of_every_network_docker_assigns(
    web_address: str,
) -> None:
    """Without it the limiter sees Caddy as the only caller and every visitor shares one
    budget. Safe only because `backend` publishes no port, which another test pins."""
    entry = _services()["backend"]["environment"]["NPTC_TRUSTED_PROXIES"]
    default = re.fullmatch(r"\$\{NPTC_TRUSTED_PROXIES-(.*)\}", entry)
    assert default is not None, entry

    trusted = hermetic_api_settings(trusted_proxies=default.group(1)).trusted_proxies

    assert any(ipaddress.ip_address(web_address) in network for network in trusted)
    assert not any(ipaddress.ip_address("203.0.113.7") in network for network in trusted)


@pytest.mark.req("NFR-41")
def test_startup_order_is_postgres_then_migrate_then_backend_then_web() -> None:
    services = _services()

    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    backend_deps = services["backend"]["depends_on"]
    assert backend_deps["migrate"]["condition"] == "service_completed_successfully"
    assert backend_deps["keycloak"]["condition"] == "service_healthy"
    assert services["web"]["depends_on"]["backend"]["condition"] == "service_healthy"


@pytest.mark.req("NFR-41")
def test_migrate_applies_migrations_before_provisioning_the_login() -> None:
    migrate = _services()["migrate"]
    script = " ".join(migrate["command"])

    assert script.index("alembic upgrade head") < script.index("nptc.db.provision_login")
    assert migrate["restart"] == "no"


@pytest.mark.req("NFR-41")
def test_only_the_web_service_publishes_an_application_port() -> None:
    services = _services()

    assert "ports" not in services["backend"]
    assert "ports" not in services["migrate"]
    assert services["web"]["ports"] == ["${NPTC_WEB_PORT:-8081}:80"]


@pytest.mark.req("NFR-41")
def test_web_bakes_the_oidc_settings_into_the_build() -> None:
    args = _services()["web"]["build"]["args"]

    assert set(args) == {"VITE_OIDC_ISSUER", "VITE_OIDC_CLIENT_ID"}


@pytest.mark.req("NFR-41")
def test_every_variable_compose_reads_is_documented_in_env_example() -> None:
    _, documented = _env_example()
    used = set(_COMPOSE_VARIABLE_RE.findall(COMPOSE_FILE.read_text(encoding="utf-8")))

    assert used - documented == set()


@pytest.mark.req("NFR-41")
def test_env_example_frontend_origin_matches_the_published_web_port() -> None:
    """One value feeds both the API's CORS allow-list and the Keycloak redirect
    URIs, so a port that drifts from `web`'s breaks sign-in with no error
    naming either."""
    active, _ = _env_example()

    assert active["NPTC_FRONTEND_BASE_URL"] == f"http://localhost:{active['NPTC_WEB_PORT']}"


@pytest.mark.req("NFR-41")
@pytest.mark.parametrize(
    "dockerfile", [BACKEND_DOCKERFILE, FRONTEND_DOCKERFILE], ids=lambda p: p.parent.name
)
def test_images_do_not_run_as_root(dockerfile: Path) -> None:
    users = re.findall(r"^USER\s+(\S+)", dockerfile.read_text(encoding="utf-8"), re.M)

    assert users, f"{dockerfile.parent.name}/Dockerfile never drops root"
    assert users[-1] not in {"root", "0"}


@pytest.mark.req("NFR-41")
def test_caddy_blocks_cross_site_framing_but_allows_the_spas_own_silent_renewal_frame() -> None:
    """`silentAuthorize` loads /auth/callback in a hidden same-origin iframe, so
    `DENY` would break token renewal and step-up with no server-side error."""
    headers = CADDYFILE.read_text(encoding="utf-8")

    assert re.search(r"^\s*X-Frame-Options\s+SAMEORIGIN\s*$", headers, re.M)
    assert not re.search(r"X-Frame-Options\s+DENY", headers)


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_test_database_runs_with_the_encoding_compose_pins(db: Connection) -> None:
    assert db.execute(text("SHOW server_encoding")).scalar_one() == "UTF8"
