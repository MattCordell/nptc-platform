"""Route-table inventory asserting every state-changing endpoint of the
real app emits an audit event (issue #165, NFR-08 - the parent issue #34's
third acceptance criterion).

Neither existing guard proves this: `test_audit_write_path_guard.py` (#37)
is a call-site AST guard - it proves nobody writes `audit_event` outside
`nptc.audit.writer`, not that every endpoint reaches that writer at all.
`test_authz_inventory.py` (#44) walks the same route table but for
negative-auth coverage, not auditing.

This enumerates every non-GET route from the real `create_app()` via
`route_inventory_support.mutating_routes_with_endpoints` (shared with #44's
walker, not a hand-maintained list and not a second walker), then for each
one asks `audit_call_graph_support.reachable` whether
`nptc.audit.recording.record_change`/`record_snapshot_change` is reachable
from the endpoint's own source, through a static call-graph walk confined
to `nptc.*` under `backend/src`. `audit_call_graph_support.is_resolvable`
is checked first, and separately: an endpoint whose source the walker
cannot even find (see that module's own docstring for what "reachable"
does and does not claim) must not quietly read the same as "resolved, and
genuinely does not audit" - that would turn a walker gap into an
undeserved allow-list candidate.

A route that legitimately audits nothing is named in `ALLOW_LISTED_ROUTES`
with a stated reason - today that is exactly `DELETE
/registry/properties/{key}`, which always raises
`PropertyDefinitionDeleteRefusedError` (FR-11) before touching any state.
The allow-list is checked in both directions, the same way
`assert_inventory_covers_every_mutating_route` checks coverage in both
directions: an allow-listed route that no longer exists, or that now *does*
audit, is reported rather than silently passing.

Pure `ast` plus app construction - no `@pytest.mark.integration`, no
network, no database.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI

from nptc.api.app import create_app
from nptc.api.openapi_document import GENERATION_FRONTEND_BASE_URL
from nptc.settings import ApiSettings

if TYPE_CHECKING:
    from fastapi.routing import APIRoute


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
mutating_routes_with_endpoints = _inventory.mutating_routes_with_endpoints

_walker = _load("audit_call_graph_support")
reachable = _walker.reachable
is_resolvable = _walker.is_resolvable
TARGET_FUNCTIONS = _walker.TARGET_FUNCTIONS


@dataclass(frozen=True)
class AllowListEntry:
    route: RouteKey
    reason: str


#: Routes proven, by reading their source, to legitimately audit nothing -
#: grown only alongside a stated reason, never to silence a real gap.
ALLOW_LISTED_ROUTES = frozenset(
    {
        AllowListEntry(
            route=RouteKey(method="DELETE", path="/registry/properties/{key}"),
            reason=(
                "always raises PropertyDefinitionDeleteRefused"
                "Error before touching any state (FR-11) - "
                "property_definition has no DELETE grant at the database "
                "layer at all (issue #51)"
            ),
        ),
    }
)


@pytest.fixture(scope="module")
def real_app_routes() -> dict[object, APIRoute]:
    """Every real mutating route, built once per module rather than once
    per test - `create_app()` does real construction work (settings
    validation, terminology client setup) that four separate tests gain
    nothing from repeating."""
    app = create_app(settings=ApiSettings(frontend_base_url=GENERATION_FRONTEND_BASE_URL))
    return mutating_routes_with_endpoints(app)


def _endpoint_module_and_qualname(route: APIRoute) -> tuple[str, str]:
    endpoint = route.endpoint
    module_name = getattr(endpoint, "__module__", None)
    qualname = getattr(endpoint, "__qualname__", None)
    assert module_name is not None and qualname is not None, (
        f"endpoint {endpoint!r} has no __module__/__qualname__ to resolve a source location for"
    )
    return module_name, qualname


def _endpoint_reaches_audit(route: APIRoute) -> bool:
    module_name, qualname = _endpoint_module_and_qualname(route)
    return reachable(module_name, qualname, targets=TARGET_FUNCTIONS)


@pytest.mark.req("NFR-08")
def test_every_mutating_route_endpoint_is_resolvable(
    real_app_routes: dict[object, APIRoute],
) -> None:
    """Distinct from, and checked before, the audit-reachability test below
    (PR #270 review): a route whose source the walker cannot find at all is
    a gap in the walker/bridge, not evidence the route fails to audit -
    conflating the two would let an unresolvable route quietly become an
    allow-list candidate instead of a loud failure demanding the walker
    itself be fixed."""
    unresolvable = [
        route
        for route in real_app_routes
        if not is_resolvable(*_endpoint_module_and_qualname(real_app_routes[route]))
    ]
    assert not unresolvable, (
        f"mutating route(s) whose endpoint source could not be resolved at all "
        f"(a walker/bridge gap, not evidence of missing auditing): {sorted(str(r) for r in unresolvable)}"
    )


@pytest.mark.req("NFR-08")
def test_every_mutating_route_reaches_an_audit_call_or_is_allow_listed(
    real_app_routes: dict[object, APIRoute],
) -> None:
    allow_listed = {entry.route: entry.reason for entry in ALLOW_LISTED_ROUTES}

    unaudited_and_uncovered = [
        route
        for route, api_route in real_app_routes.items()
        if route not in allow_listed and not _endpoint_reaches_audit(api_route)
    ]
    assert not unaudited_and_uncovered, (
        "mutating route(s) whose call graph never reaches "
        "record_change/record_snapshot_change, and are not allow-listed: "
        f"{sorted(str(r) for r in unaudited_and_uncovered)}"
    )

    stale_allow_list_entries = [route for route in allow_listed if route not in real_app_routes]
    assert not stale_allow_list_entries, (
        f"allow-listed route(s) no longer exist: {sorted(str(r) for r in stale_allow_list_entries)}"
    )

    now_audited_allow_list_entries = [
        route
        for route in allow_listed
        if route in real_app_routes and _endpoint_reaches_audit(real_app_routes[route])
    ]
    assert not now_audited_allow_list_entries, (
        "allow-listed route(s) now reach an audit call and no longer need the allow-list entry: "
        f"{sorted(str(r) for r in now_audited_allow_list_entries)}"
    )


def test_allow_list_entries_name_real_routes_of_the_current_app(
    real_app_routes: dict[object, APIRoute],
) -> None:
    """Standalone from the main test above so a stale entry is reported
    even if every real gap is otherwise covered - this is the same
    both-directions discipline `assert_inventory_covers_every_mutating_
    route` already applies to #44's coverage set."""
    for entry in ALLOW_LISTED_ROUTES:
        assert entry.route in real_app_routes, (
            f"allow-listed route {entry.route} no longer exists in the app's route table"
        )


@pytest.fixture
def synthetic_module_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Points the walker at `tmp_path` and guarantees its module cache is
    cleared before *and* after the test regardless of an assertion failure
    in between - a manual clear-clear pair around the test body would skip
    the trailing clear on failure and leak entries computed under this
    `tmp_path` into later tests (PR #270 review)."""
    monkeypatch.setattr(_walker, "SRC_ROOT", tmp_path)
    _walker._parse_module.cache_clear()
    yield tmp_path
    _walker._parse_module.cache_clear()


def _load_synthetic_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, source: str
) -> object:
    """A real, importable module written to `tmp_path` under the name
    `name` (flat, no package - so the walker's SRC_ROOT-relative path
    lookup needs no `__init__.py`), returning its `handler` function.
    `handler.__module__` is `name` (set by the import machinery itself),
    matching what `audit_call_graph_support` will look for once `SRC_ROOT`
    is pointed at `tmp_path`. `monkeypatch.setitem` undoes the
    `sys.modules` registration after the test, rather than leaving a
    fixed-name synthetic module (`synthetic_audited`) as session-global
    state (PR #270 review)."""
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, tmp_path / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module.handler


@pytest.mark.req("NFR-08")
def test_positive_control_a_write_route_that_never_audits_is_flagged(
    monkeypatch: pytest.MonkeyPatch, synthetic_module_root: Path
) -> None:
    """Mirrors `test_audit_write_path_guard.py`'s own positive control: a
    synthetic endpoint that writes without auditing must be caught, and one
    that genuinely calls `record_change` must not be - proving the walker
    itself is exercised end to end through `mutating_routes_with_endpoints`
    and `reachable` together, not just that the enumeration or the graph
    walk each happen to match nothing on their own."""
    tmp_path = synthetic_module_root
    app = FastAPI()
    app.post("/audited")(
        _load_synthetic_endpoint(
            monkeypatch,
            tmp_path,
            "synthetic_audited",
            "from nptc.audit.recording import record_change\n\n\n"
            "def handler():\n    record_change()\n",
        )
    )
    app.post("/unaudited")(
        _load_synthetic_endpoint(
            monkeypatch, tmp_path, "synthetic_unaudited", "def handler():\n    pass\n"
        )
    )

    routes = mutating_routes_with_endpoints(app)

    assert _endpoint_reaches_audit(routes[RouteKey(method="POST", path="/audited")])
    assert not _endpoint_reaches_audit(routes[RouteKey(method="POST", path="/unaudited")])


@pytest.mark.req("NFR-08")
def test_negative_control_a_known_good_route_resolves_through_the_graph(
    real_app_routes: dict[object, APIRoute],
) -> None:
    """A resolution bug that makes the walk find nothing for *every* route
    must surface as a failure here, not as a vacuous pass of the main test
    above (which would also pass, wrongly, if every route were
    unresolvable)."""
    known_good = RouteKey(method="POST", path="/catalogue/entries/{business_key}/bindings")

    assert _endpoint_reaches_audit(real_app_routes[known_good])


def test_mutating_route_count_sanity_check(real_app_routes: dict[object, APIRoute]) -> None:
    """Not itself an acceptance criterion, but the plan's own sanity check
    (issue #165): a route added or removed under `backend/src/nptc/api/
    routers` without updating this count is a signal to look again, not a
    silent drift in what "every mutating endpoint" means."""
    assert len(real_app_routes) == 14, (
        f"expected exactly 14 mutating routes, found {len(real_app_routes)}: "
        f"{sorted(str(r) for r in real_app_routes)}"
    )
