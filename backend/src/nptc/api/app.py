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

from nptc.api.errors import register_exception_handlers
from nptc.api.routers import auth, catalogue
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
    return app
