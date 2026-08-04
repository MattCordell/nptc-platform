"""Unit tests for scripts/backlog_sync.py (Foundation issue F-6).

Exercises the pure parsing/rendering/planning logic against synthetic fixtures. The
GhClient I/O layer that actually talks to `gh api` is deliberately not covered here -
see backlog_sync.py's own docstring on that class - it is exercised by really running
the script (docs.yml's --dry-run job).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

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
          labels: [phase/foundation, priority/must]
          requirements: [FR-74]
          docs: ["none: nothing to document"]
        """,
    )
    items, errors = bs.load_backlog_items()
    assert errors == []
    assert len(items) == 1
    assert items[0].id == "F-1"
    assert items[0].requirements == ("FR-74",)


def test_load_backlog_items_flattens_children_and_inherits_milestone_and_labels() -> None:
    _write(
        bs.BACKLOG_DIR,
        "p1.yaml",
        """\
        - id: P1-6
          title: "Property registry"
          milestone: "P1 — Core catalogue"
          labels: [area/registry, phase/p1]
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
    assert by_id["P1-6.1"].labels == ("area/registry", "phase/p1")


def test_load_backlog_items_flags_missing_docs_field() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "Example"
          milestone: "Foundation"
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
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("P9" in error for error in errors)


def test_load_backlog_items_flags_duplicate_id_across_files() -> None:
    _write(
        bs.BACKLOG_DIR,
        "foundation.yaml",
        """\
        - id: F-1
          title: "First"
          milestone: "Foundation"
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
          docs: ["none: n/a"]
        """,
    )
    _, errors = bs.load_backlog_items()
    assert any("duplicate" in error for error in errors)


# --- render_body ---------------------------------------------------------------------


def _item(**overrides: object) -> bs.BacklogItem:
    defaults: dict[str, object] = {
        "id": "F-1",
        "title": "Example",
        "milestone": "Foundation",
        "labels": ("phase/foundation",),
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


# --- plan_sync -------------------------------------------------------------------


def test_plan_sync_creates_an_issue_for_a_new_item() -> None:
    actions, errors = bs.plan_sync([_item()], {}, {})
    assert errors == []
    assert [a.kind for a in actions] == ["create"]
    assert actions[0].detail["title"] == "Example"


def test_plan_sync_adopts_by_explicit_github_issue_number() -> None:
    item = _item(github_issue=4)
    existing = bs.ExistingIssue(
        number=4,
        database_id=1001,
        body=bs.render_body(item),
        labels=frozenset({"phase/foundation"}),
        milestone="Foundation",
        state="OPEN",
    )
    actions, errors = bs.plan_sync([item], {4: existing}, {})
    assert errors == []
    assert actions == []  # already fully in sync


def test_plan_sync_errors_when_github_issue_does_not_exist() -> None:
    actions, errors = bs.plan_sync([_item(github_issue=999)], {}, {})
    assert any("999" in error for error in errors)
    assert actions == []


def test_plan_sync_matches_by_hidden_marker() -> None:
    item = _item()
    existing = bs.ExistingIssue(
        number=42,
        database_id=2002,
        body=bs.render_body(item),
        labels=frozenset({"phase/foundation"}),
        milestone="Foundation",
        state="OPEN",
    )
    actions, errors = bs.plan_sync([item], {42: existing}, {"F-1": 42})
    assert errors == []
    assert actions == []


def test_plan_sync_flags_only_missing_labels() -> None:
    item = _item(labels=("phase/foundation", "priority/must"))
    existing = bs.ExistingIssue(
        number=42,
        database_id=1,
        body=bs.render_body(item),
        labels=frozenset({"phase/foundation", "extra/one"}),
        milestone="Foundation",
        state="OPEN",
    )
    actions, errors = bs.plan_sync([item], {42: existing}, {"F-1": 42})
    assert errors == []
    add_actions = [a for a in actions if a.kind == "add_labels"]
    assert len(add_actions) == 1
    assert add_actions[0].detail["labels"] == ["priority/must"]


def test_plan_sync_flags_milestone_drift() -> None:
    item = _item(milestone="P0 — Seeding transform")
    existing = bs.ExistingIssue(
        number=42,
        database_id=1,
        body=bs.render_body(item),
        labels=frozenset(item.labels),
        milestone="Foundation",
        state="OPEN",
    )
    actions, errors = bs.plan_sync([item], {42: existing}, {"F-1": 42})
    assert errors == []
    set_milestone = [a for a in actions if a.kind == "set_milestone"]
    assert len(set_milestone) == 1
    assert set_milestone[0].detail["milestone"] == "P0 — Seeding transform"


def test_plan_sync_flags_stale_body() -> None:
    item = _item(summary="Updated summary.")
    stale_body = bs.render_body(_item(summary="Old summary."))
    existing = bs.ExistingIssue(
        number=42,
        database_id=1,
        body=stale_body,
        labels=frozenset(item.labels),
        milestone="Foundation",
        state="OPEN",
    )
    actions, errors = bs.plan_sync([item], {42: existing}, {"F-1": 42})
    assert errors == []
    assert any(a.kind == "update_body" for a in actions)


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
    parent_issue = bs.ExistingIssue(
        number=100,
        database_id=1000,
        body=bs.render_body(parent),
        labels=frozenset(parent.labels),
        milestone="Foundation",
        state="OPEN",
    )
    child_issue = bs.ExistingIssue(
        number=101,
        database_id=1001,
        body=bs.render_body(child),
        labels=frozenset(child.labels),
        milestone="Foundation",
        state="OPEN",
    )
    by_number = {100: parent_issue, 101: child_issue}
    by_marker = {"P1-6": 100, "P1-6.1": 101}
    linked_sub_issues = {100: {1001}}
    actions, errors = bs.plan_sync([parent, child], by_number, by_marker, linked_sub_issues)
    assert errors == []
    assert [a for a in actions if a.kind == "ensure_sub_issue"] == []


def test_plan_sync_still_flags_ensure_sub_issue_when_link_missing() -> None:
    parent = _item(id="P1-6")
    child = _item(id="P1-6.1", parent_id="P1-6")
    parent_issue = bs.ExistingIssue(
        number=100,
        database_id=1000,
        body=bs.render_body(parent),
        labels=frozenset(parent.labels),
        milestone="Foundation",
        state="OPEN",
    )
    child_issue = bs.ExistingIssue(
        number=101,
        database_id=1001,
        body=bs.render_body(child),
        labels=frozenset(child.labels),
        milestone="Foundation",
        state="OPEN",
    )
    by_number = {100: parent_issue, 101: child_issue}
    by_marker = {"P1-6": 100, "P1-6.1": 101}
    actions, errors = bs.plan_sync(
        [parent, child], by_number, by_marker, linked_sub_issues={100: set()}
    )
    assert errors == []
    assert len(actions) == 1
    assert actions[0].kind == "ensure_sub_issue"


# --- plan_labels / plan_milestones -------------------------------------------------


def test_plan_labels_returns_only_missing_names() -> None:
    existing = set(bs.LABEL_TAXONOMY) - {"phase/p0", "area/audit"}
    missing = bs.plan_labels(existing)
    assert set(missing) == {"phase/p0", "area/audit"}


def test_plan_milestones_returns_only_missing_titles() -> None:
    existing = {"Foundation", "P0 — Seeding transform"}
    missing = bs.plan_milestones(existing)
    assert missing == [t for t in bs.KNOWN_MILESTONES if t not in existing]
