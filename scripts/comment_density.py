#!/usr/bin/env python3
"""Measure how much of each source file is prose, and stop that share from growing.

Every physical line gets exactly one category. A line carrying any code token is code,
even with a trailing comment. Otherwise a line inside a module, class or function
docstring is docstring, a comment-only line is comment, and an empty line is blank.
Prose is comment plus docstring lines, and the ratio is prose divided by code.

With no flags the script prints the report. `--check` is the pre-commit hook. It runs two
checks over the files it is given:

- the ratchet: a file under `RATCHET_TREES` may not hold more prose than its allowance
  (see `allowance`);
- the citation check: a comment or docstring line added since the merge-base with
  `origin/main` may not cite an issue number or the review that prompted it.

`--update-baseline` rewrites the baseline from the files on disk. The frontend figures are
an approximation and never feed the baseline or the hook.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import math
import re
import subprocess
import sys
import tokenize
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "comment_density_baseline.json"

RATCHET_TREES = ("backend/src", "transform/src", "shared/src")
BASELINE_RATIO_FLOOR = 0.5
MIN_ALLOWANCE_LINES = 10
MAX_LISTED_LINES = 20

BASE_REF = "origin/main"

FRONTEND_TREE = "frontend/src"
FRONTEND_GENERATED = "frontend/src/api/schema.ts"

CODE = "code"
BLANK = "blank"
COMMENT = "comment"
DOCSTRING = "docstring"

ISSUE_CITATION = "an issue number"
REVIEW_CITATION = "a review round"

ISSUE_PATTERNS = (
    re.compile(r"(?<![\w&])#\d+\b"),
    re.compile(r"\bissues?\s+#?\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:PR|[Pp]ull [Rr]equest)\s+#?\d+\b"),
    re.compile(r"/(?:issues|pull)/\d+"),
)
REVIEW_PATTERNS = (
    re.compile(r"\bround[- ]?\d+\s+(?:review|finding|feedback)", re.IGNORECASE),
    re.compile(r"\breview[- ]round\b", re.IGNORECASE),
    re.compile(r"\breview[- ]finding", re.IGNORECASE),
)
FALLBACK_WARNING = (
    f"comment_density: warning: no merge-base with {BASE_REF} (a shallow clone, or a "
    "remote with another name?), so only changes since HEAD are checked for citations. "
    f"Fetch {BASE_REF} for the full check."
)
CITATION_ADVICE = {
    ISSUE_CITATION: (
        "Put the issue number in the commit message or PR body. "
        "A comment may cite FR-nn, NFR-nn or an ADR instead."
    ),
    REVIEW_CITATION: (
        "Resolve review feedback in the code or in an ADR, not in a comment "
        "that records which review asked for it."
    ),
}

RATCHET_ADVICE = (
    "Remove comment or docstring text that restates the code. Where a reviewer accepts the "
    "increase, or the file was split or merged, run "
    "`uv run python scripts/comment_density.py --update-baseline` and commit the result."
)

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_NON_CODE_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
)


@dataclass(frozen=True)
class Counts:
    code: int = 0
    blank: int = 0
    comment: int = 0
    docstring: int = 0

    @property
    def prose(self) -> int:
        return self.comment + self.docstring

    @property
    def ratio(self) -> float | None:
        return self.prose / self.code if self.code else None


@dataclass(frozen=True)
class BaselineEntry:
    code: int
    prose: int


@dataclass(frozen=True)
class Analysis:
    lines: list[str]
    kinds: list[str]
    comments: dict[int, str]

    def counts(self) -> Counts:
        return count_kinds(self.kinds)

    def prose_text(self, row: int) -> str | None:
        """The text a citation could hide in on this 1-based line, if any."""
        if self.kinds[row - 1] == DOCSTRING:
            return self.lines[row - 1]
        return self.comments.get(row)


def count_kinds(kinds: Iterable[str]) -> Counts:
    tally = Counter(kinds)
    return Counts(tally[CODE], tally[BLANK], tally[COMMENT], tally[DOCSTRING])


def add_counts(items: Iterable[Counts]) -> Counts:
    total = Counts()
    for item in items:
        total = Counts(
            total.code + item.code,
            total.blank + item.blank,
            total.comment + item.comment,
            total.docstring + item.docstring,
        )
    return total


def split_lines(source: str) -> list[str]:
    lines = source.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _docstring_rows(tree: ast.AST) -> set[int]:
    rows: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            rows.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return rows


def analyse_python(source: str) -> Analysis:
    lines = split_lines(source)
    docstring_rows = _docstring_rows(ast.parse(source))
    code_rows: set[int] = set()
    comments: dict[int, str] = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        first, last = token.start[0], token.end[0]
        if token.type == tokenize.COMMENT:
            comments[first] = token.string.lstrip("#")
        elif token.type not in _NON_CODE_TOKENS and not (
            token.type == tokenize.STRING and first in docstring_rows
        ):
            code_rows.update(range(first, last + 1))

    kinds: list[str] = []
    for row, line in enumerate(lines, start=1):
        if row in code_rows:
            kinds.append(CODE)
        elif row in docstring_rows:
            kinds.append(DOCSTRING)
        elif row in comments:
            kinds.append(COMMENT)
        elif not line.strip():
            kinds.append(BLANK)
        else:
            kinds.append(CODE)
    return Analysis(lines, kinds, comments)


_TS_NORMAL, _TS_BLOCK, _TS_TEMPLATE = range(3)


def _closing_quote(line: str, start: int, quote: str) -> int | None:
    i = start
    while i < len(line):
        if line[i] == "\\":
            i += 2
        elif line[i] == quote:
            return i + 1
        else:
            i += 1
    return None


def _scan_typescript_line(line: str, state: int) -> tuple[bool, bool, int]:
    has_code = has_comment = False
    i = 0
    while i < len(line):
        if state == _TS_BLOCK:
            has_comment = True
            end = line.find("*/", i)
            if end < 0:
                break
            i, state = end + 2, _TS_NORMAL
        elif state == _TS_TEMPLATE:
            has_code = True
            template_end = _closing_quote(line, i, "`")
            if template_end is None:
                break
            i, state = template_end, _TS_NORMAL
        elif line[i].isspace():
            i += 1
        elif line.startswith("//", i):
            has_comment = True
            break
        elif line.startswith("/*", i):
            has_comment = True
            i, state = i + 2, _TS_BLOCK
        elif line[i] in "'\"":
            has_code = True
            string_end = _closing_quote(line, i + 1, line[i])
            if string_end is None:
                break
            i = string_end
        elif line[i] == "`":
            has_code = True
            i, state = i + 1, _TS_TEMPLATE
        else:
            has_code = True
            i += 1
    return has_code, has_comment, state


def classify_typescript(source: str) -> list[str]:
    """Approximate: regex literals and JSX text are not parsed, and a quote left open at
    the end of a line is treated as closed there."""
    kinds: list[str] = []
    state = _TS_NORMAL
    for line in split_lines(source):
        inside_template = state == _TS_TEMPLATE
        has_code, has_comment, state = _scan_typescript_line(line, state)
        if has_code or inside_template:
            kinds.append(CODE)
        elif has_comment:
            kinds.append(COMMENT)
        else:
            kinds.append(BLANK)
    return kinds


def python_files(root: Path, tree: str) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in (root / tree).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _is_frontend_test(rel: str) -> bool:
    return ".test." in rel.rsplit("/", 1)[-1] or rel.startswith(f"{FRONTEND_TREE}/test/")


def report_groups(root: Path) -> list[tuple[str, list[str]]]:
    groups = [(tree, python_files(root, tree)) for tree in RATCHET_TREES]
    source: list[str] = []
    tests: list[str] = []
    candidates = sorted(
        path.relative_to(root).as_posix()
        for suffix in ("*.ts", "*.tsx")
        for path in (root / FRONTEND_TREE).rglob(suffix)
        if "node_modules" not in path.parts
    )
    for rel in candidates:
        if rel != FRONTEND_GENERATED:
            (tests if _is_frontend_test(rel) else source).append(rel)
    return [*groups, (f"{FRONTEND_TREE} (source)", source), (f"{FRONTEND_TREE} (tests)", tests)]


def measure(root: Path, rel: str) -> Counts:
    text = (root / rel).read_text(encoding="utf-8")
    if rel.endswith(".py"):
        return analyse_python(text).counts()
    return count_kinds(classify_typescript(text))


_ROW = "{code:>7} {blank:>7} {comment:>8} {docstring:>10} {ratio:>7}  {label}"
_HEADER = _ROW.format(
    code="code",
    blank="blank",
    comment="comment",
    docstring="docstring",
    ratio="ratio",
    label="file",
)


def _row(counts: Counts, label: str) -> str:
    ratio = "-" if counts.ratio is None else f"{counts.ratio:.2f}"
    return _ROW.format(
        code=counts.code,
        blank=counts.blank,
        comment=counts.comment,
        docstring=counts.docstring,
        ratio=ratio,
        label=label,
    )


def render_report(root: Path, *, summary_only: bool) -> str:
    out: list[str] = []
    for label, files in report_groups(root):
        measured = [(rel, measure(root, rel)) for rel in files]
        out.append(f"== {label} ({len(files)} files) ==")
        out.append(_HEADER)
        if not summary_only:
            out.extend(_row(counts, rel) for rel, counts in measured)
        out.append(_row(add_counts(c for _, c in measured), "TOTAL"))
        out.append("")
    return "\n".join(out)


def baseline_entry(counts: Counts) -> dict[str, int | float | None]:
    ratio = None if counts.ratio is None else round(counts.ratio, 3)
    return {"code": counts.code, "prose": counts.prose, "ratio": ratio}


def render_baseline(baseline: dict[str, dict[str, int | float | None]]) -> str:
    rows = [
        f"  {json.dumps(path)}: {json.dumps(entry, sort_keys=True)}"
        for path, entry in sorted(baseline.items())
    ]
    if not rows:
        return "{}\n"
    return "{\n" + ",\n".join(rows) + "\n}\n"


def build_baseline(root: Path) -> dict[str, dict[str, int | float | None]]:
    return {
        rel: baseline_entry(measure(root, rel))
        for tree in RATCHET_TREES
        for rel in python_files(root, tree)
    }


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {rel: BaselineEntry(int(item["code"]), int(item["prose"])) for rel, item in raw.items()}


def allowance(baseline: BaselineEntry | None, code: int) -> float:
    """The prose lines a file may hold: its baseline prose plus the floor ratio for each
    code line added since, and never less than the floor ratio over all its code or the
    minimum line count. Code added since the baseline earns the floor ratio, not the
    file's own, so a file with almost no code cannot scale its prose."""
    base_prose = baseline.prose if baseline else 0
    base_code = baseline.code if baseline else 0
    earned = base_prose + BASELINE_RATIO_FLOOR * max(code - base_code, 0)
    return max(earned, BASELINE_RATIO_FLOOR * code, MIN_ALLOWANCE_LINES)


def stale_baseline_paths(root: Path, baseline: dict[str, BaselineEntry]) -> list[str]:
    """Baseline entries whose file is gone. A move or a delete leaves one behind, and the
    rename mapping in `baseline_for` covers only the diff that contains the move."""
    return sorted(rel for rel in baseline if not (root / rel).is_file())


def exceeds_ratchet(counts: Counts, baseline: BaselineEntry | None) -> bool:
    return counts.prose > allowance(baseline, counts.code)


@dataclass(frozen=True)
class ParsedDiff:
    added: dict[str, set[int]]
    renames: dict[str, str]


def parse_diff(diff: str) -> ParsedDiff:
    """The 1-based lines a `git diff -U0` adds or edits in each file, and each renamed
    file's old path."""
    added: dict[str, set[int]] = {}
    renames: dict[str, str] = {}
    current: set[int] | None = None
    rename_from: str | None = None
    in_header = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            in_header, current, rename_from = True, None, None
        elif in_header and line.startswith("rename from "):
            rename_from = line.removeprefix("rename from ")
        elif in_header and line.startswith("rename to ") and rename_from is not None:
            renames[line.removeprefix("rename to ")] = rename_from
        elif in_header and line.startswith("+++ "):
            target = line[4:]
            current = None if target == "/dev/null" else added.setdefault(target[2:], set())
        elif match := _HUNK.match(line):
            in_header = False
            if current is not None:
                start = int(match[1])
                length = 1 if match[2] is None else int(match[2])
                current.update(range(start, start + length))
    return ParsedDiff(added, renames)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def merge_base(root: Path) -> str | None:
    result = _git(root, "merge-base", "HEAD", BASE_REF)
    base = result.stdout.strip()
    return base if result.returncode == 0 and base else None


def read_diff(root: Path) -> tuple[ParsedDiff, bool]:
    """The parsed diff, and whether it fell back to HEAD because there is no merge-base."""
    base = merge_base(root)
    # The pathspec is every .py file, not just the files under check: rename detection
    # needs the old path in the diff to pair it with the new one.
    result = _git(
        root,
        "-c",
        "core.quotepath=false",
        "diff",
        "-U0",
        "--find-renames",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        base or "HEAD",
        "--",
        "*.py",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return parse_diff(result.stdout), base is None


def find_citations(analysis: Analysis, added: set[int]) -> list[tuple[int, str, str]]:
    """(line, what is cited, where) for each citation on an added comment or docstring line."""
    hits: list[tuple[int, str, str]] = []
    for row in sorted(added):
        if not 1 <= row <= len(analysis.lines):
            continue
        text = analysis.prose_text(row)
        if not text:
            continue
        where = "docstring" if analysis.kinds[row - 1] == DOCSTRING else "comment"
        for cited, patterns in (
            (ISSUE_CITATION, ISSUE_PATTERNS),
            (REVIEW_CITATION, REVIEW_PATTERNS),
        ):
            if any(pattern.search(text) for pattern in patterns):
                hits.append((row, cited, where))
    return hits


def _printable(text: str) -> str:
    return text.strip().encode("ascii", "backslashreplace").decode("ascii")


def citation_failures(rel: str, analysis: Analysis, added: set[int]) -> list[str]:
    return [
        f"{rel}:{row}: this {where} cites {cited}. {CITATION_ADVICE[cited]}"
        for row, cited, where in find_citations(analysis, added)
    ]


def ratchet_failures(
    rel: str, analysis: Analysis, baseline: BaselineEntry | None, added: set[int]
) -> list[str]:
    counts = analysis.counts()
    if not exceeds_ratchet(counts, baseline):
        return []
    limit = math.floor(allowance(baseline, counts.code))
    if baseline is None:
        reference = "no baseline, so the floor applies"
    else:
        reference = f"baseline {baseline.prose} prose over {baseline.code} code"
    listed = [
        row
        for row in sorted(added)
        if 1 <= row <= len(analysis.kinds) and analysis.kinds[row - 1] in (COMMENT, DOCSTRING)
    ]
    first = listed[0] if listed else 1
    out = [
        f"{rel}:{first}: prose is {counts.prose} lines ({counts.comment} comment, "
        f"{counts.docstring} docstring) over {counts.code} code lines; "
        f"the allowance is {limit} ({reference}). {RATCHET_ADVICE}"
    ]
    out.extend(
        f"{rel}:{row}: added prose: {_printable(analysis.lines[row - 1])}"
        for row in listed[:MAX_LISTED_LINES]
    )
    if len(listed) > MAX_LISTED_LINES:
        out.append(
            f"{rel}:{first}: ... and {len(listed) - MAX_LISTED_LINES} more added prose lines"
        )
    return out


def repo_relative(root: Path, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute() and path.is_relative_to(root):
        path = path.relative_to(root)
    return path.as_posix()


def _in_ratchet_scope(rel: str) -> bool:
    return any(rel.startswith(f"{tree}/") for tree in RATCHET_TREES)


@dataclass(frozen=True)
class CheckResult:
    failures: list[str]
    warnings: list[str]


def baseline_for(
    rel: str, baseline: dict[str, BaselineEntry], renames: dict[str, str]
) -> BaselineEntry | None:
    """A file's own entry, or its old path's entry when git sees it as a rename."""
    if rel in baseline:
        return baseline[rel]
    return baseline.get(renames.get(rel, ""))


def run_check(root: Path, baseline_path: Path, paths: Sequence[str]) -> CheckResult:
    files = [rel for rel in (repo_relative(root, raw) for raw in paths) if rel.endswith(".py")]
    files = [rel for rel in files if (root / rel).is_file()]
    if not files:
        return CheckResult([], [])
    baseline = load_baseline(baseline_path)
    diff, used_fallback = read_diff(root)
    failures: list[str] = []
    for rel in files:
        try:
            analysis = analyse_python((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, tokenize.TokenError) as error:
            failures.append(f"{rel}:1: cannot parse this file: {error}")
            continue
        rows = diff.added.get(rel, set())
        if _in_ratchet_scope(rel):
            entry = baseline_for(rel, baseline, diff.renames)
            failures.extend(ratchet_failures(rel, analysis, entry, rows))
        failures.extend(citation_failures(rel, analysis, rows))
    return CheckResult(failures, [FALLBACK_WARNING] if used_fallback else [])


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="run the ratchet and citation checks")
    mode.add_argument(
        "--update-baseline", action="store_true", help="rewrite the baseline from the files on disk"
    )
    parser.add_argument("--summary", action="store_true", help="report per-tree totals only")
    parser.add_argument("files", nargs="*", help="files to check (with --check)")
    args = parser.parse_args(argv)

    if args.update_baseline:
        baseline = build_baseline(root)
        baseline_path.write_text(render_baseline(baseline), encoding="utf-8", newline="\n")
        print(f"Wrote {len(baseline)} entries to {baseline_path.name}")
        return 0

    if args.check:
        try:
            result = run_check(root, baseline_path, args.files)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"comment_density: {error}", file=sys.stderr)
            return 1
        for line in [*result.warnings, *result.failures]:
            print(line, file=sys.stderr)
        return 1 if result.failures else 0

    print(render_report(root, summary_only=args.summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
