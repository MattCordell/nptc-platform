#!/usr/bin/env python3
"""CodeQL SARIF summary and severity gate (issue #87).

Reads every `*.sarif` file produced by `github/codeql-action/analyze` and either:

  * `summarize` - renders every finding as a markdown line, appended to a summary
    file (`$GITHUB_STEP_SUMMARY` in CI). Unconditionally advisory.
  * `check-severity` - fails (exit 1) if any finding's rule carries a
    `security-severity` >= `--threshold` (default 7.0, CVSS "high" - matches the
    "high/critical" language NFR-25 already uses for pip-audit/pnpm audit in
    security.yml). Lower-severity and unscored findings stay advisory-only.

Both commands iterate every `*.sarif` file found, not just one picked arbitrarily -
CodeQL emits one file per matrix language, and a future added language or query pack
must not have its findings silently skipped because only the first file glob-matched
was ever read. Rule metadata (including `security-severity`) is looked up on both
`tool.driver.rules` and every entry in `tool.extensions[].rules` - most rules from the
security-extended query pack live in extensions, not the driver, and checking only
the driver would silently gate on nothing.

Usage:
  uv run python scripts/codeql_gate.py summarize sarif-results "$GITHUB_STEP_SUMMARY"
  uv run python scripts/codeql_gate.py check-severity sarif-results --threshold 7.0
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: float | None
    path: str
    line: int | str
    message: str


def find_sarif_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sarif"))


def _rule_index(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rule id -> rule metadata for one SARIF run, combining the tool driver
    (built-in rules) and every tool extension (query-pack rules)."""
    tool = run.get("tool", {})
    sources = [tool.get("driver", {}), *tool.get("extensions", [])]
    rules: dict[str, dict[str, Any]] = {}
    for source in sources:
        for rule in source.get("rules", []):
            rules[rule["id"]] = rule
    return rules


def extract_findings(sarif: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for run in sarif.get("runs", []):
        rules_by_id = _rule_index(run)
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "?")
            rule = rules_by_id.get(rule_id, {})
            severity_raw = rule.get("properties", {}).get("security-severity")
            severity = float(severity_raw) if severity_raw is not None else None
            text = result.get("message", {}).get("text", "")
            message = text.splitlines()[0] if text else ""
            # `locations` can legitimately be an empty list (SARIF permits it, and
            # CodeQL emits it for some non-location-bound results), not just absent
            # - `result.get("locations", [{}])` alone only covers the absent case
            # and raises IndexError on `[][0]`. `or [{}]` covers both.
            locations = result.get("locations") or [{}]
            loc = locations[0].get("physicalLocation", {})
            path = loc.get("artifactLocation", {}).get("uri", "?")
            line = loc.get("region", {}).get("startLine", "?")
            findings.append(Finding(rule_id, severity, path, line, message))
    return findings


def load_findings(sarif_paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sarif_paths:
        with path.open(encoding="utf-8") as f:
            sarif = json.load(f)
        findings.extend(extract_findings(sarif))
    return findings


def render_summary(sarif_paths: list[Path], findings: list[Finding]) -> str:
    names = ", ".join(p.name for p in sarif_paths)
    lines = [f"### CodeQL: {len(findings)} finding(s) in {names}", ""]
    for finding in findings:
        lines.append(f"- `{finding.rule_id}` {finding.path}:{finding.line} - {finding.message}")
    return "\n".join(lines) + "\n"


def cmd_summarize(sarif_dir: Path, summary_path: Path) -> int:
    sarif_paths = find_sarif_files(sarif_dir)
    if not sarif_paths:
        print(f"::error::no .sarif files found under {sarif_dir}", file=sys.stderr)
        return 1
    findings = load_findings(sarif_paths)
    out = render_summary(sarif_paths, findings)
    print(out)
    with summary_path.open("a", encoding="utf-8") as f:
        f.write(out)
    return 0


def cmd_check_severity(sarif_dir: Path, threshold: float) -> int:
    sarif_paths = find_sarif_files(sarif_dir)
    if not sarif_paths:
        print(f"::error::no .sarif files found under {sarif_dir}", file=sys.stderr)
        return 1
    failed = False
    for finding in load_findings(sarif_paths):
        if finding.severity is not None and finding.severity >= threshold:
            print(
                f"::error::{finding.rule_id} (security-severity {finding.severity}) "
                f"{finding.path}:{finding.line} - {finding.message}"
            )
            failed = True
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_summarize = sub.add_parser(
        "summarize", help="Append every finding to a markdown summary file"
    )
    p_summarize.add_argument("sarif_dir", type=Path)
    p_summarize.add_argument("summary_path", type=Path)

    p_severity = sub.add_parser(
        "check-severity", help="Exit non-zero if any finding meets the severity threshold"
    )
    p_severity.add_argument("sarif_dir", type=Path)
    p_severity.add_argument("--threshold", type=float, default=7.0)

    args = parser.parse_args()

    if args.command == "summarize":
        return cmd_summarize(args.sarif_dir, args.summary_path)
    return cmd_check_severity(args.sarif_dir, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
