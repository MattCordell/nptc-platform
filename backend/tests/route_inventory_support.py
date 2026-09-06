"""Route-table inventory: enumerating every non-GET route of the real
FastAPI app, and cross-checking it against a declared coverage set (issue
#44's FR-80/FR-81 acceptance criterion). `mutating_routes_with_endpoints`
is the same walk plus each route's `endpoint` callable, for issue #165's
NFR-08 call-graph inventory - the two consumers share `_iter_mutating_api_
routes` so they cannot silently drift into two different notions of "every
mutating endpoint".

Not a `test_*.py` module - imported by path via `importlib`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

#: HTTP methods FastAPI/Starlette add automatically that are never
#: "mutating endpoints" in the sense this inventory cares about.
_NON_MUTATING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RouteKey:
    method: str
    path: str

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def _iter_mutating_api_routes(app: FastAPI) -> Iterable[tuple[RouteKey, APIRoute]]:
    """Every `(RouteKey, APIRoute)` pair for a route whose declared methods
    include at least one non-GET/HEAD/OPTIONS verb - a route declaring both
    GET and POST contributes only its POST pair. The one recursive walk
    both `mutating_routes` and issue #165's call-graph inventory build on,
    so the two cannot silently drift into two different notions of "every
    mutating endpoint" by each maintaining their own copy of it.

    **The walk is recursive, and has to be** (found while wiring issue
    #142's router): `app.include_router(...)` does not flatten the included
    router's routes into `app.routes` - it appends one opaque
    `fastapi.routing._IncludedRouter` entry that holds them. A single-level
    `isinstance(route, APIRoute)` filter therefore sees *nothing at all* for
    a real app assembled from routers, while continuing to work perfectly
    for a throwaway app whose routes were added with `@app.post` directly -
    which is exactly the shape this module's own tests use, so the gap could
    not surface there. Left unfixed, the day #143 points this inventory at
    the production app it would report an empty set and pass, silently
    asserting that every mutating endpoint is covered because it could not
    see any.

    **`_IncludedRouter`'s own shape moved again, found wiring issue #219**
    (the first mutating routes the real app ever had, which is what finally
    exercised this path with a non-empty result to check). Newer FastAPI no
    longer puts a `.routes` attribute directly on `_IncludedRouter` - the
    included `APIRouter`'s routes live one level deeper, at
    `.original_router.routes`. Both attributes are tried below, oldest
    first, so this keeps working across the FastAPI version this repo
    happens to pin rather than silently going back to seeing nothing the
    next time that internal shape changes.
    """
    seen: dict[RouteKey, APIRoute] = {}

    def visit(routes: Iterable[BaseRoute]) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods or set():
                    if method not in _NON_MUTATING_METHODS:
                        seen[RouteKey(method=method, path=route.path)] = route
            nested = getattr(route, "routes", None)
            if nested is None:
                original_router = getattr(route, "original_router", None)
                nested = getattr(original_router, "routes", None)
            if nested is not None:
                visit(nested)

    visit(app.routes)
    return seen.items()


def mutating_routes(app: FastAPI) -> frozenset[RouteKey]:
    """Every route on `app` whose declared methods include at least one
    non-GET/HEAD/OPTIONS verb, one `RouteKey` per (method, path) pair -
    a route declaring both GET and POST contributes only its POST key.
    See `_iter_mutating_api_routes` for the walk itself."""
    return frozenset(key for key, _ in _iter_mutating_api_routes(app))


def mutating_routes_with_endpoints(app: FastAPI) -> dict[RouteKey, Callable[..., Any]]:
    """Like `mutating_routes`, but keeping each route's `endpoint` callable
    - issue #165's call-graph inventory needs it to find the route's source
    (`endpoint.__module__` + `endpoint.__qualname__`); `mutating_routes`
    itself does not, so it stays a plain `frozenset` for #44's simpler
    coverage-set comparison."""
    return {key: route.endpoint for key, route in _iter_mutating_api_routes(app)}


def assert_inventory_covers_every_mutating_route(
    app: FastAPI, covered: frozenset[RouteKey]
) -> None:
    """Fails in both directions - the way this kind of inventory actually
    rots: a real route with no declared negative-auth coverage (a gap),
    *and* a covered entry naming a route that no longer exists (a stale
    entry silently no longer proving anything). Silent one-directional
    checks are exactly how "every write endpoint is covered" quietly stops
    being true."""
    actual = mutating_routes(app)
    missing = actual - covered
    stale = covered - actual
    assert not missing, (
        f"mutating route(s) with no declared negative-auth coverage: {sorted(str(r) for r in missing)}"
    )
    assert not stale, f"covered route(s) no longer exist: {sorted(str(r) for r in stale)}"
