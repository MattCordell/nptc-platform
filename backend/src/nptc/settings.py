"""Backend runtime configuration.

``pydantic-settings``, not the ``os.environ`` reads of
``nptc_shared.terminology.config`` (ADR-0003): it is a backend-only
dependency, so nothing cross-package requires matching that module.

Each DSN has its own settings class. ``backend/migrations/env.py`` needs
``MigrationSettings`` alone, so an operator running ``alembic upgrade head``
need not set ``NPTC_DATABASE_URL``. A required DSN has no default: a missing,
empty or whitespace-only value fails naming the field, so a misconfigured
deployment never runs against a placeholder.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_URL_DELIMITERS = ("@", ":", "/", "?", "#", "%")


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
    """The DSN `scripts/verify_audit_chain.py` falls back to when
    ``--database-url`` is not passed. ``nptc.audit.verification.verify_chain``
    only issues `SELECT`s, so this can name a read-only replica or a restored
    backup.

    Empty default, unlike ``DatabaseSettings``: the CLI falls back further to
    ``NPTC_DATABASE_URL`` (see the runbook), so empty is a valid
    configuration. The CLI raises, naming all three sources, if no DSN
    resolves.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    audit_verify_database_url: str = ""


class AuthSettings(BaseSettings):
    """The NFR-05 trusted-issuer allowlist for auto-linking
    (``nptc.auth.linking.may_auto_link``) and the NFR-07 JWT verification
    configuration (``nptc.auth.tokens.TokenVerifier.from_settings``).

    Empty defaults, unlike the DSNs above: "no issuer is trusted yet" is a
    valid fail-closed configuration (NFR-02, federation off). An empty
    ``oidc_issuer`` cannot construct a ``TokenVerifier`` (see
    ``nptc.auth.errors``), so an unconfigured deployment refuses every token.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    # NoDecode: pydantic-settings JSON-decodes a frozenset[str] before any
    # validator runs, so the comma-separated format in configuration.md would
    # raise a SettingsError and never reach `_split_comma_separated`.
    trusted_issuers: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator("trusted_issuers", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    oidc_issuer: str = ""

    #: Fixed by the committed realm (`nptc-api-audience` mapper in
    #: deploy/keycloak/realm/nptc-realm.json), not by a deployment: ADR-0014.
    oidc_audience: str = "nptc-api"

    #: Empty means "resolve via OIDC discovery" (`nptc.auth.discovery`).
    #: Setting it skips discovery, for air-gapped deployments and so the
    #: offline tests need only one local HTTP endpoint.
    jwks_url: str = ""

    jwks_cache_seconds: float = 300.0

    #: An unknown `kid` within this many seconds of the last refresh attempt
    #: is refused without an HTTP request (``nptc.auth.jwks.SigningKeys``).
    jwks_refresh_cooldown_seconds: float = 30.0

    #: NFR-06: the `acr` claim values that satisfy mandatory MFA for
    #: administrators (``nptc.auth.principal.principal_for``). Matches the
    #: committed realm's ``acr.loa.map``, which maps the LoA-2 flow to
    #: ``"2"``. Comma-separated, so ``NoDecode`` as for ``trusted_issuers``.
    mfa_acr_values: Annotated[frozenset[str], NoDecode] = frozenset({"2"})

    @field_validator("mfa_acr_values", mode="before")
    @classmethod
    def _split_mfa_acr_values(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("mfa_acr_values", mode="after")
    @classmethod
    def _mfa_acr_values_are_usable(cls, value: frozenset[str]) -> frozenset[str]:
        """`nptc.api.errors._step_up_challenge` builds the RFC 9470 challenge
        header from this set (ADR-0036), so a bad value is a live failure.

        An empty set makes `principal_for`'s `mfa_satisfied` permanently
        `False`, while the SPA's `parseStepUpChallenge` refuses an
        `acr_values=""` challenge: every administrator is locked out with no
        way to step up. A quote or line break would be interpolated straight
        into the header value.
        """
        if not value:
            raise ValueError(
                "mfa_acr_values must not be empty - an empty set makes MFA "
                "permanently unsatisfiable and leaves the SPA nothing to step up "
                "to, locking every administrator out with no way back"
            )
        for item in value:
            if '"' in item or "\r" in item or "\n" in item:
                raise ValueError(
                    f"mfa_acr_values item {item!r} contains a quote or line break, "
                    "which would be interpolated directly into the WWW-Authenticate "
                    "response header"
                )
        return value


class ApiSettings(BaseSettings):
    """HTTP-layer configuration for the FastAPI app.

    ``frontend_base_url`` is the single browser origin allowed to call the
    API cross-origin. ADR-0021's SPA exchanges the PKCE code in the browser
    and calls this API with a Bearer token, so a wrong origin fails every
    authenticated request at CORS preflight.

    It reuses ``NPTC_FRONTEND_BASE_URL``, which the Keycloak realm import
    substitutes into ``nptc-frontend``'s ``redirectUris``/``webOrigins``, so
    the origin Keycloak redirects to and the origin the API accepts cannot
    drift apart.

    The default matches the Vite dev server and is the only setting here
    that may be a plain-http localhost value; any other deployment must set
    the frontend's real origin.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    frontend_base_url: str = "http://localhost:5173"

    #: FR-98's label-provenance declaration for every served `fsn`
    #: (`nptc.api.labels.fsn_provenance`), a placeholder for FR-66's export
    #: configuration (P4, not built). `"intact"` is the only value the read
    #: path can honestly serve: nothing strips an `fsn` between the column
    #: and the response (FR-83's renderer, `render_display_term`, is reached
    #: only from the export surface). `_fsn_semantic_tag_is_intact` refuses
    #: `"stripped"`.
    fsn_semantic_tag: Literal["intact", "stripped"] = "intact"

    @field_validator("fsn_semantic_tag")
    @classmethod
    def _fsn_semantic_tag_is_intact(cls, value: str) -> str:
        """`"stripped"` could never strip anything: the read path has no
        stripper, so it would only make `LabelProvenance.semantic_tag` claim
        a strip that never happened (FR-83, FR-66). Refusing it here turns a
        typo, or an early attempt to wire FR-66's export configuration
        through this field, into a start-up failure.
        """
        if value == "stripped":
            raise ValueError(
                "fsn_semantic_tag=stripped is refused: the API read path has no "
                "semantic-tag stripper (FR-83's renderer is reached only from the "
                "export surface), so this setting cannot yet make that true. FR-66's "
                "export configuration is the future home for this choice; until it "
                "exists, fsn_semantic_tag must stay 'intact'."
            )
        return value

    #: FR-86: unset by default. RCPA-QAP has no maximum to enforce, and PRD
    #: open item OI-1 records that the platform owes them the FR-87 length
    #: data before they can nominate one. An environment variable, not a
    #: database setting: ADR-0041. `nptc.catalogue.term_hygiene.
    #: preferred_term_length` remains the only place length is computed;
    #: this is only the ceiling to compare it against.
    max_preferred_term_length: int | None = Field(default=None, ge=1)

    @field_validator("max_preferred_term_length", mode="before")
    @classmethod
    def _blank_max_preferred_term_length_is_unset(cls, value: object) -> object:
        """Blank means unset. Per field, not `env_ignore_empty`: a blank
        `NPTC_FRONTEND_BASE_URL` must keep failing, not fall back to localhost."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("frontend_base_url")
    @classmethod
    def _is_a_bare_origin(cls, value: str) -> str:
        """Scheme, host and optional port - nothing else.

        A browser sends `Origin: https://app.example` with no path, so a
        value carrying one (`https://app.example/nptc`) never matches, and
        CORS would fail every authenticated request with nothing in the logs
        pointing here.
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
    ``backend/migrations/env.py``."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    migration_database_url: str

    @field_validator("migration_database_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "migration_database_url")


class AppLoginSettings(BaseSettings):
    """The password `nptc.db.provision_login` sets on the app runtime login
    role. Required, so a missing value fails naming the variable (NFR-26).
    A `SecretStr`, so it never appears in a repr or a traceback.

    Compose splices it unencoded into `NPTC_DATABASE_URL`, which SQLAlchemy
    percent-decodes: a URL delimiter would provision, then fail to log in."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    app_db_password: SecretStr

    @field_validator("app_db_password")
    @classmethod
    def _url_safe_and_not_blank(cls, value: SecretStr) -> SecretStr:
        secret = _require_non_blank(value.get_secret_value(), "app_db_password")
        if any(char in secret for char in _URL_DELIMITERS):
            raise ValueError(
                f"app_db_password must not contain any of {' '.join(_URL_DELIMITERS)} "
                "because compose places it inside a database URL"
            )
        return value


class IndexerSettings(BaseSettings):
    """The DSN `nptc.db.property_reconciler` runs its DDL as (FR-13) - see
    that module and `scripts/reconcile_property_indexes.py`.

    Empty default, unlike `MigrationSettings`: "runtime index reconciliation
    is not configured" is a valid fail-closed posture, because
    `reconcile_property_indexes()` refuses to run rather than use another
    credential.

    **Deliberately its own variable, never a fallback to
    `NPTC_MIGRATION_DATABASE_URL` or `NPTC_DATABASE_URL`.** The migration
    role can `CREATE ROLE`/`DROP TABLE`, far more than one expression-index
    DDL operation needs, and the app role cannot do DDL at all
    (ADR-0012, `docs/operations/configuration.md`), so a fallback to it would
    only turn a clear refusal into a permission error mid-run."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    indexer_database_url: str = ""

    @field_validator("indexer_database_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Normalises a whitespace-only value to `""`. Otherwise
        `get_indexer_engine()`'s `if not settings.indexer_database_url` guard
        reads it as truthy, skips `IndexerNotConfiguredError` and fails later
        inside `create_engine`."""
        return value.strip()
