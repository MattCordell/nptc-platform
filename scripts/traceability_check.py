#!/usr/bin/env python3
"""Requirements traceability check (Foundation issue F-5).

Cross-checks docs/requirements/requirements.yaml against the test suite and the
backlog, and regenerates docs/requirements/traceability.md. Exits non-zero and
prints every problem found, rather than stopping at the first one, so a single
CI run surfaces the whole list.

Fails when:
  * an FR-nn/NFR-nn ID used in a test marker or a backlog item is not in
    requirements.yaml (a typo, or the requirement was renumbered);
  * a requirement marked `implemented` in requirements.yaml has no test
    carrying `@pytest.mark.req("<id>")`;
  * a MUST-priority requirement has no backlog item referencing it. Skipped,
    with a warning, until docs/backlog/ exists (Foundation issue F-6).

Deliberately not enforced: that every SHOULD/MAY has a backlog item. Those are
allowed to be deferred, and a check that nags about them gets ignored.

Usage: uv run python scripts/traceability_check.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = ROOT / "docs" / "requirements" / "requirements.yaml"
TRACEABILITY_REPORT = ROOT / "docs" / "requirements" / "traceability.md"
TEST_DIRS = [ROOT / "backend" / "tests", ROOT / "transform" / "tests", ROOT / "shared" / "tests"]
BACKLOG_DIR = ROOT / "docs" / "backlog"

ID_PATTERN = re.compile(r"^(FR|NFR)-\d{2,}$")
MARKER_PATTERN = re.compile(r'pytest\.mark\.req\(\s*["\'](?P<id>(?:FR|NFR)-\d+)["\']\s*\)')
VALID_PRIORITIES = {"MUST", "SHOULD", "MAY"}
VALID_STATUSES = {"planned", "in-progress", "implemented", "deferred", "n-a"}
VALID_PHASES = {"foundation", "p0", "p1", "p2", "p3", "p4", "p5", "governance"}


def _display_path(path: Path) -> str:
    """Path relative to the repo root for readability, or absolute if it isn't
    one (e.g. under a test's tmp_path fixture, outside ROOT entirely)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Requirement:
    id: str
    priority: str
    phase: str
    title: str
    status: str
    notes: str


def load_requirements() -> tuple[dict[str, Requirement], list[str]]:
    errors: list[str] = []
    raw = yaml.safe_load(REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return {}, [f"{REQUIREMENTS_FILE}: expected a YAML list at the top level"]

    requirements: dict[str, Requirement] = {}
    for entry in raw:
        rid = entry.get("id", "<missing id>")
        if not ID_PATTERN.match(str(rid)):
            errors.append(f"{rid}: id does not match FR-nn/NFR-nn")
            continue
        if rid in requirements:
            errors.append(f"{rid}: duplicate entry")
            continue
        priority = entry.get("priority", "")
        if priority not in VALID_PRIORITIES:
            errors.append(f"{rid}: priority '{priority}' is not one of {sorted(VALID_PRIORITIES)}")
        phase = entry.get("phase", "")
        if phase not in VALID_PHASES:
            errors.append(f"{rid}: phase '{phase}' is not one of {sorted(VALID_PHASES)}")
        status = entry.get("status", "")
        if status not in VALID_STATUSES:
            errors.append(f"{rid}: status '{status}' is not one of {sorted(VALID_STATUSES)}")
        requirements[rid] = Requirement(
            id=rid,
            priority=priority,
            phase=phase,
            title=str(entry.get("title", "")),
            status=status,
            notes=str(entry.get("notes", "")),
        )
    return requirements, errors


def collect_test_markers() -> dict[str, list[str]]:
    references: dict[str, list[str]] = {}
    for test_dir in TEST_DIRS:
        if not test_dir.is_dir():
            continue
        for path in sorted(test_dir.rglob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                match = MARKER_PATTERN.search(line)
                if match:
                    location = f"{_display_path(path)}:{lineno}"
                    references.setdefault(match.group("id"), []).append(location)
    return references


def _walk_backlog_item(
    item: dict[str, Any], file_label: str, references: dict[str, list[str]]
) -> None:
    """Attribute an item's own `requirements:` to its own id, then recurse into
    `children:` (native GitHub sub-issues, §3.1/§3.2 of the delivery plan) so a
    requirement covered only by a child is not misattributed to its parent."""
    item_id = item.get("id", "?")
    for rid in item.get("requirements", []) or []:
        references.setdefault(rid, []).append(f"{file_label}#{item_id}")
    for child in item.get("children", []) or []:
        _walk_backlog_item(child, file_label, references)


def collect_backlog_references() -> tuple[dict[str, list[str]], set[str]]:
    """Returns (requirement id -> backlog item locations, phases with a backlog file).

    The second value - e.g. {"foundation", "p0", "p1"} once foundation.yaml, p0.yaml
    and p1.yaml exist - is what scopes the MUST-has-a-backlog-item check below: a
    MUST requirement in a phase that has not been backlogged yet (P2-P5 today) is
    not a defect, it just hasn't been written yet.
    """
    backlog_files = sorted(BACKLOG_DIR.glob("*.yaml")) if BACKLOG_DIR.is_dir() else []
    if not backlog_files:
        # docs/backlog/ exists from Foundation issue F-1 scaffolding (a README
        # placeholder only) well before it holds any backlog YAML (F-6), so
        # presence of the directory itself is not a useful signal here.
        return {}, set()

    references: dict[str, list[str]] = {}
    backlogged_phases: set[str] = set()
    for path in backlog_files:
        items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for item in items:
            _walk_backlog_item(item, _display_path(path), references)
        backlogged_phases.add(path.stem)
    return references, backlogged_phases


def run_checks(
    requirements: dict[str, Requirement],
    test_refs: dict[str, list[str]],
    backlog_refs: dict[str, list[str]],
    backlogged_phases: set[str],
) -> list[str]:
    errors: list[str] = []

    for rid in test_refs:
        if rid not in requirements:
            locations = ", ".join(test_refs[rid])
            errors.append(
                f"{rid}: referenced by a test marker ({locations}) but not in requirements.yaml"
            )

    for rid in backlog_refs:
        if rid not in requirements:
            locations = ", ".join(backlog_refs[rid])
            errors.append(
                f"{rid}: referenced by a backlog item ({locations}) but not in requirements.yaml"
            )

    for req in requirements.values():
        if req.status == "implemented" and req.id not in test_refs:
            errors.append(
                f"{req.id}: status is 'implemented' but no test carries "
                f'@pytest.mark.req("{req.id}")'
            )

    if backlogged_phases:
        for req in requirements.values():
            if (
                req.priority == "MUST"
                and req.phase in backlogged_phases
                and req.id not in backlog_refs
            ):
                errors.append(f"{req.id}: MUST-priority requirement has no backlog item")
    else:
        print(
            "warning: docs/backlog/ does not exist yet (Foundation issue F-6) - "
            "skipping the MUST-has-a-backlog-item check",
            file=sys.stderr,
        )

    return errors


def _escape_cell(text: str) -> str:
    """Escape characters that would otherwise be parsed as markdown table
    syntax - a literal '|' in a requirement's title (e.g. FR-84) would
    otherwise split it into extra columns."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(
    requirements: dict[str, Requirement],
    test_refs: dict[str, list[str]],
    backlog_refs: dict[str, list[str]],
) -> str:
    lines = [
        "# Requirements traceability report",
        "",
        "Generated by `scripts/traceability_check.py` from `docs/requirements/requirements.yaml`. "
        "Do not hand-edit; it is overwritten on every run.",
        "",
        f"Total requirements: {len(requirements)}. "
        f"With a test: {sum(1 for r in requirements if r in test_refs)}. "
        f"With a backlog item: {sum(1 for r in requirements if r in backlog_refs)}.",
        "",
        "| ID | Priority | Phase | Status | Title | Tests | Backlog |",
        "|---|---|---|---|---|---|---|",
    ]
    for rid in sorted(requirements, key=lambda r: (r.split("-")[0], int(r.split("-")[1]))):
        req = requirements[rid]
        title = _escape_cell(req.title)
        tests = _escape_cell("<br>".join(test_refs.get(rid, [])) or "-")
        backlog = _escape_cell("<br>".join(backlog_refs.get(rid, [])) or "-")
        lines.append(
            f"| {req.id} | {req.priority} | {req.phase} | {req.status} | {title} | {tests} | {backlog} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    requirements, schema_errors = load_requirements()
    test_refs = collect_test_markers()
    backlog_refs, backlogged_phases = collect_backlog_references()

    errors = schema_errors + run_checks(requirements, test_refs, backlog_refs, backlogged_phases)

    TRACEABILITY_REPORT.write_text(
        render_report(requirements, test_refs, backlog_refs), encoding="utf-8", newline="\n"
    )

    if errors:
        print(f"traceability_check: {len(errors)} problem(s) found:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"traceability_check: {len(requirements)} requirements, no problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
