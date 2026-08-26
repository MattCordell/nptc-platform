#!/usr/bin/env python3
"""Breaking-change detection for `docs/api/openapi.json` (issue #206, FR-20).

`.github/workflows/openapi.yml` (issue #143) already proves the committed document
is exactly what the app serves and that it is a valid OpenAPI 3.1 document. Neither
that check nor anything else in the repo asks whether a *change* to the document
would break an existing consumer - the concrete one being the generated TypeScript
client (issue #147). This script answers that question for a PR's `head` document
against its `base` document.

Detection is direction-aware: the same schema edit is breaking on the request side
(narrowing what a client may send) or the response side (removing what a client may
read), never both. A request parameter/schema is anything under an operation's
`parameters` or `requestBody`; everything under `responses` is a response schema.
`$ref`s into `#/components/schemas/*` are resolved **against their own document** -
a base `$ref` against base's component map, a head `$ref` against head's - since a
`$ref` string is normally unchanged across an edit and the edit lives inside the
referenced component, not in the ref itself. Resolution has a cycle guard so a
self-referential schema does not recurse forever.

Traversal descends into object `properties` and array `items`, and pairwise into
`allOf` branches (by position, on the reasonable assumption that a schema's `allOf`
list is not reordered independently of a semantic change). `anyOf`/`oneOf` branches
are walked only for the null-branch check in `_diff_scalar_schema` - comparing
arbitrary reorderable union branches pairwise would misattribute edits to the wrong
branch, so that is intentionally out of scope here.

Additive changes - a new path, a new optional parameter, a new response property, a
relaxed request constraint, or any description/summary/title edit - are not findings.

Usage:
  uv run python scripts/openapi_breaking_check.py --base old.json --head new.json
  uv run python scripts/openapi_breaking_check.py --base old.json --head new.json --format github
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

Document = dict[str, Any]
Schema = dict[str, Any]

# Constraint keys that narrow a value's acceptable range or shape, and the
# direction ("lower" or "higher") in which a change to that key is a narrowing
# (i.e. potentially breaking on the request side / loosening on the response side).
_TIGHTENS_WHEN_LOWERED = ("maximum", "maxLength", "maxItems")
_TIGHTENS_WHEN_RAISED = ("minimum", "minLength", "minItems")

_2XX_PREFIX = "2"

_REF_PREFIX = "#/components/schemas/"


class Finding:
    """One breaking change: a stable dotted `path` and a human-readable `message`."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Finding) and self.path == other.path and self.message == other.message
        )

    def __hash__(self) -> int:
        return hash((self.path, self.message))


def _resolve(schema: Any, components: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """Resolve a `$ref` chain into `#/components/schemas/*`, guarding against cycles.

    A cycle resolves to the unresolved `{"$ref": ...}` stub rather than raising or
    looping forever - a self-referential schema (a tree node with children of the
    same type, say) is not something this checker's rules need to see past.
    """
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
        return schema
    name = ref[len(_REF_PREFIX) :]
    if name in seen or name not in components:
        return schema
    return _resolve(components[name], components, seen | {name})


def _ref_name(schema: Any) -> str | None:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    return ref if isinstance(ref, str) else None


def _scalar_type(schema: Any) -> Any:
    return schema.get("type") if isinstance(schema, dict) else None


def _enum(schema: Any) -> list[Any] | None:
    value = schema.get("enum") if isinstance(schema, dict) else None
    return value if isinstance(value, list) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_null_branch(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    for branch in schema.get("anyOf", []) + schema.get("oneOf", []):
        if isinstance(branch, dict) and branch.get("type") == "null":
            return True
    return False


def _diff_scalar_schema(
    base: Any,
    head: Any,
    base_components: dict[str, Any],
    head_components: dict[str, Any],
    path: str,
    *,
    narrowing_direction: str,
) -> list[Finding]:
    """Compare two schemas - each resolved against its *own* document's components -
    for `narrowing_direction`.

    `narrowing_direction` is `"request"` (a finding is a narrowing of what a client
    may send) or `"response"` (a finding is a removal of what a client may read).
    """
    findings: list[Finding] = []
    base = _resolve(base, base_components)
    head = _resolve(head, head_components)
    if not isinstance(base, dict) or not isinstance(head, dict):
        return findings

    base_type, head_type = _scalar_type(base), _scalar_type(head)
    if base_type is not None and head_type is not None and base_type != head_type:
        findings.append(Finding(path, f"type changed from '{base_type}' to '{head_type}'"))

    base_enum, head_enum = _enum(base), _enum(head)
    if base_enum is not None and head_enum is not None:
        if narrowing_direction == "request":
            removed = set(base_enum) - set(head_enum)
            if removed:
                findings.append(Finding(path, f"enum values removed: {sorted(removed, key=str)}"))
        else:
            added = set(head_enum) - set(base_enum)
            if added:
                findings.append(
                    Finding(
                        path,
                        f"enum values added (breaks an exhaustive switch): {sorted(added, key=str)}",
                    )
                )

    if narrowing_direction == "request":
        for key in _TIGHTENS_WHEN_LOWERED:
            b, h = base.get(key), head.get(key)
            if isinstance(b, (int, float)) and isinstance(h, (int, float)) and h < b:
                findings.append(Finding(path, f"'{key}' lowered from {b} to {h}"))
        for key in _TIGHTENS_WHEN_RAISED:
            b, h = base.get(key), head.get(key)
            if isinstance(b, (int, float)) and isinstance(h, (int, float)) and h > b:
                findings.append(Finding(path, f"'{key}' raised from {b} to {h}"))
        base_pattern, head_pattern = base.get("pattern"), head.get("pattern")
        if base_pattern is not None and head_pattern is not None and base_pattern != head_pattern:
            findings.append(
                Finding(path, f"'pattern' changed from {base_pattern!r} to {head_pattern!r}")
            )
        if _has_null_branch(base) and not _has_null_branch(head):
            findings.append(Finding(path, "no longer accepts null"))

    return findings


def _diff_object_schema(
    base: Any,
    head: Any,
    base_components: dict[str, Any],
    head_components: dict[str, Any],
    path: str,
    *,
    narrowing_direction: str,
    seen: frozenset[tuple[str | None, str | None]] = frozenset(),
) -> list[Finding]:
    # Guard against a $ref cycle (e.g. a tree node whose child is the same type):
    # once a (base-ref, head-ref) pair has been visited on this path, stop
    # recursing rather than resolving it again and diffing forever.
    ref_pair = (_ref_name(base), _ref_name(head))
    if ref_pair != (None, None) and ref_pair in seen:
        return []
    seen = seen | {ref_pair}

    findings: list[Finding] = []
    base = _resolve(base, base_components)
    head = _resolve(head, head_components)
    if not isinstance(base, dict) or not isinstance(head, dict):
        return findings

    findings.extend(
        _diff_scalar_schema(
            base,
            head,
            base_components,
            head_components,
            path,
            narrowing_direction=narrowing_direction,
        )
    )

    base_required = set(base.get("required", []) if isinstance(base.get("required"), list) else [])
    head_required = set(head.get("required", []) if isinstance(head.get("required"), list) else [])
    base_props = base.get("properties", {}) if isinstance(base.get("properties"), dict) else {}
    head_props = head.get("properties", {}) if isinstance(head.get("properties"), dict) else {}

    if narrowing_direction == "request":
        newly_required = head_required - base_required
        if newly_required:
            findings.append(Finding(path, f"properties became required: {sorted(newly_required)}"))
    else:
        removed_props = base_props.keys() - head_props.keys()
        if removed_props:
            findings.append(Finding(path, f"properties removed: {sorted(removed_props)}"))
        removed_required = base_required - head_required
        # A required property that is also removed from `properties` is already
        # reported above; only report a bare required->optional demotion once.
        removed_required -= removed_props
        if removed_required:
            findings.append(
                Finding(
                    path,
                    f"properties demoted from required to optional: {sorted(removed_required)}",
                )
            )

    for name in sorted(base_props.keys() & head_props.keys()):
        findings.extend(
            _diff_object_schema(
                base_props[name],
                head_props[name],
                base_components,
                head_components,
                f"{path}.{name}",
                narrowing_direction=narrowing_direction,
                seen=seen,
            )
        )

    base_items, head_items = base.get("items"), head.get("items")
    if base_items is not None and head_items is not None:
        findings.extend(
            _diff_object_schema(
                base_items,
                head_items,
                base_components,
                head_components,
                f"{path}[]",
                narrowing_direction=narrowing_direction,
                seen=seen,
            )
        )

    base_all_of = _as_list(base.get("allOf"))
    head_all_of = _as_list(head.get("allOf"))
    for index, (base_branch, head_branch) in enumerate(zip(base_all_of, head_all_of, strict=False)):
        findings.extend(
            _diff_object_schema(
                base_branch,
                head_branch,
                base_components,
                head_components,
                f"{path}.allOf[{index}]",
                narrowing_direction=narrowing_direction,
                seen=seen,
            )
        )

    return findings


def _diff_parameters(
    base_op: Schema,
    head_op: Schema,
    base_components: dict[str, Any],
    head_components: dict[str, Any],
    path: str,
) -> list[Finding]:
    findings: list[Finding] = []
    base_params = {p["name"]: p for p in base_op.get("parameters", []) if "name" in p}
    head_params = {p["name"]: p for p in head_op.get("parameters", []) if "name" in p}

    for name in sorted(base_params.keys() - head_params.keys()):
        findings.append(Finding(f"{path}.parameters.{name}", "parameter removed"))

    for name in sorted(head_params.keys() - base_params.keys()):
        if head_params[name].get("required") is True:
            findings.append(Finding(f"{path}.parameters.{name}", "new required parameter added"))

    for name in sorted(base_params.keys() & head_params.keys()):
        base_p, head_p = base_params[name], head_params[name]
        if base_p.get("required") is not True and head_p.get("required") is True:
            findings.append(Finding(f"{path}.parameters.{name}", "parameter became required"))
        findings.extend(
            _diff_object_schema(
                base_p.get("schema", {}),
                head_p.get("schema", {}),
                base_components,
                head_components,
                f"{path}.parameters.{name}",
                narrowing_direction="request",
            )
        )

    return findings


def _request_body_schema(op: Schema) -> Any:
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content", {})
    media = content.get("application/json", {})
    return media.get("schema") if isinstance(media, dict) else None


def _response_schema(op: Schema, status: str) -> Any:
    responses = op.get("responses", {})
    response = responses.get(status)
    if not isinstance(response, dict):
        return None
    content = response.get("content", {})
    media = content.get("application/json", {})
    return media.get("schema") if isinstance(media, dict) else None


def _diff_operation(
    base_op: Schema,
    head_op: Schema,
    base_components: dict[str, Any],
    head_components: dict[str, Any],
    path: str,
) -> list[Finding]:
    findings: list[Finding] = []

    findings.extend(_diff_parameters(base_op, head_op, base_components, head_components, path))

    base_body = _request_body_schema(base_op)
    head_body = _request_body_schema(head_op)
    if base_body is not None and head_body is not None:
        findings.extend(
            _diff_object_schema(
                base_body,
                head_body,
                base_components,
                head_components,
                f"{path}.requestBody",
                narrowing_direction="request",
            )
        )

    base_statuses = {s for s in base_op.get("responses", {}) if s.startswith(_2XX_PREFIX)}
    head_statuses = {s for s in head_op.get("responses", {}) if s.startswith(_2XX_PREFIX)}
    for status in sorted(base_statuses - head_statuses):
        findings.append(Finding(f"{path}.responses.{status}", "response status code removed"))

    for status in sorted(base_statuses & head_statuses):
        base_resp = _response_schema(base_op, status)
        head_resp = _response_schema(head_op, status)
        if base_resp is not None and head_resp is not None:
            findings.extend(
                _diff_object_schema(
                    base_resp,
                    head_resp,
                    base_components,
                    head_components,
                    f"{path}.responses.{status}",
                    narrowing_direction="response",
                )
            )

    return findings


def find_breaking_changes(base: Document, head: Document) -> list[Finding]:
    """Return every breaking change in `head` relative to `base`, order-stable."""
    findings: list[Finding] = []
    base_components = base.get("components", {}).get("schemas", {})
    head_components = head.get("components", {}).get("schemas", {})
    base_paths = base.get("paths", {}) if isinstance(base.get("paths"), dict) else {}
    head_paths = head.get("paths", {}) if isinstance(head.get("paths"), dict) else {}

    for path_name in sorted(base_paths.keys() - head_paths.keys()):
        findings.append(Finding(path_name, "path removed"))

    for path_name in sorted(base_paths.keys() & head_paths.keys()):
        base_path_item = base_paths[path_name]
        head_path_item = head_paths[path_name]
        base_methods = {
            m for m in base_path_item if m.lower() in ("get", "post", "put", "patch", "delete")
        }
        head_methods = {
            m for m in head_path_item if m.lower() in ("get", "post", "put", "patch", "delete")
        }
        for method in sorted(base_methods - head_methods):
            findings.append(Finding(f"{path_name} {method.upper()}", "operation removed"))
        for method in sorted(base_methods & head_methods):
            findings.extend(
                _diff_operation(
                    base_path_item[method],
                    head_path_item[method],
                    base_components,
                    head_components,
                    f"{path_name} {method.upper()}",
                )
            )

    return findings


def _load(path: Path) -> Document:
    document: Document = json.loads(path.read_text(encoding="utf-8"))
    return document


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path, help="the base ref's openapi.json")
    parser.add_argument("--head", required=True, type=Path, help="the PR's openapi.json")
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="'github' also emits ::error:: workflow-command annotations",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        base = _load(args.base)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"openapi_breaking_check: could not read --base {args.base}: {exc}", file=sys.stderr)
        return 2

    try:
        head = _load(args.head)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"openapi_breaking_check: could not read --head {args.head}: {exc}", file=sys.stderr)
        return 2

    findings = find_breaking_changes(base, head)

    if not findings:
        print("openapi_breaking_check: no breaking changes detected.")
        return 0

    for finding in findings:
        if args.format == "github":
            print(f"::error::openapi breaking change at {finding.path}: {finding.message}")
        else:
            print(f"BREAKING: {finding}")

    print(f"openapi_breaking_check: {len(findings)} breaking change(s) detected.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
