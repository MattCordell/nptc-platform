"""FR-77's named enforcement mechanism (ADR-0013 SS5): a pure-`ast` guard
proving no `switch`/`match` on property datatype exists outside
`backend/src/nptc/registry/datatypes/`.

Modelled directly on `test_sql_parameterisation.py` (NFR-22's guard): same
`SCAN_DIRS` (imported from `ast_guard_support`, not a second constant that
happens to match), same frozen `Violation` with `file:line: [rule] detail`,
same print-the-whole-list convention, same inline-source positive control
asserting an exact per-rule `Counter`.

No fixtures - this walks source files with `ast`, it never touches a
database, and it joins `test_settings.py` and `test_sql_parameterisation.py`
as tests that must not start Docker (`conftest.py`'s `migrated` fixture is
deliberately not autouse for exactly this reason).

The known-datatype literal set is imported from `BUILTIN_DATATYPES`
(`nptc.registry.datatypes`), never hardcoded here - a hardcoded list would
make this guard a second enumeration of the valid set, i.e. the guard would
itself violate the requirement it enforces. `backend/src/nptc/registry/datatypes/`
is excluded from **this** guard only, never from `test_sql_parameterisation.py`;
`backend/tests` is outside `SCAN_DIRS` either way, so #53's synthetic-datatype
test may use datatype literals freely.

A "datatype-bearing expression" is recognised **by name, not by type** - the
guard is syntactic: `Attribute.attr == "datatype"`, `Name.id == "datatype"`
or `*_datatype`, or a `Subscript` with a constant `"datatype"` slice.

Four rules:

1. `datatype-match` - an `ast.Match` whose subject is a datatype-bearing
   expression.
2. `datatype-compare` - `Eq`/`NotEq`/`In`/`NotIn` against a string constant,
   or a tuple/list/set of them, on one side of the comparison.
3. `datatype-dispatch-table` - an `ast.Dict` with two or more keys, all
   string constants, whose key set is either a **subset** of the known
   datatypes, or a **superset** of them (catches "all five plus a
   `default`/`fallback` key", which a subset test alone would miss because
   it is a superset).
4. `registry-imports-sibling` - an import inside `registry/**` naming
   `nptc.db`, `nptc.catalogue`, `nptc.exports`, `nptc.api`, `nptc.validation`,
   `nptc.submissions`, `nptc.releases`, or `nptc.jobs` (ADR-0013 SS2's leaf
   rule made mechanical).

**Worked false-positive analysis**: `{"code": "12345", "system":
"http://snomed.info/sct"}` is not flagged - `"system"` is not a datatype
literal, and neither the subset nor the superset test matches a key set that
shares no elements with the known datatypes at all. `if binding.code ==
"code":` is not flagged - `.code` is not `.datatype`. A one-key dispatch
dict is an accepted gap.

**A fifth rule is explicitly rejected**: "any bare string literal equal to a
known datatype". `code`, `string` and `url` are ordinary English words and
ordinary JSON keys elsewhere in this codebase; such a rule fires dozens of
times on day one and gets suppressed within a week, which is worse than not
having it.

**mypy's complementary half**: `DatatypeHandler` is a `Protocol`,
`build_builtin_handlers` is annotated `-> tuple[DatatypeHandler, ...]`, so a
missing member or a drifted signature is a `mypy --strict` error at the
registration site. Two mechanisms because they prove opposite things: mypy
proves a handler is *complete*; this guard proves dispatch exists *nowhere
else*.

**Named limits, verbatim, so review knows its job**:

1. Proxy switches - `if definition.binding_target is not None:` is
   `datatype == "code"` in disguise, as is `if "minimum" in
   definition.constraints:` - the most likely real violation and the one
   hardest for a syntactic guard to catch.
2. Reflective dispatch (`getattr(self, f"handle_{datatype}")`).
3. Dispatch expressed in SQL (a static literal `CASE WHEN pd.datatype =
   'decimal'` - NFR-22's guard bans dynamic SQL *text*, not this).
4. The frontend and generated client.
5. One handler branching on another datatype's name inside
   `registry/datatypes/`, excluded by construction.
6. A dispatch dict keyed on the known datatypes plus one extra
   `"default"`/`"fallback"` key - the superset case rule 3 also catches,
   named here anyway since it is the shape most likely to be written
   unthinkingly.
"""

from __future__ import annotations

import ast
import importlib.util
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

# `backend/tests` has no `__init__.py` (pytest's `--import-mode=importlib`),
# so a plain `from ast_guard_support import ...` cannot resolve.
_ast_guard_support_spec = importlib.util.spec_from_file_location(
    "_ast_guard_support", Path(__file__).parent / "ast_guard_support.py"
)
assert _ast_guard_support_spec is not None and _ast_guard_support_spec.loader is not None
_ast_guard_support = importlib.util.module_from_spec(_ast_guard_support_spec)
_ast_guard_support_spec.loader.exec_module(_ast_guard_support)

REPO_ROOT = _ast_guard_support.REPO_ROOT
SCAN_DIRS = _ast_guard_support.SCAN_DIRS
_display = _ast_guard_support.display_path

from nptc.registry.datatypes import BUILTIN_DATATYPES  # noqa: E402

_KNOWN_DATATYPES = frozenset(BUILTIN_DATATYPES)

#: `backend/src/nptc/registry/datatypes/` is excluded from this guard only -
#: it is the one place a datatype switch is allowed to live.
_EXCLUDED_DIR_SUFFIX = str(Path("nptc") / "registry" / "datatypes")

#: ADR-0013 SS2's leaf rule: `nptc.registry` may import `nptc_shared`,
#: SQLAlchemy, `jsonschema` and the stdlib, and nothing else from `nptc`.
_FORBIDDEN_SIBLING_IMPORTS = frozenset(
    {
        "nptc.db",
        "nptc.catalogue",
        "nptc.exports",
        "nptc.api",
        "nptc.validation",
        "nptc.submissions",
        "nptc.releases",
        "nptc.jobs",
    }
)
_REGISTRY_DIR_SUFFIX = str(Path("nptc") / "registry")


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _is_datatype_bearing(node: ast.expr) -> bool:
    """By name, not by type - this guard is syntactic (module docstring)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "datatype" or node.attr.endswith("_datatype")
    if isinstance(node, ast.Name):
        return node.id == "datatype" or node.id.endswith("_datatype")
    if isinstance(node, ast.Subscript):
        sub = node.slice
        return isinstance(sub, ast.Constant) and sub.value == "datatype"
    return False


def _string_constant_set(node: ast.expr) -> frozenset[str] | None:
    """`node`'s value if it is a string constant, or the set of its values
    if it is a tuple/list/set literal of string constants - `None` if it is
    neither (used by both the compare and dispatch-table rules)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        values: set[str] = set()
        for element in node.elts:
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                return None
            values.add(element.value)
        return frozenset(values)
    return None


def _check_source(source: str, display_path: str) -> list[Violation]:
    violations: list[Violation] = []
    tree = ast.parse(source)
    normalised_path = display_path.replace("\\", "/")
    is_in_datatypes_dir = _EXCLUDED_DIR_SUFFIX.replace("\\", "/") in normalised_path
    is_in_registry_dir = _REGISTRY_DIR_SUFFIX.replace("\\", "/") in normalised_path

    for node in ast.walk(tree):
        if not is_in_datatypes_dir:
            if isinstance(node, ast.Match) and _is_datatype_bearing(node.subject):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "datatype-match",
                        "match statement dispatches on a datatype-bearing expression",
                    )
                )

            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                op = node.ops[0]
                if isinstance(op, ast.Eq | ast.NotEq | ast.In | ast.NotIn):
                    left, right = node.left, node.comparators[0]
                    left_is_datatype = _is_datatype_bearing(left)
                    right_is_datatype = _is_datatype_bearing(right)
                    other = right if left_is_datatype else left
                    if (left_is_datatype or right_is_datatype) and _string_constant_set(
                        other
                    ) is not None:
                        violations.append(
                            Violation(
                                display_path,
                                node.lineno,
                                "datatype-compare",
                                "comparison of a datatype-bearing expression against a "
                                "string constant",
                            )
                        )

            if isinstance(node, ast.Dict) and len(node.keys) >= 2:
                key_values: set[str] = set()
                all_string_keys = True
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        key_values.add(key.value)
                    else:
                        all_string_keys = False
                        break
                if all_string_keys:
                    key_set = frozenset(key_values)
                    if key_set and (key_set <= _KNOWN_DATATYPES or key_set >= _KNOWN_DATATYPES):
                        violations.append(
                            Violation(
                                display_path,
                                node.lineno,
                                "datatype-dispatch-table",
                                f"dict literal keyed on the known datatype set ({sorted(key_set)})",
                            )
                        )

        if is_in_registry_dir:
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                module_names = [node.module]
            for module_name in module_names:
                if any(
                    module_name == forbidden or module_name.startswith(forbidden + ".")
                    for forbidden in _FORBIDDEN_SIBLING_IMPORTS
                ):
                    violations.append(
                        Violation(
                            display_path,
                            node.lineno,
                            "registry-imports-sibling",
                            f"registry/ imports {module_name!r}, a non-leaf sibling package",
                        )
                    )

    return violations


def _check_file(path: Path) -> list[Violation]:
    return _check_source(path.read_text(encoding="utf-8"), _display(path))


@pytest.mark.req("FR-77")
def test_no_datatype_dispatch_outside_registry_datatypes() -> None:
    violations = [
        v
        for v in (
            violation
            for path in _ast_guard_support.iter_source_files(SCAN_DIRS)
            for violation in _check_file(path)
        )
        if v.rule != "registry-imports-sibling"
    ]
    assert not violations, "FR-77 violation(s) found:\n" + "\n".join(str(v) for v in violations)


@pytest.mark.req("FR-77")
def test_registry_never_imports_a_non_leaf_sibling_package() -> None:
    violations = [
        v
        for path in _ast_guard_support.iter_source_files(SCAN_DIRS)
        for v in _check_file(path)
        if v.rule == "registry-imports-sibling"
    ]
    assert not violations, "FR-77 violation(s) found:\n" + "\n".join(str(v) for v in violations)


def test_guard_flags_known_violations() -> None:
    """Positive control run over inline source, not real files - a
    refactor that quietly makes the walker match nothing still fails
    loudly here, rather than this guard rotting into one that always
    passes (CLAUDE.md's "principal failure mode" rule)."""
    bad_source = """
def dispatch_by_match(datatype):
    match datatype:
        case "string":
            return "text"
        case "decimal":
            return "number"


def dispatch_by_compare(prop):
    if prop.datatype == "code":
        return True
    return False


DISPATCH_TABLE = {
    "code": lambda v: v,
    "string": lambda v: v,
    "decimal": lambda v: v,
    "positiveInt": lambda v: v,
    "url": lambda v: v,
}

DISPATCH_TABLE_WITH_FALLBACK = {
    "code": 1,
    "string": 2,
    "decimal": 3,
    "positiveInt": 4,
    "url": 5,
    "default": 0,
}


def not_a_violation(binding):
    # "system" is not a datatype literal - no rule fires here.
    return {"code": "12345", "system": "http://snomed.info/sct"}


def also_not_a_violation(binding):
    # .code is not .datatype
    if binding.code == "code":
        return True
    return False
"""
    violations = _check_source(bad_source, "<positive-control>")
    rule_counts = Counter(v.rule for v in violations)

    # datatype-match: the one `match` statement (1). datatype-compare:
    # `prop.datatype == "code"` (1). datatype-dispatch-table:
    # DISPATCH_TABLE (a subset match - exactly the known set) and
    # DISPATCH_TABLE_WITH_FALLBACK (a superset match - the known set plus
    # "default") (2). `not_a_violation`'s dict shares no elements with the
    # known set (neither subset nor superset) and is correctly not flagged;
    # `also_not_a_violation`'s `.code == "code"` is not `.datatype` and is
    # correctly not flagged either.
    assert rule_counts == Counter(
        {
            "datatype-match": 1,
            "datatype-compare": 1,
            "datatype-dispatch-table": 2,
        }
    )


def test_guard_flags_a_sibling_import_inside_registry() -> None:
    bad_source = "from nptc.db import models\n"
    violations = _check_source(bad_source, "backend/src/nptc/registry/definitions.py")
    assert [v.rule for v in violations] == ["registry-imports-sibling"]


def test_guard_does_not_flag_a_sibling_import_inside_datatypes() -> None:
    """Registry code excluded from the dispatch rules is *not* excluded
    from the leaf-import rule - only `registry/datatypes/` earns the
    dispatch-rule exclusion (module docstring)."""
    bad_source = "from nptc.db import models\n"
    violations = _check_source(bad_source, "backend/src/nptc/registry/datatypes/duration.py")
    assert [v.rule for v in violations] == ["registry-imports-sibling"]
