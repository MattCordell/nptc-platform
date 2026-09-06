"""Unit tests for `audit_call_graph_support.reachable` (issue #165) against
synthetic sources, so a walker bug can be pinned down without depending on
the shape of the real routers - `test_audit_route_inventory.py` exercises
this walker against the real app separately.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


walker = _load("audit_call_graph_support")


@pytest.fixture(autouse=True)
def _fresh_module_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test gets its own `SRC_ROOT` and a clean `_parse_module` cache
    - without the clear, a module name reused across tests (e.g. `pkg.mod`)
    would resolve to whichever test happened to parse it first."""
    monkeypatch.setattr(walker, "SRC_ROOT", tmp_path)
    walker._parse_module.cache_clear()
    yield
    walker._parse_module.cache_clear()


def _write(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


TARGET = frozenset({("nptc.audit", "record_change")})


def test_reaches_target_directly(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "nptc/audit.py",
        "def record_change(*a, **k):\n    pass\n",
    )
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from nptc.audit import record_change\n\n\ndef handler():\n    record_change()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET)


def test_cross_module_hop_reaches_target(tmp_path: Path) -> None:
    """The known shape of every real mutating route: endpoint -> one
    service-layer function -> `record_change`, resolved through two
    separate modules' own import tables."""
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/service.py",
        "from nptc.audit import record_change\n\n\ndef save(entry):\n    record_change(entry)\n",
    )
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from nptc.service import save\n\n\ndef handler(body):\n    save(body)\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET)


def test_same_module_helper_hop_reaches_target(tmp_path: Path) -> None:
    """A route that calls a private helper defined in its own module (no
    import needed) which in turn calls the audit function."""
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from nptc.audit import record_change\n\n\n"
        "def _do_write():\n    record_change()\n\n\n"
        "def handler():\n    _do_write()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET)


def test_import_cycle_terminates_without_reaching_target(tmp_path: Path) -> None:
    """`a` and `b` call into each other forever if the walk does not track
    visited (module, qualname) pairs; neither reaches `record_change`."""
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/a.py",
        "from nptc.b import b_func\n\n\ndef a_func():\n    b_func()\n",
    )
    _write(
        tmp_path,
        "nptc/b.py",
        "from nptc.a import a_func\n\n\ndef b_func():\n    a_func()\n",
    )

    assert walker.reachable("nptc.a", "a_func", targets=TARGET) is False


def test_unresolvable_attribute_call_does_not_reach_target(tmp_path: Path) -> None:
    """`session.flush()`-shaped calls (base is a parameter, not an import)
    must not be mistaken for reaching anything - the walker never guesses
    at a call it cannot tie to a known module."""
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "def handler(session):\n    session.flush()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET) is False


def test_call_to_function_with_no_source_on_disk_is_a_leaf(tmp_path: Path) -> None:
    """A call to a third-party/stdlib name (nothing under `SRC_ROOT` to
    parse) must not blow up the walk - it is simply a leaf."""
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from fastapi import Response\n\n\ndef handler():\n    Response()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET) is False


def test_relative_import_hop_reaches_target(tmp_path: Path) -> None:
    """`from . import audit` / `from .audit import record_change`-style
    relative imports, used throughout `nptc.catalogue.*`, resolve the same
    as an absolute import."""
    _write(tmp_path, "nptc/__init__.py", "")
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from .audit import record_change\n\n\ndef handler():\n    record_change()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET)


def test_submodule_attribute_call_reaches_target(tmp_path: Path) -> None:
    """`from nptc import audit` then `audit.record_change(...)` - the
    submodule-import shape used by e.g. `nptc/api/app.py`'s router
    imports."""
    _write(tmp_path, "nptc/__init__.py", "")
    _write(tmp_path, "nptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "nptc/endpoint.py",
        "from nptc import audit\n\n\ndef handler():\n    audit.record_change()\n",
    )

    assert walker.reachable("nptc.endpoint", "handler", targets=TARGET)


def test_missing_function_is_not_reachable(tmp_path: Path) -> None:
    """A qualname that does not exist in the named module (a stale
    bridge from route to source) fails closed, not with an exception."""
    _write(tmp_path, "nptc/endpoint.py", "def handler():\n    pass\n")

    assert walker.reachable("nptc.endpoint", "nonexistent", targets=TARGET) is False
