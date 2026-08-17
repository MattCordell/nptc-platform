"""NFR-07 guard: no code path trusts a JWT claim without verifying the
signature first (issue #43).

Pure ``ast`` over ``backend/src``, no network, modelled on
``test_sql_parameterisation.py`` - including its own positive control over
an inline source string, so this guard cannot rot into an always-pass
(CLAUDE.md's "principal failure mode" rule).

Four rules:

1. No ``jwt.decode``/``jwt.decode_complete`` call anywhere passes
   ``verify_signature: False`` (via ``options=``) or the legacy
   ``verify=False``.
2. ``jwt.decode`` is called only from ``nptc/auth/tokens.py`` - every other
   consumer of a verified token goes through ``TokenVerifier.verify``.
3. ``jwt.get_unverified_header`` is called only from ``nptc/auth/tokens.py``
   and ``nptc/auth/jwks.py`` - the two places the header's unauthenticated
   `alg`/`typ`/`kid` are read for key/algorithm selection, never as a
   trusted claim.
4. Every ``algorithms=`` argument is either the module constant
   (``_ALGORITHMS``) or a literal list containing neither ``"none"`` nor
   any ``HS*`` entry - the check that stops an RS256 realm's public key
   being reused as an HMAC secret.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "backend" / "src"]

_ALLOWED_DECODE_PATH = "backend/src/nptc/auth/tokens.py"
_ALLOWED_UNVERIFIED_HEADER_PATHS = {
    "backend/src/nptc/auth/tokens.py",
    "backend/src/nptc/auth/jwks.py",
}
_ALLOWED_ALGORITHMS_CONSTANT = "_ALGORITHMS"
_DISALLOWED_ALGORITHMS = {"none"}


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


def _is_jwt_module_call(node: ast.Call, attr_name: str) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == attr_name
        and isinstance(func.value, ast.Name)
        and func.value.id == "jwt"
    )


def _dict_disables_signature_verification(node: ast.expr) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values, strict=False):
        if (
            isinstance(key, ast.Constant)
            and key.value == "verify_signature"
            and isinstance(value, ast.Constant)
            and value.value is False
        ):
            return True
    return False


def _algorithms_violation(node: ast.expr) -> str | None:
    """Returns a reason the `algorithms=` value is unsafe, or None."""
    if isinstance(node, ast.Name):
        if node.id == _ALLOWED_ALGORITHMS_CONSTANT:
            return None
        return (
            f"algorithms= references {node.id!r}, not the {_ALLOWED_ALGORITHMS_CONSTANT!r} constant"
        )
    if isinstance(node, ast.List):
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                value = element.value
                if value in _DISALLOWED_ALGORITHMS or value.upper().startswith("HS"):
                    return f"algorithms= literal list contains disallowed entry {value!r}"
        return None
    return "algorithms= is neither the module constant nor a literal list"


def _check_source(source: str, display_path: str) -> list[Violation]:
    violations: list[Violation] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        for keyword in node.keywords:
            if (
                keyword.arg == "verify"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
            ):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "unsafe-verify-kwarg",
                        "call passes verify=False",
                    )
                )
            if keyword.arg == "options" and _dict_disables_signature_verification(keyword.value):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "unsafe-verify-options",
                        "call passes options={'verify_signature': False}",
                    )
                )
            if keyword.arg == "algorithms":
                reason = _algorithms_violation(keyword.value)
                if reason is not None:
                    violations.append(
                        Violation(display_path, node.lineno, "unsafe-algorithms", reason)
                    )

        is_decode_call = _is_jwt_module_call(node, "decode") or _is_jwt_module_call(
            node, "decode_complete"
        )
        if is_decode_call and display_path != _ALLOWED_DECODE_PATH:
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "decode-outside-tokens",
                    "jwt.decode/decode_complete called outside nptc/auth/tokens.py",
                )
            )

        if (
            _is_jwt_module_call(node, "get_unverified_header")
            and display_path not in _ALLOWED_UNVERIFIED_HEADER_PATHS
        ):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "unverified-header-outside-allowed",
                    "jwt.get_unverified_header called outside the allowed modules",
                )
            )

    return violations


def _check_file(path: Path) -> list[Violation]:
    return _check_source(path.read_text(encoding="utf-8"), _display(path))


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


@pytest.mark.req("NFR-07")
def test_no_jwt_trust_without_verification() -> None:
    violations = [v for path in _iter_source_files() for v in _check_file(path)]

    assert not violations, "NFR-07 violation(s) found:\n" + "\n".join(str(v) for v in violations)


def test_guard_flags_known_violations() -> None:
    bad_source = """
import jwt

_ALGORITHMS = ["RS256"]


def unsafe_verify_kwarg(token):
    return jwt.decode(token, key, algorithms=_ALGORITHMS, verify=False)


def unsafe_verify_options(token):
    return jwt.decode(
        token, key, algorithms=_ALGORITHMS, options={"verify_signature": False}
    )


def unsafe_algorithms(token, key):
    return jwt.decode(token, key, algorithms=["HS256"])


def unsafe_none_algorithm(token, key):
    return jwt.decode(token, key, algorithms=["none"])


def decode_outside_tokens(token, key):
    return jwt.decode(token, key, algorithms=_ALGORITHMS)


def unverified_header_outside_allowed(token):
    return jwt.get_unverified_header(token)
"""
    violations = _check_source(bad_source, "nptc/auth/somewhere_else.py")
    rule_counts = Counter(v.rule for v in violations)

    assert rule_counts == Counter(
        {
            "unsafe-verify-kwarg": 1,
            "unsafe-verify-options": 1,
            "unsafe-algorithms": 2,
            "decode-outside-tokens": 5,
            "unverified-header-outside-allowed": 1,
        }
    )
