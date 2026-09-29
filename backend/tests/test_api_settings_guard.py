"""FR-86/FR-98 guard: `ApiSettings` reaches request code only through
`Depends(get_api_settings)`.

A direct `get_api_settings()` call in a route or helper bypasses
`app.dependency_overrides`, so the request would read the process-wide
env-read instance instead of the one `create_app` was given - the two-object
hazard this guard keeps closed. `nptc/api/app.py` is the one caller: it is
the factory that installs the override.

Pure ``ast`` over ``backend/src/nptc/api``, modelled on
``test_token_verification_guard.py``, with a positive control over an inline
source string so the guard cannot rot into an always-pass.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "backend" / "src" / "nptc" / "api"

_FACTORY_PATH = "backend/src/nptc/api/app.py"
_GUARDED_NAME = "get_api_settings"


def _display(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_guarded_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _GUARDED_NAME
    return isinstance(func, ast.Attribute) and func.attr == _GUARDED_NAME


def _direct_calls(source: str, display_path: str) -> list[str]:
    if display_path == _FACTORY_PATH:
        return []
    return [
        f"{display_path}:{node.lineno}: direct {_GUARDED_NAME}() call"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _is_guarded_call(node)
    ]


@pytest.mark.req("FR-86")
def test_no_api_module_calls_get_api_settings_directly() -> None:
    violations = [
        violation
        for path in sorted(API_DIR.rglob("*.py"))
        for violation in _direct_calls(path.read_text(encoding="utf-8"), _display(path))
    ]

    assert not violations, (
        "Inject ApiSettingsDep instead of calling get_api_settings():\n" + "\n".join(violations)
    )


def test_guard_flags_a_direct_call_and_ignores_depends() -> None:
    source = """
from fastapi import Depends
from nptc.api import dependencies

def bare():
    return get_api_settings()

def qualified():
    return dependencies.get_api_settings()

def injected(settings=Depends(get_api_settings)):
    return settings
"""
    assert len(_direct_calls(source, "backend/src/nptc/api/routers/x.py")) == 2
    assert _direct_calls(source, _FACTORY_PATH) == []
