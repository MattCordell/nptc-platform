#!/usr/bin/env python3
"""Requirements traceability check (Foundation issue F-5).

Cross-checks docs/requirements/requirements.yaml against the test suite and
regenerates docs/requirements/traceability.md. Exits non-zero and prints every
problem found, rather than stopping at the first one, so a single CI run
surfaces the whole list.

Fails when:
  * an FR-nn/NFR-nn ID used in a test marker is not in requirements.yaml (a
    typo, or the requirement was renumbered);
  * a requirement marked `implemented` in requirements.yaml has no test
    carrying `@pytest.mark.req("<id>")`.

Deliberately not enforced: that a requirement has a corresponding GitHub
issue. The backlog lives on GitHub Issues directly now, and that coverage is
a manual review concern, not a CI gate.

Usage: uv run python scripts/traceability_check.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = ROOT / "docs" / "requirements" / "requirements.yaml"
TRACEABILITY_REPORT = ROOT / "docs" / "requirements" / "traceability.md"
TEST_DIRS = [ROOT / "backend" / "tests", ROOT / "transform" / "tests", ROOT / "shared" / "tests"]

ID_PATTERN = re.compile(r"^(FR|NFR)-\d{2,}$")
MARKER_PATTERN = re.compile(r'pytest\.mark\.req\(\s*["\'](?P<id>(?:FR|NFR)-\d+)["\']\s*\)')
VALID_PRIORITIES = {"MUST", "SHOULD", "MAY"}
VALID_STATUSES = {"planned", "in-progress", "implemented", "deferred", "n-a"}
VALID_PHASES = {"foundation", "p0", "p1", "p2", "p3", "p4", "p5", "governance"}


def _display_path(path: Path) -> str:
    """Path relative to the repo root for readability, or absolute if it isn't
    one (e.g. under a test's tmp_path fixture, outside ROOT entirely).

    Always forward-slashed: str(Path) is backslash-separated on Windows, and this
    value is embedded in the committed traceability.md, which must match byte-for-
    byte regardless of which OS regenerated it (CI runs on ubuntu-latest)."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


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


def run_checks(requirements: dict[str, Requirement], test_refs: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []

    for rid in test_refs:
        if rid not in requirements:
            locations = ", ".join(test_refs[rid])
            errors.append(
                f"{rid}: referenced by a test marker ({locations}) but not in requirements.yaml"
            )

    for req in requirements.values():
        if req.status == "implemented" and req.id not in test_refs:
            errors.append(
                f"{req.id}: status is 'implemented' but no test carries "
                f'@pytest.mark.req("{req.id}")'
            )

    return errors


def _escape_cell(text: str) -> str:
    """Escape characters that would otherwise be parsed as markdown table
    syntax - a literal '|' in a requirement's title (e.g. FR-84) would
    otherwise split it into extra columns."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(requirements: dict[str, Requirement], test_refs: dict[str, list[str]]) -> str:
    lines = [
        "# Requirements traceability report",
        "",
        "Generated by `scripts/traceability_check.py` from `docs/requirements/requirements.yaml`. "
        "Do not hand-edit; it is overwritten on every run.",
        "",
        f"Total requirements: {len(requirements)}. "
        f"With a test: {sum(1 for r in requirements if r in test_refs)}.",
        "",
        "| ID | Priority | Phase | Status | Title | Tests |",
        "|---|---|---|---|---|---|",
    ]
    for rid in sorted(requirements, key=lambda r: (r.split("-")[0], int(r.split("-")[1]))):
        req = requirements[rid]
        title = _escape_cell(req.title)
        tests = _escape_cell("<br>".join(test_refs.get(rid, [])) or "-")
        lines.append(
            f"| {req.id} | {req.priority} | {req.phase} | {req.status} | {title} | {tests} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    requirements, schema_errors = load_requirements()
    test_refs = collect_test_markers()

    errors = schema_errors + run_checks(requirements, test_refs)

    TRACEABILITY_REPORT.write_text(
        render_report(requirements, test_refs), encoding="utf-8", newline="\n"
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
