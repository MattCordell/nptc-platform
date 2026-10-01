"""FR-86/FR-98 guard: `ApiSettings` reaches request code only through
`Depends(get_api_settings)`.

A direct `get_api_settings()` call or `ApiSettings(...)` construction in a
route or helper bypasses `app.dependency_overrides`, so the request would
read an env-read instance instead of the one `create_app` was given - the
two-object hazard this guard keeps closed. Each form has an allow-list:
`app.py` is the factory that installs the override, and `dependencies.py` is
where `get_api_settings` builds the process-wide instance. The environment-free
forms `ApiSettings.model_construct()` and `.model_validate()` are flagged too, with
`openapi_document.py` as their one allowed caller.

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

_ALLOWED_PATHS = {
    "get_api_settings": {"backend/src/nptc/api/app.py"},
    "ApiSettings": {"backend/src/nptc/api/dependencies.py"},
}

#: `ApiSettings.model_construct(...)` and `.model_validate(...)` build an instance
#: without reading the environment, so `_called_name` sees only the attribute name
#: and the `ApiSettings(...)` check never fires. They bypass `dependency_overrides`
#: just as a bare construction does, so they get their own allow-list.
_ENV_FREE_BUILDERS = {"model_construct", "model_validate"}
_ENV_FREE_BUILD_ALLOWED_PATHS = {"backend/src/nptc/api/openapi_document.py"}


def _display(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _env_free_build(node: ast.Call) -> str | None:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _ENV_FREE_BUILDERS
        and isinstance(func.value, ast.Name)
        and func.value.id == "ApiSettings"
    ):
        return func.attr
    return None


def _direct_uses(source: str, display_path: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in _ALLOWED_PATHS and display_path not in _ALLOWED_PATHS[name]:
            violations.append(f"{display_path}:{node.lineno}: direct {name}() call")
        builder = _env_free_build(node)
        if builder and display_path not in _ENV_FREE_BUILD_ALLOWED_PATHS:
            violations.append(f"{display_path}:{node.lineno}: direct ApiSettings.{builder}() call")
    return violations


@pytest.mark.req("FR-86")
def test_no_api_module_resolves_api_settings_outside_the_allow_list() -> None:
    violations = [
        violation
        for path in sorted(API_DIR.rglob("*.py"))
        for violation in _direct_uses(path.read_text(encoding="utf-8"), _display(path))
    ]

    assert not violations, (
        "Inject ApiSettingsDep instead of resolving or building ApiSettings:\n"
        + "\n".join(violations)
    )


def test_guard_flags_direct_use_and_ignores_depends() -> None:
    source = """
from fastapi import Depends
from nptc.api import dependencies

def bare():
    return get_api_settings()

def qualified():
    return dependencies.get_api_settings()

def constructed():
    return ApiSettings(frontend_base_url="http://localhost:5173")

def constructed_without_env():
    return ApiSettings.model_construct(frontend_base_url="http://localhost:5173")

def validated_without_env():
    return ApiSettings.model_validate({"frontend_base_url": "http://localhost:5173"})

def other_model(settings=Depends(get_api_settings)):
    return AuthSettings.model_construct()

def injected(settings=Depends(get_api_settings)):
    return settings
"""
    assert len(_direct_uses(source, "backend/src/nptc/api/routers/x.py")) == 5
    assert len(_direct_uses(source, "backend/src/nptc/api/app.py")) == 3
    assert len(_direct_uses(source, "backend/src/nptc/api/dependencies.py")) == 4
    assert len(_direct_uses(source, "backend/src/nptc/api/openapi_document.py")) == 3
