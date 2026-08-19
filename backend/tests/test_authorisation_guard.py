"""FR-44's greppable acceptance criterion: no authorisation check in
`backend/src` compares against a role-name literal, or even against
`nptc.auth.permissions.Role` itself (issue #44).

Pure `ast` over `backend/src`, modelled directly on
`test_token_verification_guard.py` - including its own positive control
over an inline source string, so this guard cannot rot into an
always-pass (CLAUDE.md's "principal failure mode" rule, and the same
"a guard that vacuously passes is not a guard" discipline
`authz_support.assert_permission_refused` applies at runtime).

Four AST rules, plus one non-AST data check:

1. ``role-name-literal`` - a comparison against a string constant naming a
   role (or `In`/`NotIn` against a literal collection containing one).
2. ``role-enum-comparison`` - a comparison against `Role.X` directly. This
   is the rule that actually satisfies FR-44's own wording ("rather than
   as hard-coded checks against a role enum") - rule 1 alone is bypassed
   by simply writing `Role.ADMINISTRATOR` instead of the string
   `"administrator"`.
3. ``role-membership-test`` - `x in principal.roles` (or `not in`).
4. ``string-permission-argument`` - the permission argument to
   `require_permission`/`has_permission`/`may_act_on` must be a
   `Permission.X` attribute access, never a string literal. Because
   `Permission` is a `StrEnum`, a raw string *would* match by hash in a
   `frozenset[Permission]`, so a typo silently becomes a permanent deny
   (or, worse, a permanent grant if it happens to collide) rather than a
   type error - `mypy --strict` catches most of this, this rule is the
   belt for the untyped edges (e.g. an f-string built at runtime).
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "backend" / "src"]

#: The only modules whose entire job is roles - everywhere else, a
#: comparison against a role name or `Role` member is exactly the
#: hard-coded check FR-44 forbids.
_ALLOWED_PATHS = {
    "backend/src/nptc/auth/permissions.py",
    "backend/src/nptc/auth/principal.py",
    "backend/src/nptc/auth/grants.py",
    "backend/src/nptc/db/models/user_role.py",
}

_ROLE_NAME_LITERALS = {
    "observer",
    "provisional",
    "member",
    "reviewer",
    "administrator",
    "admin",
    "anon",
    "anonymous",
}

_PERMISSION_CHECK_FUNCTIONS = {"require_permission", "has_permission", "may_act_on"}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_role_name_constant(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower() in _ROLE_NAME_LITERALS
    )


def _is_role_name_collection(node: ast.expr) -> bool:
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return False
    return any(_is_role_name_constant(elt) for elt in node.elts)


def _is_role_enum_attribute(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Role"
    )


def _is_roles_attribute(node: ast.expr) -> bool:
    """Matches `<anything>.roles`, e.g. `principal.roles` - the
    `Principal.roles` field a membership test against would be exactly
    the hard-coded role check FR-44 forbids."""
    return isinstance(node, ast.Attribute) and node.attr == "roles"


def _check_compare(node: ast.Compare, display_path: str) -> list[Violation]:
    violations: list[Violation] = []
    operands = [node.left, *node.comparators]

    for left, op, right in zip(operands, node.ops, operands[1:], strict=False):
        if isinstance(op, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)):
            for side in (left, right):
                if _is_role_name_constant(side):
                    violations.append(
                        Violation(
                            display_path,
                            node.lineno,
                            "role-name-literal",
                            f"compares against {side.value!r}",
                        )  # type: ignore[union-attr]
                    )
                if _is_role_enum_attribute(side):
                    violations.append(
                        Violation(
                            display_path,
                            node.lineno,
                            "role-enum-comparison",
                            "compares against a Role member",
                        )
                    )
        if isinstance(op, (ast.In, ast.NotIn)):
            if _is_role_name_constant(left) or _is_role_name_collection(right):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "role-name-literal",
                        "membership test against a role-name literal",
                    )
                )
            if _is_role_enum_attribute(left):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "role-enum-comparison",
                        "membership test of a Role member",
                    )
                )
            if _is_roles_attribute(right):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "role-membership-test",
                        "membership test against a .roles attribute",
                    )
                )
    return violations


def _check_call(node: ast.Call, display_path: str) -> list[Violation]:
    func = node.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else func.attr
        if isinstance(func, ast.Attribute)
        else None
    )
    if name not in _PERMISSION_CHECK_FUNCTIONS:
        return []

    args = list(node.args) + [
        kw.value for kw in node.keywords if kw.arg in {"permission", "own", "any_"}
    ]
    violations: list[Violation] = []
    for arg in args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "string-permission-argument",
                    f"{name}() called with a string literal {arg.value!r} instead of a Permission member",
                )
            )
    return violations


def _check_source(source: str, display_path: str) -> list[Violation]:
    if display_path in _ALLOWED_PATHS:
        return []

    violations: list[Violation] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            violations.extend(_check_compare(node, display_path))
        if isinstance(node, ast.Call):
            violations.extend(_check_call(node, display_path))
    return violations


def _check_file(path: Path) -> list[Violation]:
    return _check_source(path.read_text(encoding="utf-8"), _display(path))


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


@pytest.mark.req("FR-44")
def test_no_authorisation_check_compares_against_a_role_name_or_role_enum() -> None:
    violations = [v for path in _iter_source_files() for v in _check_file(path)]
    assert not violations, "FR-44 violation(s) found:\n" + "\n".join(str(v) for v in violations)


def test_guard_flags_known_violations() -> None:
    bad_source = """
from nptc.auth.permissions import Role
from nptc.auth.authorisation import require_permission, has_permission, may_act_on


def role_name_literal_eq(role):
    return role == "administrator"


def role_name_literal_in(role):
    return role in ["administrator", "reviewer"]


def role_enum_eq(role):
    return role == Role.ADMINISTRATOR


def role_enum_in(role):
    return role in {Role.ADMINISTRATOR}


def role_membership_test(principal):
    return Role.ADMINISTRATOR in principal.roles


def string_permission_argument(principal):
    return require_permission("release.publish")(principal)


def string_has_permission(principal):
    return has_permission(principal, "release.publish")
"""
    violations = _check_source(bad_source, "nptc/somewhere_else.py")
    rule_counts = Counter(v.rule for v in violations)

    assert rule_counts == Counter(
        {
            "role-name-literal": 2,
            "role-enum-comparison": 2,
            "role-membership-test": 1,
            "string-permission-argument": 2,
        }
    )


def test_allowlisted_modules_are_exempt_from_this_guard() -> None:
    """The four modules whose entire job is roles - proven exempt here so
    a future edit to `_ALLOWED_PATHS` is itself reviewable against a
    passing test, not just against the main assertion silently passing
    because those files happen to have no violations today."""
    source = 'x = (Role.ADMINISTRATOR == role)\ny = ("administrator" == role)\n'
    for path in [
        "backend/src/nptc/auth/permissions.py",
        "backend/src/nptc/auth/principal.py",
        "backend/src/nptc/auth/grants.py",
        "backend/src/nptc/db/models/user_role.py",
    ]:
        assert _check_source(source, path) == []


@pytest.mark.req("FR-44")
def test_no_permission_value_string_smuggles_a_role_name_as_a_permission() -> None:
    """Blocks `permission == "is_admin"`-shaped smuggling: a `Permission`
    value that itself reads as a role name would let a role-name
    comparison hide behind what looks like a permission check."""
    from nptc.auth.permissions import Permission

    for permission in Permission:
        assert permission.value not in _ROLE_NAME_LITERALS
