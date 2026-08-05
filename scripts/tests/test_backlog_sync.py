"""Unit tests for scripts/backlog_sync.py (Foundation issue F-6).

Exercises the pure parsing/rendering/planning logic against synthetic fixtures, plus
GhClient's JSON-shaping logic (with the module-level `_gh_get`/`_gh_send`/`_gh_graphql`
transport monkeypatched) and print_plan/apply_plan/main (with a fake GhClient). Real
network behaviour against a live token is exercised only by really running the script
(docs.yml's --dry-run job).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backlog_sync as bs


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "BACKLOG_DIR", tmp_path)


def _write(path: Path, name: str, body: str) -> None:
    (path / name).write_text(textwrap.dedent(body), encoding="utf-8")


# --- load_backlog_items -------------------------------------------------------------


def test_load_backlog_items_parses_a_simple_item() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          labels: []
          requirements: [FR-74]
          docs: ["none: nothing to document"]
        """,
    )
    items, errors = bs.load_backlog_items()
    assert errors == []
    assert len(items) == 1
    assert items[0].id == "F-1"
    assert items[0].issue_type == "Task"
    assert items[0].priority == "MUST"
    assert items[0].requirements == ("FR-74",)


def test_load_backlog_items_flattens_children_and_inherits_milestone_type_and_priority() -> None:
    _write(
        bs.BACKLOG_DIR,
        "p1.yaml",
        """\
        - id: P1-6
          title: "Property registry"
          milestone: "P1 — Core catalogue"
          issue_type: Feature
          priority: MUST
          labels: [registry]
          docs: ["docs/adr/: registry design"]
          children:
            - id: P1-6.1
              title: "PropertyDefinition model"
              docs: ["none: covered by the parent"]
            - id: P1-6.2
              title: "JSON Schema validation"
              docs: ["none: covered by the parent"]
        """,
    )
    items, errors = bs.load_backlog_items()
    assert errors == []
    by_id = {item.id: item for item in items}
    assert set(by_id) == {"P1-6", "P1-6.1", "P1-6.2"}
    assert by_id["P1-6.1"].parent_id == "P1-6"
    assert by_id["P1-6.1"].milestone == "P1 — Core catalogue"
    assert by_id["P1-6.1"].issue_type == "Feature"
    assert by_id["P1-6.1"].priority == "MUST"
    assert by_id["P1-6.1"].labels == ("registry",)


def test_load_backlog_items_flags_missing_docs_field() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("docs:" in error and "F-1" in error for error in errors)


def test_load_backlog_items_flags_none_doc_mixed_with_real_entries() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          docs: ["none: nothing to document", "README.md: also this"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("mixes" in error for error in errors)


def test_load_backlog_items_flags_bad_requirement_id() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          requirements: ["FR-7A"]
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("FR-7A" in error for error in errors)


def test_load_backlog_items_flags_unknown_milestone() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "P9 — Does not exist"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("P9" in error for error in errors)


def test_load_backlog_items_flags_unknown_issue_type() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Epic
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("Epic" in error and "issue_type" in error for error in errors)


def test_load_backlog_items_flags_missing_issue_type() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("missing issue_type" in error for error in errors)


def test_load_backlog_items_flags_unknown_priority() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: URGENT
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("URGENT" in error and "priority" in error for error in errors)


def test_load_backlog_items_flags_missing_priority() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("missing priority" in error for error in errors)


def test_load_backlog_items_flags_duplicate_id_across_files() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "First"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    _write(
        bs.BACKLOG_DIR,
        "p0.yaml",
        """\
        - id: F-1
          title: "Duplicate"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("duplicate" in error for error in errors)


# --- render_body ---------------------------------------------------------------------


def _item(**overrides: Any) -> bs.BacklogItem:
    defaults: dict[str, Any] = {
        "id": "F-1",
        "title": "Example",
        "milestone": "Foundation",
        "issue_type": "Task",
        "priority": "MUST",
        "labels": (),
        "requirements": (),
        "tests": (),
        "summary": "",
        "checklist": (),
        "docs": ("none: n/a",),
        "acceptance": (),
        "github_issue": None,
        "parent_id": None,
        "source_file": "foundation.yaml",
    }
    defaults.update(overrides)
    return bs.BacklogItem(**defaults)


def _existing(**overrides: Any) -> bs.ExistingIssue:
    defaults: dict[str, Any] = {
        "number": 42,
        "database_id": 1,
        "node_id": "I_node42",
        "title": "Example",
        "body": "",
        "labels": frozenset(),
        "milestone": "Foundation",
        "issue_type": "Task",
        "state": "OPEN",
    }
    defaults.update(overrides)
    return bs.ExistingIssue(**defaults)


def test_render_body_includes_the_hidden_marker() -> None:
    body = bs.render_body(_item())
    assert "<!-- nptc-backlog-id: F-1 -->" in body


def test_render_body_renders_checklist_with_done_marker() -> None:
    body = bs.render_body(_item(checklist=("[x] Done step", "Not done step")))
    assert "- [x] Done step" in body
    assert "- [ ] Not done step" in body


def test_render_body_renders_none_docs_as_a_sentence_not_a_checkbox() -> None:
    body = bs.render_body(_item(docs=("none: nothing to document",)))
    assert "No documentation impact: nothing to document." in body
    assert "- [ ]" not in body.split("## Documentation")[1].split("---")[0]


def test_render_body_renders_real_docs_as_a_checklist() -> None:
    body = bs.render_body(_item(docs=("README.md: quickstart section",)))
    assert "- [ ] README.md: quickstart section" in body


# --- plan_sync: issue create/update/labels/milestone/type -------------------------


def test_plan_sync_creates_an_issue_and_sets_priority_for_a_new_item() -> None:
    actions, errors = bs.plan_sync([_item()], {}, {}, project_priorities={})
    assert errors == []
    assert [a.kind for a in actions] == ["create", "set_priority"]
    assert actions[0].detail["title"] == "Example"
    assert actions[0].detail["issue_type"] == "Task"
    assert actions[1].detail["priority"] == "MUST"


def test_plan_sync_skips_priority_entirely_when_project_priorities_is_none() -> None:
    """None (the default - distinct from {}) means Projects-v2 access wasn't
    available at all (e.g. CI's default GITHUB_TOKEN) - priority is left alone
    rather than treated as "everything needs adding"."""
    actions, errors = bs.plan_sync([_item()], {}, {})
    assert errors == []
    assert [a.kind for a in actions] == ["create"]


def test_plan_sync_adopts_by_explicit_github_issue_number() -> None:
    item = _item(github_issue=4)
    existing = _existing(
        number=4, database_id=1001, body=bs.render_body(item), labels=frozenset(item.labels)
    )
    actions, errors = bs.plan_sync([item], {4: existing}, {}, project_priorities={4: "MUST"})
    assert errors == []
    assert actions == []  # already fully in sync, including priority


def test_plan_sync_errors_when_github_issue_does_not_exist() -> None:
    actions, errors = bs.plan_sync([_item(github_issue=999)], {}, {})
    assert any("999" in error for error in errors)
    assert actions == []


def test_plan_sync_matches_by_hidden_marker() -> None:
    item = _item()
    existing = _existing(body=bs.render_body(item), labels=frozenset(item.labels))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    assert actions == []


def test_plan_sync_flags_only_missing_labels() -> None:
    item = _item(labels=("infra",))
    existing = _existing(body=bs.render_body(item), labels=frozenset())
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_labels = [a for a in actions if a.kind == "set_labels"]
    assert len(set_labels) == 1
    assert set(set_labels[0].detail["labels"]) == {"infra"}


def test_plan_sync_preserves_a_foreign_label_not_in_the_taxonomy() -> None:
    """A hand-added label outside LABEL_TAXONOMY (e.g. the default 'bug' label, or
    something a human added in the GitHub UI) must survive a set_labels reconcile."""
    item = _item(labels=("infra",))
    existing = _existing(body=bs.render_body(item), labels=frozenset({"infra", "bug"}))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    assert [a for a in actions if a.kind == "set_labels"] == []


def test_plan_sync_drops_a_stale_taxonomy_label_no_longer_desired() -> None:
    """A label that IS in our taxonomy but is no longer in the item's desired set
    gets dropped."""
    item = _item(labels=("infra",))
    existing = _existing(body=bs.render_body(item), labels=frozenset({"infra", "security"}))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_labels = [a for a in actions if a.kind == "set_labels"]
    assert len(set_labels) == 1
    assert set(set_labels[0].detail["labels"]) == {"infra"}


def test_plan_sync_drops_a_retired_phase_or_area_or_priority_label() -> None:
    """phase/*, area/*, type/* and priority/* labels predate the Issue Types /
    Projects-v2 migrations and are no longer in LABEL_TAXONOMY at all - they must
    still be recognised as ours to prune, not mistaken for a foreign label a human
    added."""
    item = _item(labels=("infra",))
    existing = _existing(
        body=bs.render_body(item),
        labels=frozenset(
            {"infra", "phase/foundation", "area/infra", "type/chore", "priority/must"}
        ),
    )
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_labels = [a for a in actions if a.kind == "set_labels"]
    assert len(set_labels) == 1
    assert set(set_labels[0].detail["labels"]) == {"infra"}


def test_plan_sync_flags_milestone_drift() -> None:
    item = _item(milestone="P0 — Seeding transform")
    existing = _existing(
        body=bs.render_body(item), labels=frozenset(item.labels), milestone="Foundation"
    )
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_milestone = [a for a in actions if a.kind == "set_milestone"]
    assert len(set_milestone) == 1
    assert set_milestone[0].detail["milestone"] == "P0 — Seeding transform"


def test_plan_sync_flags_issue_type_drift() -> None:
    item = _item(issue_type="Feature")
    existing = _existing(
        body=bs.render_body(item), labels=frozenset(item.labels), issue_type="Task"
    )
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_type = [a for a in actions if a.kind == "set_type"]
    assert len(set_type) == 1
    assert set_type[0].detail["issue_type"] == "Feature"


def test_plan_sync_flags_title_drift() -> None:
    item = _item(title="New title")
    existing = _existing(
        body=bs.render_body(item), labels=frozenset(item.labels), title="Old title"
    )
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_title = [a for a in actions if a.kind == "set_title"]
    assert len(set_title) == 1
    assert set_title[0].detail == {"number": 42, "title": "New title"}


def test_plan_sync_does_not_flag_title_when_unchanged() -> None:
    item = _item(title="Same title")
    existing = _existing(
        body=bs.render_body(item), labels=frozenset(item.labels), title="Same title"
    )
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    assert [a for a in actions if a.kind == "set_title"] == []


def test_plan_sync_flags_stale_body() -> None:
    item = _item(summary="Updated summary.")
    stale_body = bs.render_body(_item(summary="Old summary."))
    existing = _existing(body=stale_body, labels=frozenset(item.labels))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    assert any(a.kind == "update_body" for a in actions)


# --- plan_sync: priority (Projects v2) ---------------------------------------------


def test_plan_sync_flags_priority_when_issue_not_yet_on_the_board() -> None:
    item = _item()
    existing = _existing(body=bs.render_body(item), labels=frozenset(item.labels))
    actions, errors = bs.plan_sync([item], {42: existing}, {"F-1": 42}, project_priorities={})
    assert errors == []
    set_priority = [a for a in actions if a.kind == "set_priority"]
    assert len(set_priority) == 1
    assert set_priority[0].detail == {"number": 42, "priority": "MUST"}


def test_plan_sync_flags_priority_drift_when_already_on_the_board() -> None:
    item = _item(priority="SHOULD")
    existing = _existing(body=bs.render_body(item), labels=frozenset(item.labels))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    set_priority = [a for a in actions if a.kind == "set_priority"]
    assert len(set_priority) == 1
    assert set_priority[0].detail["priority"] == "SHOULD"


def test_plan_sync_does_not_flag_priority_when_already_correct_on_the_board() -> None:
    item = _item(priority="MUST")
    existing = _existing(body=bs.render_body(item), labels=frozenset(item.labels))
    actions, errors = bs.plan_sync(
        [item], {42: existing}, {"F-1": 42}, project_priorities={42: "MUST"}
    )
    assert errors == []
    assert [a for a in actions if a.kind == "set_priority"] == []


# --- plan_sync: sub-issues ----------------------------------------------------------


def test_plan_sync_emits_ensure_sub_issue_for_a_new_child() -> None:
    parent = _item(id="P1-6")
    child = _item(id="P1-6.1", parent_id="P1-6")
    actions, errors = bs.plan_sync([parent, child], {}, {})
    assert errors == []
    sub_issue_actions = [a for a in actions if a.kind == "ensure_sub_issue"]
    assert len(sub_issue_actions) == 1
    assert sub_issue_actions[0].item_id == "P1-6.1"
    assert sub_issue_actions[0].detail["parent_id"] == "P1-6"


def test_plan_sync_skips_ensure_sub_issue_when_already_linked() -> None:
    parent = _item(id="P1-6")
    child = _item(id="P1-6.1", parent_id="P1-6")
    parent_issue = _existing(
        number=100, database_id=1000, body=bs.render_body(parent), labels=frozenset(parent.labels)
    )
    child_issue = _existing(
        number=101, database_id=1001, body=bs.render_body(child), labels=frozenset(child.labels)
    )
    by_number = {100: parent_issue, 101: child_issue}
    by_marker = {"P1-6": 100, "P1-6.1": 101}
    linked_sub_issues = {100: {1001}}
    actions, errors = bs.plan_sync(
        [parent, child],
        by_number,
        by_marker,
        linked_sub_issues,
        project_priorities={100: "MUST", 101: "MUST"},
    )
    assert errors == []
    assert [a for a in actions if a.kind == "ensure_sub_issue"] == []


def test_plan_sync_still_flags_ensure_sub_issue_when_link_missing() -> None:
    parent = _item(id="P1-6")
    child = _item(id="P1-6.1", parent_id="P1-6")
    parent_issue = _existing(
        number=100, database_id=1000, body=bs.render_body(parent), labels=frozenset(parent.labels)
    )
    child_issue = _existing(
        number=101, database_id=1001, body=bs.render_body(child), labels=frozenset(child.labels)
    )
    by_number = {100: parent_issue, 101: child_issue}
    by_marker = {"P1-6": 100, "P1-6.1": 101}
    actions, errors = bs.plan_sync(
        [parent, child],
        by_number,
        by_marker,
        linked_sub_issues={100: set()},
        project_priorities={100: "MUST", 101: "MUST"},
    )
    assert errors == []
    assert len(actions) == 1
    assert actions[0].kind == "ensure_sub_issue"


# --- plan_labels / plan_milestones -------------------------------------------------


def test_plan_labels_returns_only_missing_names() -> None:
    existing = set(bs.LABEL_TAXONOMY) - {"security", "audit"}
    missing = bs.plan_labels(existing)
    assert set(missing) == {"security", "audit"}


def test_plan_milestones_returns_only_missing_titles() -> None:
    existing = {"Foundation", "P0 — Seeding transform"}
    missing = bs.plan_milestones(existing)
    assert missing == [t for t in bs.KNOWN_MILESTONES if t not in existing]


# --- load_backlog_items: remaining edge cases --------------------------------------


def test_load_backlog_items_flags_bad_item_id_and_skips_it() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: "not an id"
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )
    items, errors = bs.load_backlog_items()
    assert items == []
    assert any("does not match the expected id pattern" in error for error in errors)


def test_load_backlog_items_flags_non_list_yaml_top_level() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        id: F-1
        title: "Example"
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("expected a YAML list" in error for error in errors)


# --- render_body: remaining fields ---------------------------------------------------


def test_render_body_includes_requirement_ids() -> None:
    body = bs.render_body(_item(requirements=("FR-06", "NFR-08")))
    assert "## Requirement IDs" in body
    assert "FR-06, NFR-08" in body


def test_render_body_includes_mandated_tests() -> None:
    body = bs.render_body(_item(tests=("NFR-38.1",)))
    assert "## Mandated tests" in body
    assert "NFR-38.1" in body


def test_render_body_includes_acceptance_criteria() -> None:
    body = bs.render_body(_item(acceptance=("It works",)))
    assert "## Acceptance criteria" in body
    assert "- It works" in body


# --- _gh_get / _gh_send / _gh_graphql (transport helpers) --------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_gh_get_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        calls.append((cmd, kwargs))
        return _FakeCompletedProcess(stdout='{"a": 1}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = bs._gh_get("repos/:owner/:repo")
    assert result == {"a": 1}
    # encoding="utf-8" is required explicitly (Windows cp1252 defect, see the
    # inline comment on _gh_get) - assert it's actually passed, not assumed.
    assert calls[0][1]["encoding"] == "utf-8"


def test_gh_get_returns_none_for_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout="  "))
    assert bs._gh_get("repos/:owner/:repo") is None


def test_gh_get_raises_gherror_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1, stderr="boom")
    )
    with pytest.raises(bs.GhError, match="boom"):
        bs._gh_get("repos/:owner/:repo")


def test_gh_send_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout='{"ok": true}')
    )
    assert bs._gh_send("PATCH", "repos/:owner/:repo/issues/1", {"title": "x"}) == {"ok": True}


def test_gh_send_raises_gherror_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1, stderr="nope")
    )
    with pytest.raises(bs.GhError, match="nope"):
        bs._gh_send("PATCH", "repos/:owner/:repo/issues/1", {})


def test_gh_graphql_returns_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess(stdout='{"data": {"x": 1}}'),
    )
    assert bs._gh_graphql("query { x }") == {"x": 1}


def test_gh_graphql_marshals_variables_as_dash_f_key_equals_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        calls.append(cmd)
        return _FakeCompletedProcess(stdout='{"data": {}}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    bs._gh_graphql("query($projectId: ID!) { x }", projectId="proj1", itemId="item1")
    cmd = calls[0]
    assert cmd[:5] == ["gh", "api", "graphql", "-f", "query=query($projectId: ID!) { x }"]
    assert "-f" in cmd and "projectId=proj1" in cmd
    assert "itemId=item1" in cmd


def test_gh_graphql_raises_gherror_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1, stderr="down")
    )
    with pytest.raises(bs.GhError, match="down"):
        bs._gh_graphql("query { x }")


def test_gh_graphql_raises_gherror_on_graphql_errors_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess(stdout='{"data": null, "errors": ["bad field"]}'),
    )
    with pytest.raises(bs.GhError, match="bad field"):
        bs._gh_graphql("query { x }")


# --- GhClient: JSON-shaping methods (module-level _gh_* monkeypatched) -------------


def test_ghclient_fetch_issues_skips_prs_and_extracts_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = [
        {
            "number": 1,
            "id": 100,
            "node_id": "n1",
            "title": "PR",
            "body": "",
            "state": "open",
            "pull_request": {},
        },
        {
            "number": 2,
            "id": 200,
            "node_id": "n2",
            "title": "Has marker",
            "body": "<!-- nptc-backlog-id: F-1 -->",
            "labels": [{"name": "infra"}],
            "milestone": {"title": "Foundation"},
            "type": {"name": "Task"},
            "state": "open",
        },
        {
            "number": 3,
            "id": 300,
            "node_id": "n3",
            "title": "No marker",
            "body": "plain body",
            "labels": [],
            "milestone": None,
            "type": None,
            "state": "closed",
        },
    ]
    monkeypatch.setattr(bs, "_gh_get", lambda path: raw)
    client = bs.GhClient()
    by_number, by_marker = client.fetch_issues()
    assert set(by_number) == {2, 3}
    assert by_marker == {"F-1": 2}
    assert by_number[2].labels == frozenset({"infra"})
    assert by_number[2].milestone == "Foundation"
    assert by_number[2].issue_type == "Task"
    assert by_number[3].milestone is None
    assert by_number[3].issue_type is None


def test_ghclient_fetch_label_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_gh_get", lambda path: [{"name": "infra"}, {"name": "docs"}])
    assert bs.GhClient().fetch_label_names() == {"infra", "docs"}


def test_ghclient_fetch_milestones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_gh_get", lambda path: [{"title": "Foundation", "number": 1}])
    assert bs.GhClient().fetch_milestones() == {"Foundation": 1}


def test_ghclient_create_label_sends_taxonomy_description_and_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    def fake_send(method: str, path: str, payload: dict[str, Any]) -> None:
        sent["method"], sent["path"], sent["payload"] = method, path, payload

    monkeypatch.setattr(bs, "_gh_send", fake_send)
    bs.GhClient().create_label("security")
    description, color = bs.LABEL_TAXONOMY["security"]
    assert sent["payload"] == {"name": "security", "color": color, "description": description}


def test_ghclient_create_milestone_returns_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_gh_send", lambda method, path, payload: {"number": "7"})
    assert bs.GhClient().create_milestone("Foundation") == 7


def test_ghclient_create_issue_omits_milestone_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_send(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent["payload"] = payload
        return {"number": 1, "id": 10, "node_id": "n1", "body": "rendered body"}

    monkeypatch.setattr(bs, "_gh_send", fake_send)
    issue = bs.GhClient().create_issue("Title", "Body", ["infra"], None, "Task")
    assert "milestone" not in sent["payload"]
    assert issue.number == 1
    assert issue.labels == frozenset({"infra"})
    assert issue.issue_type == "Task"


def test_ghclient_create_issue_includes_milestone_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    def fake_send(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        sent["payload"] = payload
        return {"number": 1, "id": 10, "node_id": "n1", "body": ""}

    monkeypatch.setattr(bs, "_gh_send", fake_send)
    bs.GhClient().create_issue("Title", "Body", [], 5, "Task")
    assert sent["payload"]["milestone"] == 5


def test_ghclient_update_body_set_labels_set_milestone_set_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []
    monkeypatch.setattr(
        bs, "_gh_send", lambda method, path, payload: sent.append((method, path, payload))
    )
    client = bs.GhClient()
    client.update_body(1, "new body")
    client.set_labels(1, ["infra"])
    client.set_milestone(1, 5)
    client.set_issue_type(1, "Feature")
    assert sent[0] == ("PATCH", "repos/:owner/:repo/issues/1", {"body": "new body"})
    assert sent[1] == ("PUT", "repos/:owner/:repo/issues/1/labels", {"labels": ["infra"]})
    assert sent[2] == ("PATCH", "repos/:owner/:repo/issues/1", {"milestone": 5})
    assert sent[3] == ("PATCH", "repos/:owner/:repo/issues/1", {"type": "Feature"})


def test_ghclient_fetch_sub_issue_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_gh_get", lambda path: [{"id": 1}, {"id": 2}])
    assert bs.GhClient().fetch_sub_issue_ids(100) == {1, 2}


def test_ghclient_add_sub_issue_sends_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setattr(
        bs,
        "_gh_send",
        lambda method, path, payload: sent.update(method=method, path=path, payload=payload),
    )
    bs.GhClient().add_sub_issue(100, 2001)
    assert sent["path"] == "repos/:owner/:repo/issues/100/sub_issues"
    assert sent["payload"] == {"sub_issue_id": 2001}


def test_ghclient_repo_owner_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_gh_get", lambda path: {"owner": {"login": "MattCordell"}})
    assert bs.GhClient().repo_owner_login() == "MattCordell"


def test_ghclient_ensure_project_returns_existing_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        return {
            "organization": {
                "id": "org1",
                "projectsV2": {"nodes": [{"id": "proj1", "title": bs.PROJECT_TITLE}]},
            }
        }

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    assert bs.GhClient().ensure_project("myorg") == "proj1"


def test_ghclient_ensure_project_creates_and_links_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        calls.append((query, variables))
        if "createProjectV2" in query:
            return {"createProjectV2": {"projectV2": {"id": "new-proj"}}}
        if "linkProjectV2ToRepository" in query:
            return {"repository": {"id": "repo1"}}
        return {"organization": {"id": "org1", "projectsV2": {"nodes": []}}}

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    monkeypatch.setattr(bs, "_gh_get", lambda path: {"node_id": "repo-node"})
    project_id = bs.GhClient().ensure_project("myorg")
    assert project_id == "new-proj"
    assert any("linkProjectV2ToRepository" in q for q, _ in calls)


def test_ghclient_ensure_priority_field_returns_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        return {
            "node": {
                "fields": {
                    "nodes": [
                        {
                            "name": bs.PRIORITY_FIELD_NAME,
                            "id": "field1",
                            "options": [{"id": "opt-must", "name": "MUST"}],
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    field_id, options = bs.GhClient().ensure_priority_field("proj1")
    assert field_id == "field1"
    assert options == {"MUST": "opt-must"}


def test_ghclient_ensure_priority_field_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        if "createProjectV2Field" in query:
            return {
                "createProjectV2Field": {
                    "projectV2Field": {
                        "id": "new-field",
                        "options": [
                            {"id": "o1", "name": "MUST"},
                            {"id": "o2", "name": "SHOULD"},
                            {"id": "o3", "name": "MAY"},
                        ],
                    }
                }
            }
        return {"node": {"fields": {"nodes": []}}}

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    field_id, options = bs.GhClient().ensure_priority_field("proj1")
    assert field_id == "new-field"
    assert options == {"MUST": "o1", "SHOULD": "o2", "MAY": "o3"}


def test_ghclient_fetch_project_priorities_shapes_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        return {
            "node": {
                "items": {
                    "nodes": [
                        {
                            "id": "item1",
                            "content": {"number": 42},
                            "fieldValueByName": {"name": "MUST"},
                        },
                        {"id": "item2", "content": {"number": 43}, "fieldValueByName": None},
                        {"id": "item3", "content": None, "fieldValueByName": None},
                    ]
                }
            }
        }

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    result = bs.GhClient().fetch_project_priorities("proj1")
    assert result == {
        42: {"item_id": "item1", "priority": "MUST"},
        43: {"item_id": "item2", "priority": None},
    }


def test_ghclient_add_project_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bs, "_gh_graphql", lambda q, **v: {"addProjectV2ItemById": {"item": {"id": "item1"}}}
    )
    assert bs.GhClient().add_project_item("proj1", "issue-node") == "item1"


def test_ghclient_set_priority_field_passes_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_graphql(query: str, **variables: str) -> dict[str, Any]:
        seen.update(variables)
        return {}

    monkeypatch.setattr(bs, "_gh_graphql", fake_graphql)
    bs.GhClient().set_priority_field("proj1", "item1", "field1", "opt1")
    assert seen == {
        "projectId": "proj1",
        "itemId": "item1",
        "fieldId": "field1",
        "optionId": "opt1",
    }


# --- print_plan ----------------------------------------------------------------------


def test_print_plan_reports_no_changes(capsys: pytest.CaptureFixture[str]) -> None:
    bs.print_plan([], [], [])
    assert "no changes" in capsys.readouterr().out


def test_print_plan_prints_one_line_per_action_kind(capsys: pytest.CaptureFixture[str]) -> None:
    actions = [
        bs.Action("create", "F-1", {"title": "New"}),
        bs.Action("update_body", "F-2", {"number": 1}),
        bs.Action("set_labels", "F-3", {"number": 2, "labels": ["infra"]}),
        bs.Action("set_milestone", "F-4", {"number": 3, "milestone": "Foundation"}),
        bs.Action("set_type", "F-5", {"number": 4, "issue_type": "Task"}),
        bs.Action("set_priority", "F-6", {"priority": "MUST"}),
        bs.Action("ensure_sub_issue", "F-7.1", {"parent_id": "F-7"}),
    ]
    bs.print_plan(["security"], ["Foundation"], actions)
    out = capsys.readouterr().out
    assert "create label security" in out
    assert "create milestone 'Foundation'" in out
    assert "create issue for F-1" in out
    assert "update body of #1 (F-2)" in out
    assert "set labels of #2 (F-3)" in out
    assert "set milestone of #3 (F-4)" in out
    assert "set issue type of #4 (F-5)" in out
    assert "set priority of F-6" in out
    assert "F-7.1 is linked as a sub-issue of F-7" in out


# --- apply_plan ------------------------------------------------------------------


class _FakeGhClient(bs.GhClient):
    """Records every call apply_plan makes, in call order, without touching gh.

    Subclasses the real GhClient (rather than duck-typing independently) purely so
    it satisfies apply_plan's `client: GhClient` type hint under mypy --strict; none
    of the base class's own methods are invoked."""

    def __init__(self, sub_issue_ids: dict[int, set[int]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._next_issue_number = 1000
        # parent issue number -> the child database_ids GitHub already has linked -
        # lets a test pre-seed the "already linked" case without a real API call.
        self._sub_issue_ids = sub_issue_ids or {}

    def create_label(self, name: str) -> None:
        self.calls.append(("create_label", (name,)))

    def create_milestone(self, title: str) -> int:
        self.calls.append(("create_milestone", (title,)))
        return 99

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        milestone_number: int | None,
        issue_type: str,
    ) -> bs.ExistingIssue:
        self.calls.append(("create_issue", (title, body, labels, milestone_number, issue_type)))
        self._next_issue_number += 1
        return bs.ExistingIssue(
            number=self._next_issue_number,
            database_id=self._next_issue_number * 10,
            node_id=f"node-{self._next_issue_number}",
            body=body,
            labels=frozenset(labels),
            milestone=None,
            issue_type=issue_type,
            state="OPEN",
        )

    def update_body(self, number: int, body: str) -> None:
        self.calls.append(("update_body", (number, body)))

    def set_labels(self, number: int, labels: list[str]) -> None:
        self.calls.append(("set_labels", (number, labels)))

    def set_milestone(self, number: int, milestone_number: int) -> None:
        self.calls.append(("set_milestone", (number, milestone_number)))

    def set_issue_type(self, number: int, issue_type: str) -> None:
        self.calls.append(("set_issue_type", (number, issue_type)))

    def fetch_sub_issue_ids(self, parent_number: int) -> set[int]:
        self.calls.append(("fetch_sub_issue_ids", (parent_number,)))
        return self._sub_issue_ids.get(parent_number, set())

    def add_sub_issue(self, parent_number: int, child_database_id: int) -> None:
        self.calls.append(("add_sub_issue", (parent_number, child_database_id)))

    def add_project_item(self, project_id: str, issue_node_id: str) -> str:
        self.calls.append(("add_project_item", (project_id, issue_node_id)))
        return "new-item-id"

    def set_priority_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self.calls.append(("set_priority_field", (project_id, item_id, field_id, option_id)))


def test_apply_plan_creates_issue_then_sets_priority_for_it() -> None:
    item = _item()
    client = _FakeGhClient()
    actions = [
        bs.Action(
            "create",
            item.id,
            {
                "title": item.title,
                "body": "body",
                "labels": [],
                "milestone": "Foundation",
                "issue_type": "Task",
            },
        ),
        bs.Action("set_priority", item.id, {"priority": "MUST"}),
    ]
    bs.apply_plan(
        client,
        [item],
        {},
        {},
        [],
        [],
        {"Foundation": 1},
        actions,
        "proj1",
        "field1",
        {"MUST": "opt-must"},
        {},
    )
    kinds = [kind for kind, _ in client.calls]
    assert kinds == ["create_issue", "add_project_item", "set_priority_field"]
    # the new issue's node_id must be what add_project_item was called with
    _, add_args = client.calls[1]
    assert add_args[1] == "node-1001"


def test_apply_plan_set_priority_reuses_existing_project_item() -> None:
    item = _item(github_issue=42)
    existing = _existing(number=42, database_id=420, node_id="n42")
    client = _FakeGhClient()
    actions = [bs.Action("set_priority", item.id, {"number": 42, "priority": "SHOULD"})]
    bs.apply_plan(
        client,
        [item],
        {42: existing},
        {},
        [],
        [],
        {},
        actions,
        "proj1",
        "field1",
        {"SHOULD": "opt-should"},
        {42: {"item_id": "existing-item"}},
    )
    kinds = [kind for kind, _ in client.calls]
    assert kinds == ["set_priority_field"]  # add_project_item skipped - already on the board
    _, args = client.calls[0]
    assert args == ("proj1", "existing-item", "field1", "opt-should")


def test_apply_plan_update_body_labels_milestone_type() -> None:
    item = _item(github_issue=42)
    existing = _existing(number=42, database_id=420, node_id="n42")
    client = _FakeGhClient()
    actions = [
        bs.Action("update_body", item.id, {"number": 42, "body": "new body"}),
        bs.Action("set_labels", item.id, {"number": 42, "labels": ["infra"]}),
        bs.Action("set_milestone", item.id, {"number": 42, "milestone": "Foundation"}),
        bs.Action("set_type", item.id, {"number": 42, "issue_type": "Feature"}),
    ]
    bs.apply_plan(
        client,
        [item],
        {42: existing},
        {},
        [],
        [],
        {"Foundation": 7},
        actions,
        None,
        None,
        {},
        {},
    )
    assert ("update_body", (42, "new body")) in client.calls
    assert ("set_labels", (42, ["infra"])) in client.calls
    assert ("set_milestone", (42, 7)) in client.calls
    assert ("set_issue_type", (42, "Feature")) in client.calls


def test_apply_plan_ensure_sub_issue_links_once_and_caches_lookup() -> None:
    parent = _item(id="P1-6", github_issue=100)
    child_a = _item(id="P1-6.1", parent_id="P1-6", github_issue=101)
    child_b = _item(id="P1-6.2", parent_id="P1-6", github_issue=102)
    by_number = {
        100: _existing(number=100, database_id=1000, node_id="n100"),
        101: _existing(number=101, database_id=1001, node_id="n101"),
        102: _existing(number=102, database_id=1002, node_id="n102"),
    }
    client = _FakeGhClient()
    actions = [
        bs.Action("ensure_sub_issue", "P1-6.1", {"parent_id": "P1-6"}),
        bs.Action("ensure_sub_issue", "P1-6.2", {"parent_id": "P1-6"}),
    ]
    bs.apply_plan(
        client,
        [parent, child_a, child_b],
        by_number,
        {},
        [],
        [],
        {},
        actions,
        None,
        None,
        {},
        {},
    )
    fetch_calls = [c for c in client.calls if c[0] == "fetch_sub_issue_ids"]
    add_calls = [c for c in client.calls if c[0] == "add_sub_issue"]
    # fetched once for the parent, reused (cached) for the second child
    assert len(fetch_calls) == 1
    assert len(add_calls) == 2


def test_apply_plan_ensure_sub_issue_skips_a_child_already_linked() -> None:
    """The guard apply_plan exists for in the first place: don't re-POST a link
    GitHub already has. Pre-seeds the fake's fetch_sub_issue_ids with the child's
    database_id, so add_sub_issue must not be called for it."""
    parent = _item(id="P1-6", github_issue=100)
    child = _item(id="P1-6.1", parent_id="P1-6", github_issue=101)
    by_number = {
        100: _existing(number=100, database_id=1000, node_id="n100"),
        101: _existing(number=101, database_id=1001, node_id="n101"),
    }
    client = _FakeGhClient(sub_issue_ids={100: {1001}})
    actions = [bs.Action("ensure_sub_issue", "P1-6.1", {"parent_id": "P1-6"})]
    bs.apply_plan(client, [parent, child], by_number, {}, [], [], {}, actions, None, None, {}, {})
    assert ("fetch_sub_issue_ids", (100,)) in client.calls
    assert not any(c[0] == "add_sub_issue" for c in client.calls)


def test_apply_plan_creates_labels_and_milestones_upfront() -> None:
    client = _FakeGhClient()
    bs.apply_plan(
        client,
        [],
        {},
        {},
        ["security"],
        ["Foundation"],
        {},
        [],
        None,
        None,
        {},
        {},
    )
    assert ("create_label", ("security",)) in client.calls
    assert ("create_milestone", ("Foundation",)) in client.calls


# --- main ------------------------------------------------------------------------


class _MainFakeGhClient:
    """Configurable fake for main()'s three call sites: fetch_issues/labels/
    milestones/sub-issues, the Projects-v2 bootstrap, and (on --apply) apply_plan's
    client. `project_error`, if set, is raised by repo_owner_login."""

    def __init__(
        self,
        issues: dict[int, bs.ExistingIssue] | None = None,
        markers: dict[str, int] | None = None,
        project_error: Exception | None = None,
        apply_error: Exception | None = None,
    ) -> None:
        self._issues = issues or {}
        self._markers = markers or {}
        self._project_error = project_error
        self._apply_error = apply_error
        self.apply_calls: list[str] = []

    def fetch_issues(self) -> tuple[dict[int, bs.ExistingIssue], dict[str, int]]:
        return self._issues, self._markers

    def fetch_label_names(self) -> set[str]:
        return set(bs.LABEL_TAXONOMY)

    def fetch_milestones(self) -> dict[str, int]:
        return {title: i for i, title in enumerate(bs.KNOWN_MILESTONES)}

    def fetch_sub_issue_ids(self, parent_number: int) -> set[int]:
        return set()

    def repo_owner_login(self) -> str:
        if self._project_error is not None:
            raise self._project_error
        return "MattCordell"

    def ensure_project(self, owner_login: str) -> str:
        return "proj1"

    def ensure_priority_field(self, project_id: str) -> tuple[str, dict[str, str]]:
        return "field1", {"MUST": "o1", "SHOULD": "o2", "MAY": "o3"}

    def fetch_project_priorities(self, project_id: str) -> dict[int, dict[str, Any]]:
        return {}

    def create_issue(self, *a: Any, **k: Any) -> bs.ExistingIssue:
        self.apply_calls.append("create_issue")
        if self._apply_error is not None:
            raise self._apply_error
        return bs.ExistingIssue(
            number=1,
            database_id=1,
            node_id="n1",
            body="x",
            labels=frozenset(),
            milestone=None,
            issue_type="Task",
            state="OPEN",
        )

    def add_project_item(self, project_id: str, issue_node_id: str) -> str:
        self.apply_calls.append("add_project_item")
        return "item1"

    def set_priority_field(self, *a: Any) -> None:
        self.apply_calls.append("set_priority_field")

    def create_label(self, name: str) -> None:
        self.apply_calls.append("create_label")

    def create_milestone(self, title: str) -> int:
        self.apply_calls.append("create_milestone")
        return 1


def _write_valid_backlog(path: Path) -> None:
    _write(
        path,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )


def test_main_returns_1_on_backlog_errors_before_any_network_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "P9 — Does not exist"
          issue_type: Task
          priority: MUST
          docs: ["none: n/a"]
        """,
    )

    class _ExplodingClient:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"main() must not reach GhClient.{name} on a backlog error")

    monkeypatch.setattr(bs, "GhClient", _ExplodingClient)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py"])
    assert bs.main() == 1
    assert "problem(s) in docs/backlog" in capsys.readouterr().err


def test_main_returns_1_when_fetch_issues_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_valid_backlog(bs.BACKLOG_DIR)

    class _RaisingClient:
        def fetch_issues(self) -> Any:
            raise bs.GhError("network down")

    monkeypatch.setattr(bs, "GhClient", _RaisingClient)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py"])
    assert bs.main() == 1
    assert "network down" in capsys.readouterr().err


def test_main_degrades_gracefully_when_projects_v2_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The principal failure mode this degrade-path exists for: CI's default
    GITHUB_TOKEN cannot reach Projects-v2 at all. main() must still complete the
    rest of the sync and print a warning, not crash or skip everything."""
    _write_valid_backlog(bs.BACKLOG_DIR)
    client = _MainFakeGhClient(project_error=bs.GhError("no project scope"))
    monkeypatch.setattr(bs, "GhClient", lambda: client)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py"])
    result = bs.main()
    err = capsys.readouterr().err
    assert result == 0
    assert "Projects-v2 (Priority) sync unavailable" in err
    assert "no project scope" in err


def test_main_dry_run_prints_plan_and_does_not_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_valid_backlog(bs.BACKLOG_DIR)
    client = _MainFakeGhClient()
    monkeypatch.setattr(bs, "GhClient", lambda: client)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py"])
    result = bs.main()
    out = capsys.readouterr().out
    assert result == 0
    assert "dry-run" in out
    assert client.apply_calls == []


def test_main_apply_runs_apply_plan_and_reports_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_valid_backlog(bs.BACKLOG_DIR)
    client = _MainFakeGhClient()
    monkeypatch.setattr(bs, "GhClient", lambda: client)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py", "--apply"])
    result = bs.main()
    out = capsys.readouterr().out
    assert result == 0
    assert "applied" in out
    assert "create_issue" in client.apply_calls


def test_main_returns_1_when_apply_plan_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A GhError partway through --apply (e.g. the token losing write access
    mid-run) must be reported and exit 1, not propagate as a traceback."""
    _write_valid_backlog(bs.BACKLOG_DIR)
    client = _MainFakeGhClient(apply_error=bs.GhError("write access revoked"))
    monkeypatch.setattr(bs, "GhClient", lambda: client)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py", "--apply"])
    result = bs.main()
    assert result == 1
    assert "write access revoked" in capsys.readouterr().err


def test_main_returns_1_on_plan_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
          issue_type: Task
          priority: MUST
          github_issue: 999
          docs: ["none: n/a"]
        """,
    )
    client = _MainFakeGhClient()  # by_number has no #999 -> plan_sync errors
    monkeypatch.setattr(bs, "GhClient", lambda: client)
    monkeypatch.setattr(sys, "argv", ["backlog_sync.py"])
    result = bs.main()
    assert result == 1
    assert "999" in capsys.readouterr().err
