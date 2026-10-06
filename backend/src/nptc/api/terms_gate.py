"""The request-time check that refuses a contribution from a user who has not accepted the
current terms (NFR-45, ADR-0043).

**An app-level dependency, so the default is refusal.** `create_app` registers
`require_current_terms` for every route. A mutating route added later is covered without anyone
remembering to opt it in, which a wrapper on `permission_dep` could not promise: that wrapper
is attached route by route. The gate is a named condition, never a role (FR-44).

**What it skips.**

- Safe methods (GET, HEAD, OPTIONS). It resolves no principal for them, so a public read
  never starts to fail on a bad or expired token (FR-20).
- Anonymous callers, which the route's own permission check already refuses with a 401.
- Accounts that are not active. A suspended user holds only the anonymous permissions and gets
  the ordinary permission refusal, which this refusal would otherwise replace.
- The routes in `EXEMPT_ROUTES`.

**Adding an exemption.** A user who declines a new version must still be able to leave and to
exercise their privacy rights, so sign-out, account closure (NFR-17) and a request to see or
correct personal information (NFR-14, NFR-16) belong on this list when they become HTTP routes.
Today none is: closure is a library function, `nptc.auth.identity.close_account`, and the SPA
signs out at Keycloak. `test_every_exempt_entry_names_a_real_mutating_route` fails for an entry
that names no real route.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    ApiSettingsDep,
    current_principal,
    get_auth_settings,
    get_session,
    get_token_verifier,
)
from nptc.api.errors import (
    TERMS_ACCEPTANCE_REQUIRED_CODE,
    TermsAcceptanceRequiredResponse,
)
from nptc.api.prefix import API_PREFIX
from nptc.auth.tokens import TokenVerifier
from nptc.db.models.user import UserStatus
from nptc.settings import AuthSettings
from nptc.terms.acceptance import has_accepted
from nptc.terms.errors import TermsAcceptanceRequiredError

#: `(method, path)` as `route.path` spells it: relative to the API prefix, with the router's
#: own prefix included and path parameters in braces. `route_inventory_support.RouteKey` uses
#: the same form.
EXEMPT_ROUTES: Final[frozenset[tuple[str, str]]] = frozenset({("POST", "/auth/terms/acceptance")})

_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


def require_current_terms(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
    auth_settings: Annotated[AuthSettings, Depends(get_auth_settings)],
    settings: ApiSettingsDep,
) -> None:
    if request.method in _SAFE_METHODS:
        return

    # Called directly, not through `Depends(current_principal)`: a dependency would resolve the
    # caller on every request, public reads included. `current_principal` keeps the result on the
    # request, so the route's own `CurrentPrincipal` reuses it.
    principal = current_principal(request, session, verifier, auth_settings)
    if principal.user_id is None or principal.status is not UserStatus.ACTIVE:
        return

    route = request.scope.get("route")
    if (request.method, getattr(route, "path", None)) in EXEMPT_ROUTES:
        return

    if not has_accepted(session, principal.user_id, current_version=settings.terms_current_version):
        raise TermsAcceptanceRequiredError(
            f"current terms version {settings.terms_current_version} not accepted"
        )


def declare_terms_refusal(app: FastAPI) -> None:
    """Adds the gate's 403 body to the OpenAPI document.

    A dependency cannot declare a response, and the gate covers every mutating route, so this
    adds `TermsAcceptanceRequiredResponse` to each mutating operation that is not exempt. Where a
    route already declares a 403 the body becomes a union, so a generated client types both.
    """
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original()
        _add_terms_refusal(schema)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


#: `EXEMPT_ROUTES` as the OpenAPI document spells them: lower-case method, full path.
_EXEMPT_OPERATIONS: Final = frozenset(
    (method.lower(), f"{API_PREFIX}{path}") for method, path in EXEMPT_ROUTES
)
_TERMS_REFUSAL_REF: Final = {"$ref": "#/components/schemas/TermsAcceptanceRequiredResponse"}
_TERMS_REFUSAL_DESCRIPTION: Final = (
    "The current terms of use have not been accepted. The body carries "
    f"`code: {TERMS_ACCEPTANCE_REQUIRED_CODE}`. Accept them with "
    "`POST /auth/terms/acceptance` and try again. Never carries `WWW-Authenticate`."
)


def _add_terms_refusal(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["TermsAcceptanceRequiredResponse"] = (
        TermsAcceptanceRequiredResponse.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    )
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method.upper() in _SAFE_METHODS or (method, path) in _EXEMPT_OPERATIONS:
                continue
            responses = operation.setdefault("responses", {})
            existing = responses.get("403")
            if existing is None:
                responses["403"] = {
                    "description": _TERMS_REFUSAL_DESCRIPTION,
                    "content": {"application/json": {"schema": _TERMS_REFUSAL_REF}},
                }
                continue
            body = existing.setdefault("content", {}).setdefault("application/json", {})
            declared = body.get("schema")
            body["schema"] = (
                {"anyOf": [declared, _TERMS_REFUSAL_REF]} if declared else _TERMS_REFUSAL_REF
            )
            existing["description"] = (
                f"{existing.get('description', '')} {_TERMS_REFUSAL_DESCRIPTION}".strip()
            )
