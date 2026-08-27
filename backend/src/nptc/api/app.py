"""The FastAPI application factory (issue #41).

Until now `nptc.api` was a docstring stub and the only FastAPI app in the
repo was a throwaway one in `backend/tests/authz_app_support.py`. This is
the real one that harness was standing in for.

A factory rather than a module-level `app = FastAPI()`: tests need to
build an app with overridden dependencies without importing a global that
has already read settings and opened a connection pool at import time.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nptc.api.dependencies import get_terminology_client
from nptc.api.errors import register_exception_handlers
from nptc.api.routers import auth, catalogue, catalogue_bindings
from nptc.settings import ApiSettings

API_PREFIX = "/api/v1"


def create_app(*, settings: ApiSettings | None = None) -> FastAPI:
    app = FastAPI(
        title="NPTC Catalogue Maintenance Platform",
        version="0.0.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
    )
    api_settings = settings or ApiSettings()

    # Built here, not on the first request that needs it. The construction
    # itself is cheap and opens no socket, so this is not about warming a
    # cache - it is about *where the failure lands*.
    # `TerminologyConfig.from_env` raises `TerminologyConfigError` on a
    # malformed `NPTC_TX_*` value, and `get_terminology_client`'s
    # `lru_cache` does not cache a raised exception, so leaving it lazy
    # turns one deployment typo into a 500 on every request to a public
    # read endpoint (FR-20) for as long as nobody notices. Calling it
    # during app construction makes the same typo a start-up failure
    # instead: loud, once, and before the process ever takes traffic. The
    # `lru_cache` then hands every request the instance built here.
    #
    # Issue #52 moved this from `get_datatype_registry` itself: that
    # function is now request-scoped (it wires a `DatabaseLocalCodeLookup`
    # against the request's own `Session`, per FR-10/#56), so it can no
    # longer be called with no arguments here - `get_terminology_client`
    # is the one piece of that construction that both fails on a
    # deployment typo and is still safe to build with no request in hand.
    get_terminology_client()

    # Exactly one origin, never "*": ADR-0021 has the browser hold the
    # access token and send it here, so a permissive CORS policy would let
    # any origin drive an authenticated request with it. `allow_credentials`
    # stays False - the SPA authenticates with an Authorization header, not
    # a cookie, and there is no cookie for a browser to be tricked into
    # attaching.
    app.add_middleware(
        CORSMiddleware,
        # Already normalised to a bare origin by ApiSettings' validator.
        allow_origins=[api_settings.frontend_base_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    register_exception_handlers(app)
    app.include_router(auth.router, prefix=API_PREFIX)
    # FR-20 (issue #142): the public read API. Under the same `/api/v1`
    # prefix as everything else - it is one versioned API with a public
    # subset, not a second API with its own version line.
    app.include_router(catalogue.router, prefix=API_PREFIX)
    # issue #219: the first state-changing catalogue routes - code binding
    # create/retire/replace. A separate router from `catalogue.py` on
    # purpose; see `catalogue_bindings`'s own module docstring.
    app.include_router(catalogue_bindings.router, prefix=API_PREFIX)
    return app
