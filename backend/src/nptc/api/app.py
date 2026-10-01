"""The FastAPI application factory.

A factory rather than a module-level `app = FastAPI()`: tests build an app
with overridden dependencies without importing a global that has already
read settings and opened a connection pool at import time.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nptc.api.dependencies import get_api_settings, get_auth_settings, get_terminology_client
from nptc.api.errors import register_exception_handlers
from nptc.api.prefix import API_PREFIX
from nptc.api.routers import (
    audit,
    auth,
    catalogue,
    catalogue_admin,
    catalogue_bindings,
    catalogue_designations,
    catalogue_entries,
    catalogue_properties,
    registry,
    terminology,
)
from nptc.settings import ApiSettings, AuthSettings

__all__ = ["API_PREFIX", "create_app"]


def create_app(
    *, settings: ApiSettings | None = None, auth_settings: AuthSettings | None = None
) -> FastAPI:
    app = FastAPI(
        title="NPTC Catalogue Maintenance Platform",
        version="0.0.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
    )
    api_settings = settings or get_api_settings()
    app.dependency_overrides[get_api_settings] = lambda: api_settings
    # Not `Depends(get_auth_settings)`: `register_exception_handlers` runs at
    # construction time, before any request exists. `get_auth_settings()` is the
    # `AuthSettings` `current_principal` reads for the positive MFA check, so an
    # explicit `auth_settings` lets a test build both from one object.
    step_up_auth_settings = auth_settings or get_auth_settings()

    # Built here for where the failure lands, not to warm a cache.
    # `TerminologyConfig.from_env` raises `TerminologyConfigError` on a malformed
    # `NPTC_TX_*` value, and `get_terminology_client`'s `lru_cache` does not cache
    # a raised exception, so a lazy build turns one deployment typo into a 500 on
    # every request to a public read endpoint (FR-20). Built here, it is a
    # start-up failure, and the `lru_cache` then hands every request this
    # instance. `get_datatype_registry` cannot be called here: it is
    # request-scoped (FR-10).
    get_terminology_client()

    # Exactly one origin, never "*": ADR-0021 has the browser hold the access
    # token and send it here, so a permissive policy would let any origin drive
    # an authenticated request. `allow_credentials` stays False: the SPA sends an
    # Authorization header, not a cookie.
    app.add_middleware(
        CORSMiddleware,
        # Already normalised to a bare origin by ApiSettings' validator.
        allow_origins=[api_settings.frontend_base_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # NFR-06: a browser hides every cross-origin response header not listed
        # here, `WWW-Authenticate` included (`vite dev` is cross-origin). Without
        # it the SPA's step-up handler reads `null` and silently never fires.
        expose_headers=["WWW-Authenticate"],
    )

    register_exception_handlers(app, step_up_auth_settings)
    app.include_router(auth.router, prefix=API_PREFIX)
    # FR-20's public read API: one versioned API with a public subset, not a
    # second API with its own version line.
    app.include_router(catalogue.router, prefix=API_PREFIX)
    # The catalogue write routers are separate modules from `catalogue.py`; each
    # module docstring says why.
    # Code binding create, retire and replace.
    app.include_router(catalogue_bindings.router, prefix=API_PREFIX)
    # Designation add, amend and retire, and collision acknowledgement (FR-04, FR-05).
    app.include_router(catalogue_designations.router, prefix=API_PREFIX)
    # Whole-property-value replace (FR-09, FR-10, FR-11, FR-37, FR-38, FR-88, FR-89).
    app.include_router(catalogue_properties.router, prefix=API_PREFIX)
    # Core-column writes: status and specimen_unconstrained (FR-36, FR-37, FR-38,
    # FR-89). Shares its path with catalogue.py's public GET; see its docstring.
    app.include_router(catalogue_entries.router, prefix=API_PREFIX)
    # Admin read of an entry in any status, gated on catalogue.edit_published.
    app.include_router(catalogue_admin.router, prefix=API_PREFIX)
    # PropertyDefinition admin, including the always-refusing DELETE (FR-11, FR-12).
    app.include_router(registry.router, prefix=API_PREFIX)
    # Live SCTID resolution during form completion (FR-26). Its own prefix and
    # tag, not under /catalogue; see its docstring.
    app.include_router(terminology.router, prefix=API_PREFIX)
    # Administrator search, filter and export over the audit log (NFR-12), gated
    # on Permission.AUDIT_READ. Its own prefix and tag.
    app.include_router(audit.router, prefix=API_PREFIX)
    return app
