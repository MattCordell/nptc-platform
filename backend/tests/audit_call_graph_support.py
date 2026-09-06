"""Static call-graph walker bridging a runtime FastAPI route to its source
(issue #165, NFR-08).

`test_audit_route_inventory.py` needs to answer "can this endpoint function
reach `nptc.audit.recording.record_change` / `record_snapshot_change`
without running the app" - a question `test_audit_write_path_guard.py`
(#37, call-site only) and `route_inventory_support.py` (#44/this issue's
enumeration, no call-graph awareness) do not answer between them.

The bridge: a runtime route's `endpoint.__module__` + `endpoint.__qualname__`
names the exact source function. From there this is a pure `ast` walk of
`ast.Call` nodes, resolving each call's target name via the *calling*
module's own `import`/`from ... import` table, recursing only into modules
under `nptc.` (found on disk under `backend/src`) - FastAPI, SQLAlchemy and
stdlib calls are leaves the walk does not need to look inside.

Deliberately conservative: an attribute call whose base cannot be resolved
to a known `nptc.` module (e.g. `session.flush()`, `queries.some_read()`)
is left unresolved rather than guessed at - a false "reachable" here would
defeat the whole point of the guard. `test_audit_route_inventory.py`'s
allow-list exists precisely for the routes this walker can prove do not
reach an audit call.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "backend" / "src"

#: (module, qualname) pairs that count as "this endpoint audits its write".
TARGET_FUNCTIONS = frozenset(
    {
        ("nptc.audit.recording", "record_change"),
        ("nptc.audit.recording", "record_snapshot_change"),
    }
)


def _module_to_path(module_name: str) -> Path | None:
    parts = module_name.split(".")
    module_file = SRC_ROOT.joinpath(*parts).with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_init = SRC_ROOT.joinpath(*parts, "__init__.py")
    if package_init.is_file():
        return package_init
    return None


@cache
def _parse_module(module_name: str) -> ast.Module | None:
    path = _module_to_path(module_name)
    if path is None:
        return None
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@dataclass(frozen=True)
class _Imports:
    #: local name -> fully-qualified module it refers to, for `import x`,
    #: `import x as y` and (as a *hypothesis*, checked against disk before
    #: use) `from pkg import submodule`-style imports.
    modules: dict[str, str]
    #: local name -> (defining module, original name), for
    #: `from pkg.mod import name` / `from pkg.mod import name as alias`.
    names: dict[str, tuple[str, str]]


def _resolve_relative_module(current_module: str, node: ast.ImportFrom) -> str:
    """CPython's own relative-import resolution (see
    `importlib._bootstrap._resolve_name`): level counts from the
    *package containing* `current_module`, not from `current_module`
    itself."""
    package = ".".join(current_module.split(".")[:-1])
    if node.level == 1:
        base = package
    else:
        bits = package.rsplit(".", node.level - 1)
        base = bits[0]
    return f"{base}.{node.module}" if node.module else base


def _build_import_table(module_name: str, tree: ast.Module) -> _Imports:
    modules: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                modules[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None and not node.level:
                continue
            base_module = _resolve_relative_module(module_name, node) if node.level else node.module
            assert base_module is not None
            for alias in node.names:
                local = alias.asname or alias.name
                names[local] = (base_module, alias.name)
                # A `from pkg import submodule` import is indistinguishable
                # at the AST level from `from pkg import a_function` - carry
                # the submodule hypothesis too, so `submodule.func(...)`
                # resolves via the `modules` path below. `_parse_module`
                # silently returns `None` for the hypothesis that does not
                # correspond to a real file, so this never manufactures a
                # target that cannot be found.
                modules[local] = f"{base_module}.{alias.name}"

    return _Imports(modules=modules, names=names)


def _find_function(
    tree: ast.Module, qualname: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    parts = qualname.split(".")
    scope: list[ast.stmt] = list(tree.body)
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    for part in parts:
        node = next(
            (
                n
                for n in scope
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            return None
        scope = list(node.body)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node
    return None


def _call_targets(call: ast.Call, module_name: str, imports: _Imports) -> list[tuple[str, str]]:
    """Every `(module, qualname)` this call could resolve to, most likely
    first. A `Name` call not in the import table is assumed to be a
    same-module function/class (verified, or discarded, by `_find_function`
    failing to locate it). An `Attribute` call whose base cannot be tied to
    a known module resolves to nothing - see the module docstring."""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in imports.names:
            return [imports.names[func.id]]
        return [(module_name, func.id)]
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            if base.id in imports.modules:
                return [(imports.modules[base.id], func.attr)]
            if base.id in imports.names:
                owner_module, owner_name = imports.names[base.id]
                return [(f"{owner_module}.{owner_name}", func.attr)]
    return []


def reachable(
    module_name: str,
    qualname: str,
    *,
    targets: frozenset[tuple[str, str]] = TARGET_FUNCTIONS,
    _visited: set[tuple[str, str]] | None = None,
) -> bool:
    """Whether `qualname` in `module_name` (module-level function, or a
    dotted `Class.method`) reaches one of `targets` through a static
    `ast.Call` walk confined to `nptc.*` modules under `backend/src`.

    Every module/scan is cached (`_parse_module`) and every (module,
    qualname) pair is visited at most once per top-level call (`_visited`),
    so an import cycle or a function called from two places along the walk
    terminates rather than looping or doing quadratic re-work.
    """
    key = (module_name, qualname)
    if _visited is None:
        _visited = set()
    if key in _visited:
        return False
    _visited.add(key)

    tree = _parse_module(module_name)
    if tree is None:
        return False
    func_node = _find_function(tree, qualname)
    if func_node is None:
        return False

    imports = _build_import_table(module_name, tree)

    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        for target_module, target_qualname in _call_targets(node, module_name, imports):
            if (target_module, target_qualname) in targets:
                return True
            if not target_module.startswith("nptc."):
                continue
            if reachable(target_module, target_qualname, targets=targets, _visited=_visited):
                return True

    return False
