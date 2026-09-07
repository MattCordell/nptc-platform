"""Unit tests for the repo root `conftest.py`'s `--req=ID` option (issue
#165 review): CLAUDE.md's documented `uv run pytest -m "req('FR-07')"`
does not work (pytest's `-m` grammar has no call syntax - see that
conftest's own docstring), so `--req` is the real selection mechanism.

Exercised here directly against lightweight fakes (a fake `config`/`item`/
`hook`, not a real collected pytest session) rather than via the
`pytester` plugin, which would need `pytest_plugins = ["pytester"]" in the
root conftest - loading it into every session in the repo for this one
file's benefit. Direct unit tests exercise exactly the same functions with
no such cost.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


root_conftest = _load("root_conftest", Path(__file__).resolve().parents[2] / "conftest.py")


class _FakeMark:
    def __init__(self, *args: str) -> None:
        self.args = args


class _FakeItem:
    def __init__(self, req_ids: list[str]) -> None:
        self._req_ids = req_ids

    def iter_markers(self, name: str) -> list[_FakeMark]:
        assert name == "req"
        return [_FakeMark(rid) for rid in self._req_ids]


class _FakeHook:
    def __init__(self) -> None:
        self.deselected_calls: list[list[_FakeItem]] = []

    def pytest_deselected(self, items: list[_FakeItem]) -> None:
        self.deselected_calls.append(list(items))


class _FakeConfig:
    def __init__(self, req_option: str | None, hook: _FakeHook) -> None:
        self._req_option = req_option
        self.hook = hook

    def getoption(self, name: str) -> str | None:
        assert name == "--req"
        return self._req_option


def test_modifyitems_selects_only_items_carrying_the_matching_id() -> None:
    hook = _FakeHook()
    config = _FakeConfig("FR-01", hook)
    fr01, fr02, both, unmarked = (
        _FakeItem(["FR-01"]),
        _FakeItem(["FR-02"]),
        _FakeItem(["FR-01", "FR-02"]),
        _FakeItem([]),
    )
    items = [fr01, fr02, both, unmarked]

    root_conftest.pytest_collection_modifyitems(config, items)

    assert items == [fr01, both]
    assert hook.deselected_calls == [[fr02, unmarked]]


def test_modifyitems_matches_an_item_carrying_more_than_one_id() -> None:
    hook = _FakeHook()
    config = _FakeConfig("FR-02", hook)
    both = _FakeItem(["FR-01", "FR-02"])
    items = [both]

    root_conftest.pytest_collection_modifyitems(config, items)

    assert items == [both]
    assert hook.deselected_calls == []


def test_modifyitems_is_a_noop_without_the_option() -> None:
    hook = _FakeHook()
    config = _FakeConfig(None, hook)
    original = [_FakeItem(["FR-01"]), _FakeItem([])]
    items = list(original)

    root_conftest.pytest_collection_modifyitems(config, items)

    assert items == original
    assert hook.deselected_calls == []


def test_modifyitems_deselects_everything_when_no_item_matches() -> None:
    hook = _FakeHook()
    config = _FakeConfig("NFR-99", hook)
    items = [_FakeItem(["FR-01"]), _FakeItem([])]
    original = list(items)

    root_conftest.pytest_collection_modifyitems(config, items)

    assert items == []
    assert hook.deselected_calls == [original]


def test_configure_accepts_a_real_requirement_id() -> None:
    """NFR-08 is the requirement issue #165 itself verifies, so it is
    guaranteed present in docs/requirements/requirements.yaml for as long
    as this test file is."""
    config = _FakeConfig("NFR-08", _FakeHook())

    root_conftest.pytest_configure(config)  # must not raise


def test_configure_rejects_an_unknown_requirement_id() -> None:
    config = _FakeConfig("NFR-9999-not-a-real-id", _FakeHook())

    with pytest.raises(pytest.UsageError, match="not a requirement ID"):
        root_conftest.pytest_configure(config)


def test_configure_is_a_noop_without_the_option() -> None:
    config = _FakeConfig(None, _FakeHook())

    root_conftest.pytest_configure(config)  # must not raise, must not need requirements.yaml
