#!/usr/bin/env python3
"""Backlog -> GitHub issue sync (Foundation issue F-6).

Reads docs/backlog/*.yaml (schema documented in docs/backlog/foundation.yaml) and
reconciles it against GitHub: labels and milestones are created if missing, and each
backlog item becomes a GitHub issue, matched either by an explicit `github_issue:`
number (for issues that already exist outside this process - see the Foundation
items) or by a hidden `<!-- nptc-backlog-id: ID -->` marker this script writes into
the body of every issue it creates. Re-running produces no further changes and no
duplicates.

Genuinely multi-week items (`children:` in the YAML) are linked as GitHub native
sub-issues of their parent via the sub-issues API.

Dry-run by default: prints the plan and makes no changes. Pass --apply to execute it.
CI (docs.yml) runs the dry-run on every PR touching docs/backlog/**, so a malformed
backlog or an unreachable github_issue: fails review rather than a later import.

Usage:
  uv run python scripts/backlog_sync.py            # dry-run, prints the plan
  uv run python scripts/backlog_sync.py --apply     # execute it
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
BACKLOG_DIR = ROOT / "docs" / "backlog"

MARKER_TEMPLATE = "<!-- nptc-backlog-id: {id} -->"
MARKER_PATTERN = re.compile(r"<!--\s*nptc-backlog-id:\s*(?P<id>[\w.\-]+)\s*-->")

ITEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[\w.]+$")  # F-1, P0-5, P1-6.1, OI-6, ...
REQUIREMENT_ID_PATTERN = re.compile(r"^(FR|NFR)-\d+$")

# Milestone titles items are validated against - the plan's fixed set (docs/backlog
# only has Foundation/P0/P1 populated so far; the rest are pre-created so a later
# backlog PR never has to remember to create its own milestone).
KNOWN_MILESTONES = [
    "Foundation",
    "P0 — Seeding transform",
    "P1 — Core catalogue",
    "P2 — Contribution",
    "P3 — Validation",
    "P4 — Release and export",
    "P5 — Hardening",
    "Governance",
]

# The label taxonomy, bootstrapped regardless of which labels today's backlog items
# actually use, so a future phase's backlog PR never has to remember to create its
# own labels. name -> (description, colour).
#
# What/type and phase are deliberately NOT labels: GitHub's native Issue Types
# (see ISSUE_TYPES below) cover "what kind of work is this", and the issue's
# milestone already carries the phase - a phase/* label would just duplicate it.
# Priority is likewise not a label: it lives in the "Priority" custom field on
# the NPTC Catalogue Platform Projects-v2 board (see PROJECT_TITLE below), which
# is what a native GitHub priority looks like - Issues has no priority field of
# its own.
LABEL_TAXONOMY: dict[str, tuple[str, str]] = {
    "api": ("Backend HTTP API", "5319e7"),
    "db": ("Database schema and migrations", "5319e7"),
    "frontend": ("React/TypeScript client", "5319e7"),
    "transform": ("The P0 seeding transform CLI", "5319e7"),
    "terminology": ("SNOMED CT / Ontoserver integration", "5319e7"),
    "auth": ("Authentication and identity", "5319e7"),
    "audit": ("Audit logging", "5319e7"),
    "registry": ("Property registry", "5319e7"),
    "export": ("Release exports (CSV, FHIR, SPIA spreadsheet)", "5319e7"),
    "infra": ("CI, deployment, containers, tooling", "5319e7"),
    "ci": ("GitHub Actions workflows", "5319e7"),
    "docs": ("Documentation and its tooling", "5319e7"),
    "a11y": ("Accessibility", "5319e7"),
    "security": ("Security-relevant, cross-cutting across Bug/Task/Feature", "d73a4a"),
    "spike": ("Time-boxed investigation, not a committed implementation", "d4c5f9"),
    # PR-only: pr-hygiene.yml checks for this on the *pull request* itself (not an
    # issue) to enforce the backlog checklist being updated in the same PR. PRs
    # have no native Issue Type, so this stays a plain label rather than moving to
    # ISSUE_TYPES; backlog_sync.py never applies it to an issue.
    "feature": ("This PR implements backlog work - see pr-hygiene.yml", "0e8a16"),
    "status/blocked": ("Blocked on something outside this issue", "e4e669"),
    "status/needs-decision": ("Needs a decision before work can proceed", "e4e669"),
    "status/needs-rcpa-input": ("Needs RCPA-QAP editorial input", "e4e669"),
    "governance/open-issue": ("One of the PRD's numbered open issues (OI-n)", "5319e7"),
}

# GitHub's native Issue Types for the aehrc org (org settings, not repo-managed - this
# script can set an issue's type but not create a new one). "Epic" also exists there
# but predates this project and is deliberately unused (plan SS3.1: flat issues, no
# epic vocabulary).
ISSUE_TYPES = {"Task", "Bug", "Feature"}

# The PRD's own RFC-2119 scheduling priority (requirements.yaml and every backlog
# item already carry this vocabulary). Represented as a single-select "Priority"
# field on a Projects-v2 board, created/adopted under whichever account owns this
# repo - not a label, since GitHub Issues has no native priority field.
PRIORITIES = ("MUST", "SHOULD", "MAY")
PRIORITY_OPTION_COLORS = {"MUST": "RED", "SHOULD": "ORANGE", "MAY": "YELLOW"}
PROJECT_TITLE = "NPTC Catalogue Platform"
PRIORITY_FIELD_NAME = "Priority"


# --- Schema, parsing, validation --------------------------------------------------


@dataclass(frozen=True)
class BacklogItem:
    id: str
    title: str
    milestone: str
    issue_type: str
    priority: str
    labels: tuple[str, ...]
    requirements: tuple[str, ...]
    tests: tuple[str, ...]
    summary: str
    checklist: tuple[str, ...]
    docs: tuple[str, ...]
    acceptance: tuple[str, ...]
    github_issue: int | None
    parent_id: str | None
    source_file: str


def _as_tuple(value: Any) -> tuple[str, ...]:
    return tuple(value) if value else ()


def _strip_done(text: str) -> tuple[str, bool]:
    """Strip a "[x] " done-marker prefix, if present. Returns (text, was_done)."""
    if text.startswith("[x] "):
        return text[4:], True
    return text, False


def _is_none_doc(entry: str) -> bool:
    text, _ = _strip_done(entry)
    return text.strip().lower().startswith("none:")


def _flatten(
    raw_items: list[dict[str, Any]],
    source_file: str,
    parent: BacklogItem | None,
    errors: list[str],
) -> list[BacklogItem]:
    flat: list[BacklogItem] = []
    for raw in raw_items:
        item_id = raw.get("id", "<missing id>")
        if not ITEM_ID_PATTERN.match(str(item_id)):
            errors.append(
                f"{source_file}: item id {item_id!r} does not match the expected id pattern"
            )
            continue

        milestone = raw.get("milestone") or (parent.milestone if parent else None)
        if not milestone:
            errors.append(f"{item_id}: missing milestone")
            milestone = ""
        elif milestone not in KNOWN_MILESTONES:
            errors.append(f"{item_id}: milestone {milestone!r} is not one of {KNOWN_MILESTONES}")

        issue_type = raw.get("issue_type") or (parent.issue_type if parent else None)
        if not issue_type:
            errors.append(f"{item_id}: missing issue_type")
            issue_type = ""
        elif issue_type not in ISSUE_TYPES:
            errors.append(
                f"{item_id}: issue_type {issue_type!r} is not one of {sorted(ISSUE_TYPES)}"
            )

        priority = raw.get("priority") or (parent.priority if parent else None)
        if not priority:
            errors.append(f"{item_id}: missing priority")
            priority = ""
        elif priority not in PRIORITIES:
            errors.append(f"{item_id}: priority {priority!r} is not one of {PRIORITIES}")

        labels = _as_tuple(raw.get("labels")) or (parent.labels if parent else ())

        for rid in raw.get("requirements") or []:
            if not REQUIREMENT_ID_PATTERN.match(str(rid)):
                errors.append(f"{item_id}: requirement id {rid!r} is not FR-nn/NFR-nn")

        docs = _as_tuple(raw.get("docs"))
        if not docs:
            errors.append(
                f"{item_id}: docs: is required - use ['none: <reason>'] if there is genuinely "
                "no documentation impact"
            )
        else:
            none_entries = [d for d in docs if _is_none_doc(d)]
            if none_entries and len(docs) > 1:
                errors.append(
                    f"{item_id}: docs: mixes a 'none: <reason>' entry with real doc entries"
                )

        item = BacklogItem(
            id=str(item_id),
            title=str(raw.get("title", "")),
            milestone=milestone,
            issue_type=issue_type,
            priority=priority,
            labels=labels,
            requirements=_as_tuple(raw.get("requirements")),
            tests=_as_tuple(raw.get("tests")),
            summary=str(raw.get("summary", "")).strip(),
            checklist=_as_tuple(raw.get("checklist")),
            docs=docs,
            acceptance=_as_tuple(raw.get("acceptance")),
            github_issue=raw.get("github_issue"),
            parent_id=parent.id if parent else None,
            source_file=source_file,
        )
        flat.append(item)
        flat.extend(_flatten(raw.get("children") or [], source_file, item, errors))
    return flat


def load_backlog_items() -> tuple[list[BacklogItem], list[str]]:
    errors: list[str] = []
    items: list[BacklogItem] = []
    seen: dict[str, str] = {}
    for path in sorted(BACKLOG_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            errors.append(f"{path.name}: expected a YAML list at the top level")
            continue
        for item in _flatten(raw, path.name, None, errors):
            if item.id in seen:
                errors.append(f"{item.id}: duplicate id (also defined in {seen[item.id]})")
                continue
            seen[item.id] = item.source_file
            items.append(item)
    return items, errors


# --- Rendering ---------------------------------------------------------------------


def _render_checklist(entries: tuple[str, ...]) -> list[str]:
    lines = []
    for entry in entries:
        text, done = _strip_done(entry)
        lines.append(f"- [{'x' if done else ' '}] {text}")
    return lines


def render_body(item: BacklogItem) -> str:
    lines = [MARKER_TEMPLATE.format(id=item.id), ""]
    if item.summary:
        lines += ["## Summary", "", item.summary, ""]
    if item.requirements:
        lines += ["## Requirement IDs", "", ", ".join(item.requirements), ""]
    if item.tests:
        lines += ["## Mandated tests", "", ", ".join(item.tests), ""]
    if item.checklist:
        lines += ["## Steps", "", *_render_checklist(item.checklist), ""]
    lines += ["## Documentation", ""]
    if len(item.docs) == 1 and _is_none_doc(item.docs[0]):
        text, _ = _strip_done(item.docs[0])
        reason = text.split(":", 1)[1].strip() if ":" in text else text
        lines += [f"No documentation impact: {reason}.", ""]
    else:
        lines += [*_render_checklist(item.docs), ""]
    if item.acceptance:
        lines += ["## Acceptance criteria", "", *[f"- {a}" for a in item.acceptance], ""]
    lines += [
        "---",
        f"Managed by `scripts/backlog_sync.py` from `docs/backlog/{item.source_file}` "
        f"(item `{item.id}`). Edit the YAML, not this issue body.",
    ]
    return "\n".join(lines) + "\n"


# --- Planning (pure - diffed against a caller-supplied snapshot of GitHub state) ---


@dataclass(frozen=True)
class ExistingIssue:
    number: int
    database_id: int
    node_id: str
    body: str
    labels: frozenset[str]
    milestone: str | None
    issue_type: str | None
    state: str


# Prefixes backlog_sync.py used to manage before the Issue Types / area-prefix /
# Projects-v2-priority migrations. A label matching one of these is ours to prune
# even though it is no longer in LABEL_TAXONOMY at all (the taxonomy only lists
# what we manage *now*).
RETIRED_LABEL_PREFIXES = ("phase/", "area/", "type/", "priority/")


def _is_managed_label(name: str) -> bool:
    return name in LABEL_TAXONOMY or name.startswith(RETIRED_LABEL_PREFIXES)


def _reconciled_labels(existing_labels: frozenset[str], desired_labels: set[str]) -> set[str]:
    """The full label set an issue should end up with: our own desired set, plus
    whatever labels it already carries that we don't manage at all - past or
    present (a default GitHub label like "bug", or anything added by hand) - those
    are left alone. Anything we do manage (LABEL_TAXONOMY today, or a retired
    phase/*, area/*, type/* label from before this migration) that isn't desired
    is dropped."""
    foreign = {label for label in existing_labels if not _is_managed_label(label)}
    return foreign | desired_labels


@dataclass(frozen=True)
class Action:
    kind: str
    item_id: str
    detail: dict[str, Any] = field(default_factory=dict)


def resolve_issue_number(
    item: BacklogItem, by_number: dict[int, ExistingIssue], by_marker: dict[str, int]
) -> int | None:
    if item.github_issue is not None:
        return item.github_issue
    return by_marker.get(item.id)


def resolve_all_issue_numbers(
    items: list[BacklogItem], by_number: dict[int, ExistingIssue], by_marker: dict[str, int]
) -> dict[str, int]:
    numbers: dict[str, int] = {}
    for item in items:
        number = resolve_issue_number(item, by_number, by_marker)
        if number is not None:
            numbers[item.id] = number
    return numbers


def plan_sync(
    items: list[BacklogItem],
    by_number: dict[int, ExistingIssue],
    by_marker: dict[str, int],
    linked_sub_issues: dict[int, set[int]] | None = None,
    project_priorities: dict[int, str | None] | None = None,
) -> tuple[list[Action], list[str]]:
    """`project_priorities` maps issue number -> its current Priority field value on
    the Projects-v2 board (None if the issue is on the board with the field unset).
    A number's absence means the issue isn't on the board at all yet.

    Passing None for the whole dict (the default) - as opposed to an empty dict,
    which means "the board is reachable and has nothing on it yet" - skips Priority
    syncing entirely. main() does this when Projects-v2 access isn't available
    (e.g. CI's default GITHUB_TOKEN, which cannot reach Projects v2 at all)."""
    linked_sub_issues = linked_sub_issues or {}
    sync_priority = project_priorities is not None
    project_priorities = project_priorities or {}
    actions: list[Action] = []
    errors: list[str] = []

    for item in items:
        number = resolve_issue_number(item, by_number, by_marker)

        if item.github_issue is not None and item.github_issue not in by_number:
            errors.append(f"{item.id}: github_issue {item.github_issue} does not exist")
            continue

        desired_body = render_body(item)
        desired_labels = set(item.labels)

        if number is None:
            actions.append(
                Action(
                    "create",
                    item.id,
                    {
                        "title": item.title,
                        "body": desired_body,
                        "labels": sorted(desired_labels),
                        "milestone": item.milestone,
                        "issue_type": item.issue_type,
                    },
                )
            )
            # A brand-new issue can't already be on the project board.
            if sync_priority:
                actions.append(Action("set_priority", item.id, {"priority": item.priority}))
            continue

        existing = by_number[number]
        if existing.body.strip() != desired_body.strip():
            actions.append(Action("update_body", item.id, {"number": number, "body": desired_body}))

        reconciled = _reconciled_labels(existing.labels, desired_labels)
        if reconciled != set(existing.labels):
            actions.append(
                Action("set_labels", item.id, {"number": number, "labels": sorted(reconciled)})
            )

        if existing.milestone != item.milestone:
            actions.append(
                Action("set_milestone", item.id, {"number": number, "milestone": item.milestone})
            )

        if existing.issue_type != item.issue_type:
            actions.append(
                Action("set_type", item.id, {"number": number, "issue_type": item.issue_type})
            )

        if sync_priority and (
            number not in project_priorities or project_priorities[number] != item.priority
        ):
            actions.append(
                Action("set_priority", item.id, {"number": number, "priority": item.priority})
            )

    numbers_by_id = resolve_all_issue_numbers(items, by_number, by_marker)
    for item in items:
        if item.parent_id is None:
            continue
        parent_number = numbers_by_id.get(item.parent_id)
        child_number = numbers_by_id.get(item.id)
        already_linked = (
            parent_number is not None
            and child_number is not None
            and child_number in by_number
            and by_number[child_number].database_id in linked_sub_issues.get(parent_number, set())
        )
        if not already_linked:
            actions.append(Action("ensure_sub_issue", item.id, {"parent_id": item.parent_id}))

    return actions, errors


def plan_labels(existing_names: set[str]) -> list[str]:
    """Label names from the taxonomy that don't exist yet, in a stable order."""
    return [name for name in LABEL_TAXONOMY if name not in existing_names]


def plan_milestones(existing_titles: set[str]) -> list[str]:
    """Milestone titles from KNOWN_MILESTONES that don't exist yet, in a stable order."""
    return [title for title in KNOWN_MILESTONES if title not in existing_titles]


# --- GitHub I/O (thin - all state-reading/mutating calls go through `gh`) ----------


class GhError(RuntimeError):
    pass


def _gh_get(path: str) -> Any:
    # encoding="utf-8" is required explicitly: subprocess otherwise decodes with the
    # platform's default (cp1252 on Windows), which raises on the first non-ASCII byte
    # in a GitHub-returned title or body (an em-dash in a milestone name, for example).
    result = subprocess.run(
        ["gh", "api", path, "--paginate"], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise GhError(f"GET {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def _gh_send(method: str, path: str, payload: dict[str, Any]) -> Any:
    result = subprocess.run(
        ["gh", "api", path, "-X", method, "--input", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=json.dumps(payload),
    )
    if result.returncode != 0:
        raise GhError(f"{method} {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def _gh_graphql(query: str, **variables: str) -> dict[str, Any]:
    # Projects-v2 (the Priority field) has no REST surface at all - GraphQL is the
    # only way to create/read/write it. Variables are passed through `-f`, which
    # gh's own GraphQL command treats as plain string arguments.
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        cmd += ["-f", f"{key}={value}"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise GhError(f"GraphQL request failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise GhError(f"GraphQL errors: {payload['errors']}")
    return dict(payload["data"])


class GhClient:
    """Thin wrapper around `gh api`. Not exercised by the unit test suite beyond its
    pure JSON-shaping helpers - the planning functions above carry the real coverage,
    and this class's actual network behaviour is only meaningfully checked by running
    it (docs.yml runs the --dry-run form on every PR touching docs/backlog/**)."""

    def fetch_issues(self) -> tuple[dict[int, ExistingIssue], dict[str, int]]:
        raw_issues = _gh_get("repos/:owner/:repo/issues?state=all&per_page=100") or []
        by_number: dict[int, ExistingIssue] = {}
        by_marker: dict[str, int] = {}
        for raw in raw_issues:
            if "pull_request" in raw:
                continue
            body = raw.get("body") or ""
            issue = ExistingIssue(
                number=raw["number"],
                database_id=raw["id"],
                node_id=raw["node_id"],
                body=body,
                labels=frozenset(label["name"] for label in raw.get("labels", [])),
                milestone=(raw.get("milestone") or {}).get("title"),
                issue_type=(raw.get("type") or {}).get("name"),
                state=raw["state"],
            )
            by_number[issue.number] = issue
            match = MARKER_PATTERN.search(body)
            if match:
                by_marker[match.group("id")] = issue.number
        return by_number, by_marker

    def fetch_label_names(self) -> set[str]:
        raw = _gh_get("repos/:owner/:repo/labels?per_page=100") or []
        return {label["name"] for label in raw}

    def fetch_milestones(self) -> dict[str, int]:
        raw = _gh_get("repos/:owner/:repo/milestones?state=all&per_page=100") or []
        return {milestone["title"]: milestone["number"] for milestone in raw}

    def create_label(self, name: str) -> None:
        description, color = LABEL_TAXONOMY[name]
        _gh_send(
            "POST",
            "repos/:owner/:repo/labels",
            {"name": name, "color": color, "description": description},
        )

    def create_milestone(self, title: str) -> int:
        raw = _gh_send("POST", "repos/:owner/:repo/milestones", {"title": title})
        return int(raw["number"])

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        milestone_number: int | None,
        issue_type: str,
    ) -> ExistingIssue:
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "labels": labels,
            "type": issue_type,
        }
        if milestone_number is not None:
            payload["milestone"] = milestone_number
        raw = _gh_send("POST", "repos/:owner/:repo/issues", payload)
        return ExistingIssue(
            number=raw["number"],
            database_id=raw["id"],
            node_id=raw["node_id"],
            body=raw.get("body") or "",
            labels=frozenset(labels),
            milestone=None,
            issue_type=issue_type,
            state="OPEN",
        )

    def update_body(self, number: int, body: str) -> None:
        _gh_send("PATCH", f"repos/:owner/:repo/issues/{number}", {"body": body})

    def set_labels(self, number: int, labels: list[str]) -> None:
        # PUT replaces the issue's full label set - the desired list passed in must
        # already include any foreign (non-taxonomy) labels the caller wants kept.
        _gh_send("PUT", f"repos/:owner/:repo/issues/{number}/labels", {"labels": labels})

    def set_milestone(self, number: int, milestone_number: int) -> None:
        _gh_send("PATCH", f"repos/:owner/:repo/issues/{number}", {"milestone": milestone_number})

    def set_issue_type(self, number: int, issue_type: str) -> None:
        _gh_send("PATCH", f"repos/:owner/:repo/issues/{number}", {"type": issue_type})

    def fetch_sub_issue_ids(self, parent_number: int) -> set[int]:
        raw = _gh_get(f"repos/:owner/:repo/issues/{parent_number}/sub_issues") or []
        return {sub["id"] for sub in raw}

    def add_sub_issue(self, parent_number: int, child_database_id: int) -> None:
        _gh_send(
            "POST",
            f"repos/:owner/:repo/issues/{parent_number}/sub_issues",
            {"sub_issue_id": child_database_id},
        )

    def repo_owner_login(self) -> str:
        raw = _gh_get("repos/:owner/:repo") or {}
        return str(raw["owner"]["login"])

    def ensure_project(self, owner_login: str) -> str:
        """Node id of the PROJECT_TITLE Projects-v2 board owned by `owner_login`,
        creating it (and linking it to this repo) if it doesn't exist yet."""
        data = _gh_graphql(
            "query($login: String!) { organization(login: $login) { id "
            "projectsV2(first: 100) { nodes { id title } } } }",
            login=owner_login,
        )
        org = data["organization"]
        for node in org["projectsV2"]["nodes"]:
            if node["title"] == PROJECT_TITLE:
                return str(node["id"])

        created = _gh_graphql(
            "mutation($ownerId: ID!, $title: String!) { createProjectV2(input: "
            "{ownerId: $ownerId, title: $title}) { projectV2 { id } } }",
            ownerId=org["id"],
            title=PROJECT_TITLE,
        )
        project_id = str(created["createProjectV2"]["projectV2"]["id"])

        repo_raw = _gh_get("repos/:owner/:repo") or {}
        _gh_graphql(
            "mutation($projectId: ID!, $repositoryId: ID!) { linkProjectV2ToRepository"
            "(input: {projectId: $projectId, repositoryId: $repositoryId}) "
            "{ repository { id } } }",
            projectId=project_id,
            repositoryId=str(repo_raw["node_id"]),
        )
        return project_id

    def ensure_priority_field(self, project_id: str) -> tuple[str, dict[str, str]]:
        """Returns (field node id, {"MUST": option id, ...}), creating the field
        (and any of PRIORITIES missing from it) if needed."""
        data = _gh_graphql(
            "query($projectId: ID!) { node(id: $projectId) { ... on ProjectV2 { "
            "fields(first: 50) { nodes { ... on ProjectV2SingleSelectField "
            "{ id name options { id name } } } } } } }",
            projectId=project_id,
        )
        for existing_field in data["node"]["fields"]["nodes"]:
            if existing_field.get("name") == PRIORITY_FIELD_NAME:
                return str(existing_field["id"]), {
                    opt["name"]: opt["id"] for opt in existing_field["options"]
                }

        option_literals = ", ".join(
            f'{{name: "{p}", color: {PRIORITY_OPTION_COLORS[p]}, description: ""}}'
            for p in PRIORITIES
        )
        created = _gh_graphql(
            "mutation($projectId: ID!) { createProjectV2Field(input: {projectId: "
            f'$projectId, dataType: SINGLE_SELECT, name: "{PRIORITY_FIELD_NAME}", '
            f"singleSelectOptions: [{option_literals}]}}) {{ projectV2Field {{ "
            "... on ProjectV2SingleSelectField { id options { id name } } } } }",
            projectId=project_id,
        )
        field = created["createProjectV2Field"]["projectV2Field"]
        return str(field["id"]), {opt["name"]: opt["id"] for opt in field["options"]}

    def fetch_project_priorities(self, project_id: str) -> dict[int, dict[str, Any]]:
        """issue number -> {"item_id": ..., "priority": <name or None>}, for every
        issue from this repo currently on the board (assumes under 100 items -
        this project tracks one repo's backlog, not a cross-repo board)."""
        data = _gh_graphql(
            "query($projectId: ID!) { node(id: $projectId) { ... on ProjectV2 { "
            "items(first: 100) { nodes { id content { ... on Issue { number } } "
            'fieldValueByName(name: "Priority") { ... on '
            "ProjectV2ItemFieldSingleSelectValue { name } } } } } } }",
            projectId=project_id,
        )
        result: dict[int, dict[str, Any]] = {}
        for node in data["node"]["items"]["nodes"]:
            number = (node.get("content") or {}).get("number")
            if number is None:
                continue
            priority_value = node.get("fieldValueByName") or {}
            result[number] = {"item_id": node["id"], "priority": priority_value.get("name")}
        return result

    def add_project_item(self, project_id: str, issue_node_id: str) -> str:
        data = _gh_graphql(
            "mutation($projectId: ID!, $contentId: ID!) { addProjectV2ItemById(input: "
            "{projectId: $projectId, contentId: $contentId}) { item { id } } }",
            projectId=project_id,
            contentId=issue_node_id,
        )
        return str(data["addProjectV2ItemById"]["item"]["id"])

    def set_priority_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        _gh_graphql(
            "mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) "
            "{ updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: "
            "$itemId, fieldId: $fieldId, value: {singleSelectOptionId: $optionId}}) "
            "{ projectV2Item { id } } }",
            projectId=project_id,
            itemId=item_id,
            fieldId=field_id,
            optionId=option_id,
        )


# --- Apply / dry-run ----------------------------------------------------------------


def print_plan(
    label_actions: list[str], milestone_actions: list[str], actions: list[Action]
) -> None:
    if not (label_actions or milestone_actions or actions):
        print("backlog_sync: no changes.")
        return
    for name in label_actions:
        print(f"  + create label {name}")
    for title in milestone_actions:
        print(f"  + create milestone {title!r}")
    for action in actions:
        if action.kind == "create":
            print(f"  + create issue for {action.item_id}: {action.detail['title']!r}")
        elif action.kind == "update_body":
            print(f"  ~ update body of #{action.detail['number']} ({action.item_id})")
        elif action.kind == "set_labels":
            print(
                f"  ~ set labels of #{action.detail['number']} ({action.item_id}) "
                f"to {action.detail['labels']}"
            )
        elif action.kind == "set_milestone":
            print(
                f"  ~ set milestone of #{action.detail['number']} ({action.item_id}) "
                f"to {action.detail['milestone']!r}"
            )
        elif action.kind == "set_type":
            print(
                f"  ~ set issue type of #{action.detail['number']} ({action.item_id}) "
                f"to {action.detail['issue_type']!r}"
            )
        elif action.kind == "set_priority":
            print(f"  ~ set priority of {action.item_id} to {action.detail['priority']!r}")
        elif action.kind == "ensure_sub_issue":
            print(
                f"  ~ ensure {action.item_id} is linked as a sub-issue of {action.detail['parent_id']}"
            )


def apply_plan(
    client: GhClient,
    items: list[BacklogItem],
    by_number: dict[int, ExistingIssue],
    by_marker: dict[str, int],
    label_actions: list[str],
    milestone_actions: list[str],
    milestone_numbers: dict[str, int],
    actions: list[Action],
    project_id: str | None,
    priority_field_id: str | None,
    priority_option_ids: dict[str, str],
    project_items: dict[int, dict[str, Any]],
) -> None:
    # project_id/priority_field_id are None only when Projects-v2 access was
    # unavailable (see main()), in which case plan_sync never emitted a
    # "set_priority" action, so the branch below never actually runs with them
    # unset.
    for name in label_actions:
        client.create_label(name)

    for title in milestone_actions:
        milestone_numbers[title] = client.create_milestone(title)

    resolved: dict[str, int] = {}
    resolved_issues: dict[str, ExistingIssue] = {}
    actions_by_item: dict[str, list[Action]] = {}
    for action in actions:
        actions_by_item.setdefault(action.item_id, []).append(action)

    for item in items:
        # Resolved upfront (not just as a post-loop fallback) so "set_priority" can
        # rely on resolved[item.id] uniformly, whether this item already had an
        # issue or gets one from a "create" action later in this same loop body.
        if item.id not in resolved:
            number = resolve_issue_number(item, by_number, by_marker)
            if number is not None:
                resolved[item.id] = number
                resolved_issues[item.id] = by_number[number]

        for action in actions_by_item.get(item.id, []):
            if action.kind == "create":
                issue = client.create_issue(
                    action.detail["title"],
                    action.detail["body"],
                    action.detail["labels"],
                    milestone_numbers.get(action.detail["milestone"]),
                    action.detail["issue_type"],
                )
                resolved[item.id] = issue.number
                resolved_issues[item.id] = issue
            elif action.kind == "update_body":
                client.update_body(action.detail["number"], action.detail["body"])
            elif action.kind == "set_labels":
                client.set_labels(action.detail["number"], action.detail["labels"])
            elif action.kind == "set_milestone":
                client.set_milestone(
                    action.detail["number"], milestone_numbers[action.detail["milestone"]]
                )
            elif action.kind == "set_type":
                client.set_issue_type(action.detail["number"], action.detail["issue_type"])
            elif action.kind == "set_priority":
                assert project_id is not None and priority_field_id is not None, (
                    "plan_sync only emits set_priority when project state was fetched"
                )
                number = resolved[item.id]
                existing_item = project_items.get(number)
                if existing_item is not None:
                    project_item_id = existing_item["item_id"]
                else:
                    project_item_id = client.add_project_item(
                        project_id, resolved_issues[item.id].node_id
                    )
                client.set_priority_field(
                    project_id,
                    project_item_id,
                    priority_field_id,
                    priority_option_ids[action.detail["priority"]],
                )

    sub_issue_cache: dict[int, set[int]] = {}
    for action in actions:
        if action.kind != "ensure_sub_issue":
            continue
        parent_number = resolved[action.detail["parent_id"]]
        child_number = resolved[action.item_id]
        child_database_id = resolved_issues[action.item_id].database_id
        if parent_number not in sub_issue_cache:
            sub_issue_cache[parent_number] = client.fetch_sub_issue_ids(parent_number)
        if child_database_id not in sub_issue_cache[parent_number]:
            client.add_sub_issue(parent_number, child_database_id)
            sub_issue_cache[parent_number].add(child_database_id)
        del child_number  # only used for readability above


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Apply the plan. Default is dry-run.")
    args = parser.parse_args()

    items, errors = load_backlog_items()
    if errors:
        print(f"backlog_sync: {len(errors)} problem(s) in docs/backlog/*.yaml:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    client = GhClient()
    try:
        by_number, by_marker = client.fetch_issues()
        label_names = client.fetch_label_names()
        milestone_numbers = client.fetch_milestones()

        numbers_by_id = resolve_all_issue_numbers(items, by_number, by_marker)
        parent_numbers = {
            numbers_by_id[item.parent_id]
            for item in items
            if item.parent_id is not None and item.parent_id in numbers_by_id
        }
        linked_sub_issues = {
            number: client.fetch_sub_issue_ids(number) for number in parent_numbers
        }
    except GhError as exc:
        print(f"backlog_sync: {exc}", file=sys.stderr)
        return 1

    # Projects-v2 (the Priority field) requires a PAT or GitHub App token with the
    # 'project' scope - CI's default GITHUB_TOKEN cannot reach it at all, with no
    # `permissions:` setting able to grant it. Degrade gracefully rather than fail
    # the whole sync: skip Priority syncing (project_priorities stays None, which
    # plan_sync treats as "don't touch priority") and say why.
    project_id: str | None = None
    priority_field_id: str | None = None
    priority_option_ids: dict[str, str] = {}
    project_items: dict[int, dict[str, Any]] = {}
    project_priorities: dict[int, str | None] | None = None
    try:
        owner_login = client.repo_owner_login()
        project_id = client.ensure_project(owner_login)
        priority_field_id, priority_option_ids = client.ensure_priority_field(project_id)
        project_items = client.fetch_project_priorities(project_id)
        project_priorities = {number: entry["priority"] for number, entry in project_items.items()}
    except GhError as exc:
        print(
            f"warning: Projects-v2 (Priority) sync unavailable - {exc}\n"
            "warning: skipping Priority sync. Expected when running with the default "
            "GITHUB_TOKEN (e.g. in CI); run locally with a PAT carrying the 'project' "
            "scope (gh auth refresh -s project) to sync priorities.",
            file=sys.stderr,
        )

    actions, plan_errors = plan_sync(
        items, by_number, by_marker, linked_sub_issues, project_priorities
    )
    if plan_errors:
        print(f"backlog_sync: {len(plan_errors)} problem(s):", file=sys.stderr)
        for error in plan_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    label_actions = plan_labels(label_names)
    milestone_actions = plan_milestones(set(milestone_numbers))

    if not args.apply:
        print_plan(label_actions, milestone_actions, actions)
        print(
            f"\nbacklog_sync: dry-run, {len(items)} backlog items, no changes made. Pass --apply to execute."
        )
        return 0

    try:
        apply_plan(
            client,
            items,
            by_number,
            by_marker,
            label_actions,
            milestone_actions,
            milestone_numbers,
            actions,
            project_id,
            priority_field_id,
            priority_option_ids,
            project_items,
        )
    except GhError as exc:
        print(f"backlog_sync: {exc}", file=sys.stderr)
        return 1

    print(f"backlog_sync: applied. {len(items)} backlog items reconciled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
