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
module's own `import`/`from ... import` table (collected from anywhere in
the module, including function-scoped imports - a flat, whole-module symbol
table rather than true per-scope resolution, which is conservative in the
same direction as everything else here: it can only make a real import
visible sooner than Python's own scoping would, never invent one),
recursing only into modules under `nptc.` (found on disk under
`backend/src`) - FastAPI, SQLAlchemy and stdlib calls are leaves the walk
does not need to look inside.

Deliberately conservative: an attribute call whose base cannot be resolved
to a known `nptc.` module (e.g. `session.flush()`, `queries.some_read()`)
is left unresolved rather than guessed at - a false "reachable" here would
defeat the whole point of the guard. `test_audit_route_inventory.py`'s
allow-list exists precisely for the routes this walker can prove do not
reach an audit call.

**What "reachable" does not claim** (PR #270 review): this is "a call to
`record_change`/`record_snapshot_change` exists somewhere in the endpoint's
static call graph", not "every state change this endpoint makes is
audited". An endpoint that writes twice and audits once passes, and so
does an audit call sitting in a dead `except` branch or an inner closure
that is never invoked (`ast.walk` descends into nested `def`s without
regard to whether they run). NFR-08's route-table inventory test treats
that as an acceptable proxy - the 13 real routes were individually read
during #165's implementation to confirm each one's only mutating call is
the one this walker finds - but a future route added without that manual
read relies on the proxy alone.

`reachable` also cannot distinguish "no audit call exists" from "the
walker could not resolve the endpoint's source at all" (an unknown module,
a qualname it does not know how to look up - e.g. a closure-defined route,
`__qualname__` containing `<locals>`, or a function nested under a
module-level `if`/`try` that `_find_function` does not descend into): both
return `False`. `is_resolvable` answers the narrower "did the walk even
find source to look at" question, so a caller that needs to tell these
apart - as `test_audit_route_inventory.py` does, since an unresolvable
route silently becoming an allow-list candidate would be exactly the kind
of gap this guard exists to prevent - can check it first.
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


def _resolve_module_file(module_name: str) -> tuple[Path, bool] | None:
    """`(file path, is_package)` for `module_name`'s source under
    `SRC_ROOT`, or `None` if it has none. `is_package` matters to relative
    import resolution below: a package's `__init__.py` *is* the package
    CPython's `__package__` semantics see (`nptc.pkg`'s own dotted name is
    the package a `from .` inside it counts levels from), unlike a plain
    module, whose package is its parent."""
    parts = module_name.split(".")
    module_file = SRC_ROOT.joinpath(*parts).with_suffix(".py")
    if module_file.is_file():
        return module_file, False
    package_init = SRC_ROOT.joinpath(*parts, "__init__.py")
    if package_init.is_file():
        return package_init, True
    return None


@dataclass(frozen=True)
class _ParsedModule:
    tree: ast.Module
    is_package: bool


@cache
def _parse_module(module_name: str) -> _ParsedModule | None:
    resolved = _resolve_module_file(module_name)
    if resolved is None:
        return None
    path, is_package = resolved
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _ParsedModule(tree=tree, is_package=is_package)


@dataclass(frozen=True)
class _Imports:
    #: local name -> fully-qualified module it refers to: `import x`,
    #: `import x.y.z` (binds only the top-level package name - CPython
    #: never binds the dotted path itself to a local name), `import x as
    #: y`, and (as a *hypothesis*, checked against disk before use)
    #: `from pkg import submodule`-style imports.
    modules: dict[str, str]
    #: local name -> (defining module, original name), for
    #: `from pkg.mod import name` / `from pkg.mod import name as alias`.
    names: dict[str, tuple[str, str]]


def _resolve_relative_module(current_module: str, node: ast.ImportFrom, *, is_package: bool) -> str:
    """CPython's own relative-import resolution (see
    `importlib._bootstrap._resolve_name`): level counts from the
    *package containing* `current_module` - which is `current_module`
    itself when it is a package's `__init__.py`, and its parent otherwise."""
    package = current_module if is_package else ".".join(current_module.split(".")[:-1])
    if node.level == 1:
        base = package
    else:
        bits = package.rsplit(".", node.level - 1)
        base = bits[0]
    return f"{base}.{node.module}" if node.module else base


def _build_import_table(module_name: str, tree: ast.Module, *, is_package: bool) -> _Imports:
    """Collected from anywhere in the module (`ast.walk`, not just
    top-level statements) - a function-scoped import is a real import this
    walker must not be blind to, even though the result is a flat,
    whole-module table rather than a scope-accurate one (see the module
    docstring)."""
    modules: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    # `import a.b.c` binds only the top-level name `a` in
                    # the importing module's namespace - never the dotted
                    # path - so that is the only local name this can add.
                    top = alias.name.split(".")[0]
                    modules[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.module is None and not node.level:
                continue
            base_module = (
                _resolve_relative_module(module_name, node, is_package=is_package)
                if node.level
                else node.module
            )
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


def _flatten_attribute_chain(node: ast.expr) -> list[str] | None:
    """`a.b.c.d` (any length) as `["a", "b", "c", "d"]`, or `None` if the
    chain's base is not a bare name (e.g. `foo().bar` - a call in the
    middle, which this walker does not attempt to resolve)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


def _attribute_call_targets(chain: list[str], imports: _Imports) -> list[tuple[str, str]]:
    """Every `(module, qualname)` a dotted attribute chain could resolve
    to, longest module-path prefix first: `nptc.audit.recording.
    record_change(...)` (chain `["nptc", "audit", "recording",
    "record_change"]`, base `nptc` bound to itself by a plain `import
    nptc.audit.recording`) needs the same three-hop fold as the one-hop
    `audit.record_change(...)` case (`from nptc import audit`) - both are
    just different lengths of the same chain, so one loop handles both
    instead of two separate code paths for "how many attributes are module
    path versus how many are an object/class access". Each candidate is
    checked for real source by `reachable`'s own `_parse_module` call, not
    guessed at here."""
    base, *rest = chain
    if not rest or base not in imports.modules:
        return []
    full_parts = [*imports.modules[base].split("."), *rest]
    return [
        (".".join(full_parts[:split]), ".".join(full_parts[split:]))
        for split in range(len(full_parts) - 1, 0, -1)
    ]


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
        chain = _flatten_attribute_chain(func)
        if chain is not None:
            return _attribute_call_targets(chain, imports)
    return []


def is_resolvable(module_name: str, qualname: str) -> bool:
    """Whether the walker can find source for `qualname` in `module_name`
    at all - `reachable` returning `False` does not distinguish "resolved,
    and no audit call found" from "could not resolve this endpoint's
    source", and a caller that needs that distinction (a route whose
    source cannot be found at all is a walker gap, not evidence the route
    fails to audit) should check this first."""
    parsed = _parse_module(module_name)
    if parsed is None:
        return False
    return _find_function(parsed.tree, qualname) is not None


def reachable(
    module_name: str,
    qualname: str,
    *,
    targets: frozenset[tuple[str, str]] = TARGET_FUNCTIONS,
    _visited: set[tuple[str, str]] | None = None,
) -> bool:
    """Whether `qualname` in `module_name` (module-level function, or a
    dotted `Class.method`) reaches one of `targets` through a static
    `ast.Call` walk confined to `nptc.*` modules under `backend/src`. See
    the module docstring for what this does and does not claim.

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

    parsed = _parse_module(module_name)
    if parsed is None:
        return False
    func_node = _find_function(parsed.tree, qualname)
    if func_node is None:
        return False

    imports = _build_import_table(module_name, parsed.tree, is_package=parsed.is_package)

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
