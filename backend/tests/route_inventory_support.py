"""Route-table inventory: enumerating every non-GET route of the real
FastAPI app, and cross-checking it against a declared coverage set (issue
#44's FR-80/FR-81 acceptance criterion, and issue #165's route-table
inventory test for NFR-08 - the two share this walker so they cannot
silently drift into two different notions of "every mutating endpoint").

Not a `test_*.py` module - imported by path via `importlib`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.routing import APIRoute

#: HTTP methods FastAPI/Starlette add automatically that are never
#: "mutating endpoints" in the sense this inventory cares about.
_NON_MUTATING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RouteKey:
    method: str
    path: str

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def mutating_routes(app: FastAPI) -> frozenset[RouteKey]:
    """Every route on `app` whose declared methods include at least one
    non-GET/HEAD/OPTIONS verb, one `RouteKey` per (method, path) pair -
    a route declaring both GET and POST contributes only its POST key."""
    keys: set[RouteKey] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method not in _NON_MUTATING_METHODS:
                keys.add(RouteKey(method=method, path=route.path))
    return frozenset(keys)


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
