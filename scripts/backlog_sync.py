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

# The full label taxonomy (plan SS3.4), bootstrapped regardless of which labels
# today's backlog items actually use, so a future phase's backlog PR never has to
# remember to create its own labels. name -> (description, colour).
LABEL_TAXONOMY: dict[str, tuple[str, str]] = {
    "phase/foundation": ("Foundation work: repo layout, CI, governance, tooling", "c2e0c6"),
    "phase/p0": ("P0 - Seeding transform", "c2e0c6"),
    "phase/p1": ("P1 - Core catalogue", "c2e0c6"),
    "phase/p2": ("P2 - Contribution", "c2e0c6"),
    "phase/p3": ("P3 - Validation", "c2e0c6"),
    "phase/p4": ("P4 - Release and export", "c2e0c6"),
    "phase/p5": ("P5 - Hardening", "c2e0c6"),
    "phase/governance": ("Cross-cutting governance / open issues, no fixed phase", "c2e0c6"),
    "priority/must": ("PRD scheduling priority: MUST", "b60205"),
    "priority/should": ("PRD scheduling priority: SHOULD", "d93f0b"),
    "priority/may": ("PRD scheduling priority: MAY", "fbca04"),
    "type/feature": ("User-facing behaviour change", "0e8a16"),
    "type/chore": ("Tooling, infra or process work with no user-facing behaviour", "fef2c0"),
    "type/docs": ("Documentation-only change", "fef2c0"),
    "type/test": ("Test-only change", "fef2c0"),
    "type/spike": ("Time-boxed investigation, not a committed implementation", "d4c5f9"),
    "type/bug": ("Something isn't working", "d73a4a"),
    "type/security": ("Security-relevant change", "d73a4a"),
    "area/api": ("Backend HTTP API", "5319e7"),
    "area/db": ("Database schema and migrations", "5319e7"),
    "area/frontend": ("React/TypeScript client", "5319e7"),
    "area/transform": ("The P0 seeding transform CLI", "5319e7"),
    "area/terminology": ("SNOMED CT / Ontoserver integration", "5319e7"),
    "area/auth": ("Authentication and identity", "5319e7"),
    "area/audit": ("Audit logging", "5319e7"),
    "area/registry": ("Property registry", "5319e7"),
    "area/export": ("Release exports (CSV, FHIR, SPIA spreadsheet)", "5319e7"),
    "area/infra": ("CI, deployment, containers, tooling", "5319e7"),
    "area/ci": ("GitHub Actions workflows", "5319e7"),
    "area/docs": ("Documentation and its tooling", "5319e7"),
    "area/a11y": ("Accessibility", "5319e7"),
    "area/security": ("Security controls, scanning, audits", "5319e7"),
    "status/blocked": ("Blocked on something outside this issue", "e4e669"),
    "status/needs-decision": ("Needs a decision before work can proceed", "e4e669"),
    "status/needs-rcpa-input": ("Needs RCPA-QAP editorial input", "e4e669"),
    "governance/open-issue": ("One of the PRD's numbered open issues (OI-n)", "5319e7"),
}


# --- Schema, parsing, validation --------------------------------------------------


@dataclass(frozen=True)
class BacklogItem:
    id: str
    title: str
    milestone: str
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
    body: str
    labels: frozenset[str]
    milestone: str | None
    state: str


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
) -> tuple[list[Action], list[str]]:
    linked_sub_issues = linked_sub_issues or {}
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
                    },
                )
            )
            continue

        existing = by_number[number]
        if existing.body.strip() != desired_body.strip():
            actions.append(Action("update_body", item.id, {"number": number, "body": desired_body}))

        missing_labels = desired_labels - existing.labels
        if missing_labels:
            actions.append(
                Action("add_labels", item.id, {"number": number, "labels": sorted(missing_labels)})
            )

        if existing.milestone != item.milestone:
            actions.append(
                Action("set_milestone", item.id, {"number": number, "milestone": item.milestone})
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
                body=body,
                labels=frozenset(label["name"] for label in raw.get("labels", [])),
                milestone=(raw.get("milestone") or {}).get("title"),
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
        self, title: str, body: str, labels: list[str], milestone_number: int | None
    ) -> ExistingIssue:
        payload: dict[str, Any] = {"title": title, "body": body, "labels": labels}
        if milestone_number is not None:
            payload["milestone"] = milestone_number
        raw = _gh_send("POST", "repos/:owner/:repo/issues", payload)
        return ExistingIssue(
            number=raw["number"],
            database_id=raw["id"],
            body=raw.get("body") or "",
            labels=frozenset(labels),
            milestone=None,
            state="OPEN",
        )

    def update_body(self, number: int, body: str) -> None:
        _gh_send("PATCH", f"repos/:owner/:repo/issues/{number}", {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        _gh_send("POST", f"repos/:owner/:repo/issues/{number}/labels", {"labels": labels})

    def set_milestone(self, number: int, milestone_number: int) -> None:
        _gh_send("PATCH", f"repos/:owner/:repo/issues/{number}", {"milestone": milestone_number})

    def fetch_sub_issue_ids(self, parent_number: int) -> set[int]:
        raw = _gh_get(f"repos/:owner/:repo/issues/{parent_number}/sub_issues") or []
        return {sub["id"] for sub in raw}

    def add_sub_issue(self, parent_number: int, child_database_id: int) -> None:
        _gh_send(
            "POST",
            f"repos/:owner/:repo/issues/{parent_number}/sub_issues",
            {"sub_issue_id": child_database_id},
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
        elif action.kind == "add_labels":
            print(
                f"  ~ add labels {action.detail['labels']} to #{action.detail['number']} ({action.item_id})"
            )
        elif action.kind == "set_milestone":
            print(
                f"  ~ set milestone of #{action.detail['number']} ({action.item_id}) "
                f"to {action.detail['milestone']!r}"
            )
        elif action.kind == "ensure_sub_issue":
            print(
                f"  ~ ensure {action.item_id} is linked as a sub-issue of {action.detail['parent_id']}"
            )


def apply_plan(
    client: GhClient,
    items: list[BacklogItem],
    by_number: dict[int, ExistingIssue],
    label_actions: list[str],
    milestone_actions: list[str],
    milestone_numbers: dict[str, int],
    actions: list[Action],
) -> None:
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
        for action in actions_by_item.get(item.id, []):
            if action.kind == "create":
                issue = client.create_issue(
                    action.detail["title"],
                    action.detail["body"],
                    action.detail["labels"],
                    milestone_numbers.get(action.detail["milestone"]),
                )
                resolved[item.id] = issue.number
                resolved_issues[item.id] = issue
            elif action.kind == "update_body":
                client.update_body(action.detail["number"], action.detail["body"])
            elif action.kind == "add_labels":
                client.add_labels(action.detail["number"], action.detail["labels"])
            elif action.kind == "set_milestone":
                client.set_milestone(
                    action.detail["number"], milestone_numbers[action.detail["milestone"]]
                )
        if item.id not in resolved:
            number = resolve_issue_number(item, by_number, {})
            if number is not None:
                resolved[item.id] = number
                resolved_issues[item.id] = by_number[number]

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

    actions, plan_errors = plan_sync(items, by_number, by_marker, linked_sub_issues)
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
            client, items, by_number, label_actions, milestone_actions, milestone_numbers, actions
        )
    except GhError as exc:
        print(f"backlog_sync: {exc}", file=sys.stderr)
        return 1

    print(f"backlog_sync: applied. {len(items)} backlog items reconciled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
