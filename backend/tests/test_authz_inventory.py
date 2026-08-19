"""`route_inventory_support`'s own correctness (issue #44/#165): the
checker must fail when a mutating route has no declared coverage, fail
when a covered entry names a route that no longer exists, and pass when
neither is true - proven against synthetic apps here, since `nptc/api/`
has no real `FastAPI()` app yet (it is a docstring stub, landing with
P1-9/#142/#143). Once that app exists, the same `mutating_routes`/
`assert_inventory_covers_every_mutating_route` should be pointed at it
directly, and `COVERED_WRITE_ROUTES`-style sets grown alongside each new
endpoint - this file is what keeps that checker itself honest in the
meantime.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # See test_authz_negative_http.py's _load for why sys.modules must be
    # populated before exec_module when the loaded module uses @dataclass.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_inventory = _load("route_inventory_support")
RouteKey = _inventory.RouteKey
mutating_routes = _inventory.mutating_routes
assert_inventory_covers_every_mutating_route = (
    _inventory.assert_inventory_covers_every_mutating_route
)


def _app_with_one_get_and_one_post() -> FastAPI:
    app = FastAPI()

    @app.get("/entries")
    def _list_entries() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/entries")
    def _create_entry() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_mutating_routes_excludes_get() -> None:
    app = _app_with_one_get_and_one_post()
    assert mutating_routes(app) == frozenset({RouteKey(method="POST", path="/entries")})


@pytest.mark.req("FR-80")
def test_positive_control_an_uncovered_route_is_flagged() -> None:
    """Proves the checker is meaningful even at zero real endpoints - an
    inline app with one deliberately-uncovered `POST` route must be
    reported, not silently pass."""
    app = _app_with_one_get_and_one_post()

    with pytest.raises(AssertionError, match="no declared negative-auth coverage"):
        assert_inventory_covers_every_mutating_route(app, covered=frozenset())


@pytest.mark.req("FR-80")
def test_a_fully_covered_app_passes() -> None:
    app = _app_with_one_get_and_one_post()
    covered = frozenset({RouteKey(method="POST", path="/entries")})

    assert_inventory_covers_every_mutating_route(app, covered=covered)


@pytest.mark.req("FR-81")
def test_a_stale_covered_entry_is_flagged() -> None:
    """The other direction a coverage set rots: a route that used to
    exist, renamed or removed, whose stale entry would otherwise keep
    silently "passing" without proving anything."""
    app = _app_with_one_get_and_one_post()
    covered = frozenset(
        {
            RouteKey(method="POST", path="/entries"),
            RouteKey(method="POST", path="/a-route-that-no-longer-exists"),
        }
    )

    with pytest.raises(AssertionError, match="no longer exist"):
        assert_inventory_covers_every_mutating_route(app, covered=covered)
