"""Backend runtime configuration (issue #33).

``pydantic-settings``, not the explicit ``os.environ`` reads
``nptc_shared.terminology.config`` uses (ADR-0003): ``pydantic-settings`` is
declared as a backend-only dependency and this is its first consumer, so
there is no cross-package reason to match that module's approach here.

Two separate settings classes, not one combined ``Settings`` with both
DSNs required: ``backend/migrations/env.py`` only ever needs
``MigrationSettings``, and an operator running `alembic upgrade head`
should not be forced to also set ``NPTC_DATABASE_URL``, a variable Alembic
has no use for. Each DSN is a required field with no default, mirroring
``nptc_shared.terminology.config``'s "raise naming the variable, never
silently default" convention: a missing, empty, or whitespace-only value
fails loudly, naming the field, rather than falling back to a placeholder a
misconfigured deployment could run against for a while before anyone
notices.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


class DatabaseSettings(BaseSettings):
    """The app runtime role's DSN - ``nptc_app_login`` in tests, an
    equivalent least-privilege role in a deployment."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "database_url")


class AuditVerifySettings(BaseSettings):
    """The DSN `scripts/verify_audit_chain.py` (issue #38) falls back to when
    ``--database-url`` is not passed on the command line - see
    ``nptc.audit.verification.verify_chain``, which only ever issues
    `SELECT`s, so this can name a read-only replica or a restored backup
    rather than the app runtime's own DSN.

    Empty default, not required, unlike ``DatabaseSettings``: the CLI falls
    further back to ``NPTC_DATABASE_URL`` when this is unset (see the
    runbook), so an empty value here is a valid, common configuration -
    "no separate verification DSN is configured" - not a misconfiguration to
    reject at settings-construction time. The CLI itself is what raises,
    naming all three sources, if no DSN can be resolved at all.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    audit_verify_database_url: str = ""


class AuthSettings(BaseSettings):
    """The NFR-05 trusted-issuer allowlist controlling auto-linking (issue
    #42) - see ``nptc.auth.linking.may_auto_link`` - plus the NFR-07
    server-side JWT verification configuration (issue #43) - see
    ``nptc.auth.tokens.TokenVerifier.from_settings``.

    Empty default, not a required field: unlike the DSNs above, "no issuer
    is trusted yet" is itself a valid, safe configuration (fail closed,
    matching NFR-02's federation-off posture), not a misconfiguration to
    reject. ``oidc_issuer`` follows the same posture for the same reason:
    an empty issuer cannot construct a ``TokenVerifier`` at all (see
    ``nptc.auth.errors``), so a missing configuration refuses every token
    rather than accepting one unverified.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    # NoDecode: frozenset[str] is a "complex" type to pydantic-settings, so
    # without this it tries to JSON-decode the raw environment string
    # before any validator below ever runs - the comma-separated format
    # configuration.md documents would raise a SettingsError on every
    # non-JSON value, never reaching `_split_comma_separated`.
    trusted_issuers: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator("trusted_issuers", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    #: Empty, not required: matches the fail-closed posture above. A
    #: ``TokenVerifier`` cannot be constructed from a blank issuer, so an
    #: unconfigured deployment refuses every token instead of accepting one
    #: whose issuer was never actually checked.
    oidc_issuer: str = ""

    #: Fixed by the committed realm (deploy/keycloak/realm/nptc-realm.json's
    #: `nptc-api-audience` mapper), not by a deployment - see ADR-0014.
    oidc_audience: str = "nptc-api"

    #: Empty means "resolve via OIDC discovery" (`nptc.auth.discovery`).
    #: Setting this explicitly skips discovery entirely - for air-gapped
    #: deployments, and so the offline test suite only ever needs to stand
    #: up one local HTTP endpoint.
    jwks_url: str = ""

    jwks_cache_seconds: float = 300.0

    #: An unknown `kid` within this many seconds of the last refresh
    #: attempt is refused without an HTTP request - see
    #: ``nptc.auth.jwks.SigningKeys``.
    jwks_refresh_cooldown_seconds: float = 30.0

    #: NFR-06 (issue #44): the set of `acr` claim values that satisfy the
    #: mandatory-MFA-for-administrators requirement - see
    #: ``nptc.auth.principal.principal_for``. Matches the committed
    #: realm's ``acr.loa.map`` (``deploy/keycloak/realm/nptc-realm.json``),
    #: which maps the LoA-2 authentication flow to ``"2"``. Same
    #: ``NoDecode`` treatment as ``trusted_issuers`` above, for the same
    #: reason: a comma-separated string, not JSON.
    mfa_acr_values: Annotated[frozenset[str], NoDecode] = frozenset({"2"})

    @field_validator("mfa_acr_values", mode="before")
    @classmethod
    def _split_mfa_acr_values(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value


class ApiSettings(BaseSettings):
    """HTTP-layer configuration for the FastAPI app (issue #41).

    ``frontend_base_url`` is the single browser origin allowed to call the
    API cross-origin. It is load-bearing, not cosmetic: ADR-0021's SPA
    performs the PKCE code exchange in the browser and then calls this API
    with a Bearer token, so without an accurate allowed origin every
    authenticated request fails CORS preflight.

    It reuses the ``NPTC_FRONTEND_BASE_URL`` variable the Keycloak realm
    import already substitutes into ``nptc-frontend``'s ``redirectUris``/
    ``webOrigins`` - one value, so the origin Keycloak will redirect to and
    the origin the API will accept cannot drift apart.

    The default matches the Vite dev server, and is the one setting here
    that may legitimately be a plain-http localhost value; any other
    deployment must set it to the frontend's real origin.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    frontend_base_url: str = "http://localhost:5173"

    @field_validator("frontend_base_url")
    @classmethod
    def _is_a_bare_origin(cls, value: str) -> str:
        """Scheme, host and optional port - nothing else.

        A browser sends `Origin: https://app.example` with no path, so a
        configured value carrying one (`https://app.example/nptc`) can
        never match, and CORS would fail every authenticated request with
        nothing in the logs pointing here. Rejecting it at
        settings-construction time turns a silent runtime failure into a
        startup error naming the field.
        """
        value = _require_non_blank(value, "frontend_base_url").rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"frontend_base_url must be an http(s) origin, got {value!r}")
        if parts.path or parts.query or parts.fragment:
            raise ValueError(
                f"frontend_base_url must be a bare origin (scheme, host, optional "
                f"port) with no path, query or fragment, got {value!r}"
            )
        return value


class MigrationSettings(BaseSettings):
    """The owning role's DSN Alembic runs migrations as - see
    ``backend/migrations/env.py``. Deliberately separate from
    ``DatabaseSettings``: nothing about running a migration should depend
    on ``NPTC_DATABASE_URL`` being set."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    migration_database_url: str

    @field_validator("migration_database_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "migration_database_url")
