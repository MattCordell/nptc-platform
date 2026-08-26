"""Unit tests for scripts/openapi_breaking_check.py (issue #206, FR-20).

Each rule gets a test asserting its principal failure mode (the breaking edit is
detected) alongside the matching non-breaking counterpart (the additive edit is not),
per CLAUDE.md's testing conventions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openapi_breaking_check as obc

ROOT = Path(__file__).resolve().parent.parent.parent


def _doc(paths: dict[str, Any], components: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths,
        "components": {"schemas": components or {}},
    }


def _op(
    *,
    parameters: list[dict[str, Any]] | None = None,
    request_schema: dict[str, Any] | None = None,
    response_schema: dict[str, Any] | None = None,
    status: str = "200",
    extra_statuses: dict[str, Any] | None = None,
) -> dict[str, Any]:
    op: dict[str, Any] = {"parameters": parameters or []}
    if request_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": request_schema}}}
    responses = {}
    if response_schema is not None:
        responses[status] = {"content": {"application/json": {"schema": response_schema}}}
    if extra_statuses:
        responses.update(extra_statuses)
    op["responses"] = responses
    return op


def _messages(findings: list[obc.Finding]) -> list[str]:
    return [str(f) for f in findings]


# --- structural -------------------------------------------------------------


def test_removed_path_is_breaking() -> None:
    base = _doc({"/a": {"get": _op()}, "/b": {"get": _op()}})
    head = _doc({"/a": {"get": _op()}})
    findings = obc.find_breaking_changes(base, head)
    assert any("path removed" in m for m in _messages(findings))


def test_added_path_is_not_breaking() -> None:
    base = _doc({"/a": {"get": _op()}})
    head = _doc({"/a": {"get": _op()}, "/b": {"get": _op()}})
    assert obc.find_breaking_changes(base, head) == []


def test_removed_operation_is_breaking() -> None:
    base = _doc({"/a": {"get": _op(), "post": _op()}})
    head = _doc({"/a": {"get": _op()}})
    findings = obc.find_breaking_changes(base, head)
    assert any("operation removed" in m for m in _messages(findings))


def test_added_operation_is_not_breaking() -> None:
    base = _doc({"/a": {"get": _op()}})
    head = _doc({"/a": {"get": _op(), "post": _op()}})
    assert obc.find_breaking_changes(base, head) == []


def test_removed_2xx_response_status_is_breaking() -> None:
    base = _doc({"/a": {"get": _op(extra_statuses={"200": {}, "201": {}})}})
    head = _doc({"/a": {"get": _op(extra_statuses={"200": {}})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("response status code removed" in m for m in _messages(findings))


def test_added_2xx_response_status_is_not_breaking() -> None:
    base = _doc({"/a": {"get": _op(extra_statuses={"200": {}})}})
    head = _doc({"/a": {"get": _op(extra_statuses={"200": {}, "201": {}})}})
    assert obc.find_breaking_changes(base, head) == []


def test_removed_non_2xx_response_status_is_not_breaking() -> None:
    base = _doc({"/a": {"get": _op(extra_statuses={"200": {}, "404": {}})}})
    head = _doc({"/a": {"get": _op(extra_statuses={"200": {}})}})
    assert obc.find_breaking_changes(base, head) == []


# --- request side: parameters ------------------------------------------------


def test_removed_parameter_is_breaking() -> None:
    base = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": False, "schema": {"type": "string"}}]
                )
            }
        }
    )
    head = _doc({"/a": {"get": _op(parameters=[])}})
    findings = obc.find_breaking_changes(base, head)
    assert any("parameter removed" in m for m in _messages(findings))


def test_added_optional_parameter_is_not_breaking() -> None:
    base = _doc({"/a": {"get": _op(parameters=[])}})
    head = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": False, "schema": {"type": "string"}}]
                )
            }
        }
    )
    assert obc.find_breaking_changes(base, head) == []


def test_new_required_parameter_is_breaking() -> None:
    base = _doc({"/a": {"get": _op(parameters=[])}})
    head = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": True, "schema": {"type": "string"}}]
                )
            }
        }
    )
    findings = obc.find_breaking_changes(base, head)
    assert any("new required parameter added" in m for m in _messages(findings))


def test_parameter_flipped_to_required_is_breaking() -> None:
    base = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": False, "schema": {"type": "string"}}]
                )
            }
        }
    )
    head = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": True, "schema": {"type": "string"}}]
                )
            }
        }
    )
    findings = obc.find_breaking_changes(base, head)
    assert any("parameter became required" in m for m in _messages(findings))


def test_parameter_flipped_to_optional_is_not_breaking() -> None:
    base = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": True, "schema": {"type": "string"}}]
                )
            }
        }
    )
    head = _doc(
        {
            "/a": {
                "get": _op(
                    parameters=[{"name": "q", "required": False, "schema": {"type": "string"}}]
                )
            }
        }
    )
    assert obc.find_breaking_changes(base, head) == []


# --- request side: schema constraints ----------------------------------------


def test_request_enum_value_removed_is_breaking() -> None:
    schema = {"type": "string", "enum": ["a", "b"]}
    narrowed = {"type": "string", "enum": ["a"]}
    base = _doc({"/a": {"post": _op(request_schema=schema)}})
    head = _doc({"/a": {"post": _op(request_schema=narrowed)}})
    findings = obc.find_breaking_changes(base, head)
    assert any("enum values removed" in m for m in _messages(findings))


def test_request_enum_value_added_is_not_breaking() -> None:
    schema = {"type": "string", "enum": ["a"]}
    widened = {"type": "string", "enum": ["a", "b"]}
    base = _doc({"/a": {"post": _op(request_schema=schema)}})
    head = _doc({"/a": {"post": _op(request_schema=widened)}})
    assert obc.find_breaking_changes(base, head) == []


def test_request_scalar_type_changed_is_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "string"})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "integer"})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("type changed" in m for m in _messages(findings))


def test_request_max_lowered_is_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "integer", "maximum": 200})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "integer", "maximum": 50})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("'maximum' lowered" in m for m in _messages(findings))


def test_request_max_raised_is_not_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "integer", "maximum": 50})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "integer", "maximum": 200})}})
    assert obc.find_breaking_changes(base, head) == []


def test_request_min_raised_is_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "integer", "minimum": 1})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "integer", "minimum": 5})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("'minimum' raised" in m for m in _messages(findings))


def test_request_min_lowered_is_not_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "integer", "minimum": 5})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "integer", "minimum": 1})}})
    assert obc.find_breaking_changes(base, head) == []


def test_request_pattern_changed_is_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"type": "string", "pattern": "^A"})}})
    head = _doc({"/a": {"post": _op(request_schema={"type": "string", "pattern": "^B"})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("'pattern' changed" in m for m in _messages(findings))


def test_request_null_branch_removed_is_breaking() -> None:
    base = _doc(
        {"/a": {"post": _op(request_schema={"anyOf": [{"type": "string"}, {"type": "null"}]})}}
    )
    head = _doc({"/a": {"post": _op(request_schema={"anyOf": [{"type": "string"}]})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("no longer accepts null" in m for m in _messages(findings))


def test_request_null_branch_added_is_not_breaking() -> None:
    base = _doc({"/a": {"post": _op(request_schema={"anyOf": [{"type": "string"}]})}})
    head = _doc(
        {"/a": {"post": _op(request_schema={"anyOf": [{"type": "string"}, {"type": "null"}]})}}
    )
    assert obc.find_breaking_changes(base, head) == []


def test_request_property_became_required_is_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    narrowed = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    base = _doc({"/a": {"post": _op(request_schema=schema)}})
    head = _doc({"/a": {"post": _op(request_schema=narrowed)}})
    findings = obc.find_breaking_changes(base, head)
    assert any("became required" in m for m in _messages(findings))


def test_request_property_became_optional_is_not_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    widened = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    base = _doc({"/a": {"post": _op(request_schema=schema)}})
    head = _doc({"/a": {"post": _op(request_schema=widened)}})
    assert obc.find_breaking_changes(base, head) == []


def test_request_constraint_relaxed_via_ref_is_not_breaking() -> None:
    components = {
        "Widget": {"type": "object", "properties": {"n": {"type": "integer", "maximum": 5}}}
    }
    base = _doc(
        {"/a": {"post": _op(request_schema={"$ref": "#/components/schemas/Widget"})}}, components
    )
    widened_components = {
        "Widget": {"type": "object", "properties": {"n": {"type": "integer", "maximum": 50}}}
    }
    head = _doc(
        {"/a": {"post": _op(request_schema={"$ref": "#/components/schemas/Widget"})}},
        widened_components,
    )
    assert obc.find_breaking_changes(base, head) == []


# --- response side -------------------------------------------------------------


def test_response_property_removed_is_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "string"}}}
    narrowed = {"type": "object", "properties": {"x": {"type": "string"}}}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=narrowed)}})
    findings = obc.find_breaking_changes(base, head)
    assert any("properties removed" in m for m in _messages(findings))


def test_response_property_added_is_not_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    widened = {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "string"}}}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=widened)}})
    assert obc.find_breaking_changes(base, head) == []


def test_response_required_demoted_to_optional_is_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    demoted = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=demoted)}})
    findings = obc.find_breaking_changes(base, head)
    assert any("demoted from required to optional" in m for m in _messages(findings))


def test_response_property_promoted_to_required_is_not_breaking() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    promoted = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=promoted)}})
    assert obc.find_breaking_changes(base, head) == []


def test_response_scalar_type_changed_is_breaking() -> None:
    base = _doc({"/a": {"get": _op(response_schema={"type": "string"})}})
    head = _doc({"/a": {"get": _op(response_schema={"type": "integer"})}})
    findings = obc.find_breaking_changes(base, head)
    assert any("type changed" in m for m in _messages(findings))


def test_response_enum_value_added_is_breaking() -> None:
    schema = {"type": "string", "enum": ["a"]}
    widened = {"type": "string", "enum": ["a", "b"]}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=widened)}})
    findings = obc.find_breaking_changes(base, head)
    assert any("enum values added" in m for m in _messages(findings))


def test_response_enum_value_removed_is_not_breaking() -> None:
    schema = {"type": "string", "enum": ["a", "b"]}
    narrowed = {"type": "string", "enum": ["a"]}
    base = _doc({"/a": {"get": _op(response_schema=schema)}})
    head = _doc({"/a": {"get": _op(response_schema=narrowed)}})
    assert obc.find_breaking_changes(base, head) == []


# --- $ref cycle guard, real-document self-diff, CLI error handling -----------


def test_ref_cycle_does_not_recurse_forever() -> None:
    components = {
        "Node": {
            "type": "object",
            "properties": {"child": {"$ref": "#/components/schemas/Node"}},
        }
    }
    base = _doc(
        {"/a": {"get": _op(response_schema={"$ref": "#/components/schemas/Node"})}}, components
    )
    head = _doc(
        {"/a": {"get": _op(response_schema={"$ref": "#/components/schemas/Node"})}}, components
    )
    assert obc.find_breaking_changes(base, head) == []


@pytest.mark.req("FR-20")
def test_real_committed_document_self_diff_is_clean() -> None:
    """The actual contract, compared to itself, must yield zero findings - a guard
    against a rule that fires on shapes present in the real document (e.g. the
    `after` cursor parameter's `anyOf: [string, null]` pattern)."""
    doc = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    assert obc.find_breaking_changes(doc, doc) == []


def test_cli_exits_2_on_missing_base_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    head_path = tmp_path / "head.json"
    head_path.write_text(json.dumps(_doc({})), encoding="utf-8")
    exit_code = obc.main(["--base", str(tmp_path / "missing.json"), "--head", str(head_path)])
    assert exit_code == 2
    assert "missing.json" in capsys.readouterr().err


def test_cli_exits_2_on_malformed_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base_path = tmp_path / "base.json"
    base_path.write_text("{not json", encoding="utf-8")
    head_path = tmp_path / "head.json"
    head_path.write_text(json.dumps(_doc({})), encoding="utf-8")
    exit_code = obc.main(["--base", str(base_path), "--head", str(head_path)])
    assert exit_code == 2
    assert "base.json" in capsys.readouterr().err


def test_cli_exits_0_when_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = _doc({"/a": {"get": _op()}})
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(doc), encoding="utf-8")
    head_path.write_text(json.dumps(doc), encoding="utf-8")
    assert obc.main(["--base", str(base_path), "--head", str(head_path)]) == 0
    assert "no breaking changes" in capsys.readouterr().out


def test_cli_exits_1_and_prints_github_annotation_when_breaking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(_doc({"/a": {"get": _op()}})), encoding="utf-8")
    head_path.write_text(json.dumps(_doc({})), encoding="utf-8")
    exit_code = obc.main(["--base", str(base_path), "--head", str(head_path), "--format", "github"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "/a" in out
