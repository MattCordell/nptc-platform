"""Unit tests for scripts/comment_density.py.

The regression this file exists to prevent: prose growing back unnoticed. The ratchet must
fail when a file's prose rises past its allowance and pass when only code is removed. The
citation check must fail on a new comment or docstring that cites an issue number or the
review behind it, and stay silent about a citation the diff did not add.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comment_density as cd

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = ROOT / "backend" / "migrations" / "script.py.mako"
CONFIG_PATH = ROOT / ".pre-commit-config.yaml"

SAMPLE = '''"""Module doc
over two lines."""

import os  # trailing comment makes this a code line

# a comment line

class Thing:
    """Class doc."""

    def method(self) -> str:
        """Method doc.

        Second paragraph.
        """
        text = """a
        string"""  # not a docstring
        return text


async def go() -> None:
    """Async doc."""
'''

SAMPLE_KINDS = [
    "docstring",
    "docstring",
    "blank",
    "code",
    "blank",
    "comment",
    "blank",
    "code",
    "docstring",
    "blank",
    "code",
    "docstring",
    "docstring",
    "docstring",
    "docstring",
    "code",
    "code",
    "code",
    "blank",
    "blank",
    "code",
    "docstring",
]


# --- line classification ----------------------------------------------------------------


def test_every_line_gets_exactly_one_category() -> None:
    analysis = cd.analyse_python(SAMPLE)
    assert analysis.kinds == SAMPLE_KINDS
    counts = analysis.counts()
    assert (counts.code, counts.blank, counts.comment, counts.docstring) == (7, 6, 1, 8)
    assert counts.code + counts.blank + counts.comment + counts.docstring == len(SAMPLE_KINDS)


def test_a_blank_line_inside_a_docstring_is_docstring() -> None:
    assert cd.analyse_python('def f() -> None:\n    """a\n\n    b"""\n').kinds[2] == cd.DOCSTRING


def test_a_multiline_string_that_is_not_a_docstring_is_code() -> None:
    kinds = cd.analyse_python('x = """one\n\n# two\n"""\n').kinds
    assert kinds == [cd.CODE] * 4


def test_a_file_with_no_code_has_no_ratio() -> None:
    counts = cd.analyse_python('"""Only a docstring."""\n').counts()
    assert counts.code == 0
    assert counts.prose == 1
    assert counts.ratio is None


def test_an_empty_file_has_no_lines() -> None:
    assert cd.analyse_python("").counts() == cd.Counts()


def test_the_ratio_is_prose_over_code() -> None:
    counts = cd.analyse_python("# a\n# b\nx = 1\n").counts()
    assert counts.ratio == 2.0


def test_typescript_scanner_classifies_comments_strings_and_templates() -> None:
    source = "\n".join(
        [
            "// line comment",
            "const a = 1; // trailing",
            "/* block",
            "   still block */",
            "",
            'const url = "http://example.com";',
            "const t = `first",
            "",
            "// not a comment ${x}`;",
            "/** doc */ const b = 2;",
        ]
    )
    assert cd.classify_typescript(source) == [
        cd.COMMENT,
        cd.CODE,
        cd.COMMENT,
        cd.COMMENT,
        cd.BLANK,
        cd.CODE,
        cd.CODE,
        cd.CODE,
        cd.CODE,
        cd.CODE,
    ]


def test_typescript_unterminated_quote_ends_at_the_line() -> None:
    assert cd.classify_typescript("<p>don't</p>\n// real comment\n") == [cd.CODE, cd.COMMENT]


# --- the report -------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_report_separates_frontend_tests_and_skips_the_generated_schema(tmp_path: Path) -> None:
    _write(tmp_path / "frontend/src/a.ts", "const a = 1;\n")
    _write(tmp_path / "frontend/src/a.test.ts", "const t = 1;\n")
    _write(tmp_path / "frontend/src/test/setup.ts", "const s = 1;\n")
    _write(tmp_path / "frontend/src/api/schema.ts", "// generated\n")
    groups = dict(cd.report_groups(tmp_path))
    assert groups["frontend/src (source)"] == ["frontend/src/a.ts"]
    assert groups["frontend/src (tests)"] == [
        "frontend/src/a.test.ts",
        "frontend/src/test/setup.ts",
    ]


def test_report_lists_files_with_posix_paths_and_skips_pycache(tmp_path: Path) -> None:
    _write(tmp_path / "backend/src/pkg/mod.py", "x = 1\n")
    _write(tmp_path / "backend/src/pkg/__pycache__/junk.py", "x = 1\n")
    assert cd.python_files(tmp_path, "backend/src") == ["backend/src/pkg/mod.py"]


def test_report_prints_counts_and_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "backend/src/pkg/mod.py", "# c\nx = 1\n")
    assert cd.main([], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "backend/src/pkg/mod.py" in out
    assert "TOTAL" in out
    assert "frontend/src (tests)" in out


def test_summary_omits_per_file_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path / "backend/src/pkg/mod.py", "x = 1\n")
    assert cd.main(["--summary"], root=tmp_path) == 0
    assert "mod.py" not in capsys.readouterr().out


def test_report_runs_over_the_real_repository(capsys: pytest.CaptureFixture[str]) -> None:
    assert cd.main(["--summary"]) == 0
    out = capsys.readouterr().out
    assert "backend/src" in out
    assert "frontend/src (source)" in out


# --- the baseline -----------------------------------------------------------------------


def test_update_baseline_is_deterministic_and_enumerates_the_filesystem(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "backend/src/pkg/b.py", "x = 1\n")
    _write(tmp_path / "backend/src/pkg/a.py", "# c\ny = 2\n")
    _write(tmp_path / "backend/src/pkg/__pycache__/junk.py", "z = 3\n")
    _write(tmp_path / "shared/src/s.py", '"""d"""\n')
    baseline_path = tmp_path / "baseline.json"

    assert cd.main(["--update-baseline"], root=tmp_path, baseline_path=baseline_path) == 0
    first = baseline_path.read_bytes()
    assert cd.main(["--update-baseline"], root=tmp_path, baseline_path=baseline_path) == 0
    assert baseline_path.read_bytes() == first

    text = first.decode("utf-8")
    assert text.endswith("}\n")
    assert b"\r" not in first
    lines = text.splitlines()
    assert len(lines) == 3 + 2
    keys = list(json.loads(text))
    assert keys == ["backend/src/pkg/a.py", "backend/src/pkg/b.py", "shared/src/s.py"]
    assert json.loads(text)["backend/src/pkg/a.py"] == {"code": 1, "prose": 1, "ratio": 1.0}
    assert json.loads(text)["shared/src/s.py"] == {"code": 0, "prose": 1, "ratio": None}


def test_an_empty_tree_renders_an_empty_object() -> None:
    assert cd.render_baseline({}) == "{}\n"


def test_the_committed_baseline_is_in_the_generated_format() -> None:
    text = cd.BASELINE_PATH.read_bytes().decode("utf-8")
    assert cd.render_baseline(json.loads(text)) == text


def test_the_committed_baseline_only_names_ratchet_scope_files() -> None:
    baseline = json.loads(cd.BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline
    assert all(any(rel.startswith(f"{tree}/") for tree in cd.RATCHET_TREES) for rel in baseline)


# --- the ratchet ------------------------------------------------------------------------


def _counts(code: int, prose: int) -> cd.Counts:
    return cd.Counts(code=code, comment=prose)


def test_a_prose_rise_above_the_allowance_fails() -> None:
    baseline = cd.BaselineEntry(code=100, prose=30)
    assert cd.allowance(baseline, 100) == 50
    assert not cd.exceeds_ratchet(_counts(100, 50), baseline)
    assert cd.exceeds_ratchet(_counts(100, 51), baseline)


def test_deleting_code_does_not_fail_a_file_whose_prose_is_unchanged() -> None:
    baseline = cd.BaselineEntry(code=100, prose=140)
    assert not cd.exceeds_ratchet(_counts(50, 140), baseline)
    assert not cd.exceeds_ratchet(_counts(50, 100), baseline)


def test_adding_prose_to_a_heavy_file_fails_even_after_code_shrinks() -> None:
    baseline = cd.BaselineEntry(code=100, prose=140)
    assert cd.exceeds_ratchet(_counts(50, 141), baseline)


def test_a_heavy_file_may_keep_its_ratio_as_code_grows() -> None:
    baseline = cd.BaselineEntry(code=100, prose=140)
    assert not cd.exceeds_ratchet(_counts(200, 280), baseline)
    assert cd.exceeds_ratchet(_counts(200, 281), baseline)


def test_a_lean_file_may_grow_to_the_floor_ratio() -> None:
    baseline = cd.BaselineEntry(code=100, prose=10)
    assert not cd.exceeds_ratchet(_counts(100, 50), baseline)
    assert cd.exceeds_ratchet(_counts(100, 51), baseline)


def test_a_new_file_above_the_floor_ratio_fails() -> None:
    assert not cd.exceeds_ratchet(_counts(40, 20), None)
    assert cd.exceeds_ratchet(_counts(40, 21), None)


def test_a_small_new_file_is_held_to_the_minimum_line_allowance() -> None:
    assert not cd.exceeds_ratchet(_counts(4, 10), None)
    assert cd.exceeds_ratchet(_counts(4, 11), None)


def test_a_file_with_no_code_does_not_divide_by_zero() -> None:
    baseline = cd.BaselineEntry(code=0, prose=5)
    assert not cd.exceeds_ratchet(_counts(0, 5), baseline)
    assert not cd.exceeds_ratchet(_counts(0, 10), baseline)
    assert cd.exceeds_ratchet(_counts(0, 11), baseline)
    assert cd.exceeds_ratchet(_counts(0, 11), None)


# --- citation patterns ------------------------------------------------------------------


def _cited(text: str) -> list[str]:
    analysis = cd.analyse_python(f"# {text}\n")
    return [cited for _, cited, _ in cd.find_citations(analysis, {1})]


@pytest.mark.parametrize(
    "text",
    [
        "see #123",
        "issue 45",
        "Issue #45 fixed here",
        "PR #7 changed this",
        "pull request 8",
        "https://github.com/o/r/issues/9",
        "moved in /pull/10",
    ],
)
def test_issue_citations_are_found(text: str) -> None:
    assert _cited(text) == [cd.ISSUE_CITATION]


@pytest.mark.parametrize(
    "text",
    [
        "round-3 review asked for this",
        "round 2 findings",
        "Round3 feedback",
        "after the review round",
        "a review finding",
    ],
)
def test_review_round_citations_are_found(text: str) -> None:
    assert _cited(text) == [cd.REVIEW_CITATION]


@pytest.mark.parametrize(
    "text",
    [
        "FR-12 applies here",
        "NFR-3 sets the limit",
        "see ADR-0013",
        "ADR 13 explains it",
        "C#7 syntax",
        "the &#123; entity",
        "colour #fff",
        "an issue arises when empty",
        "the review is short",
    ],
)
def test_stable_identifiers_and_lookalikes_pass(text: str) -> None:
    assert _cited(text) == []


def test_an_issue_that_is_also_a_review_round_reports_both() -> None:
    assert _cited("issue 5 round-2 review") == [cd.ISSUE_CITATION, cd.REVIEW_CITATION]


def test_a_string_literal_is_not_scanned() -> None:
    source = 'MESSAGE = "see issue #42 and round-3 review"\nOTHER = """\n#7\n"""\n'
    analysis = cd.analyse_python(source)
    assert cd.find_citations(analysis, {1, 2, 3, 4}) == []


def test_a_trailing_comment_on_a_code_line_is_scanned() -> None:
    analysis = cd.analyse_python("x = 1  # per issue 12\n")
    assert cd.find_citations(analysis, {1}) == [(1, cd.ISSUE_CITATION, "comment")]


@pytest.mark.parametrize(
    "source",
    [
        '"""Module for issue #5."""\n',
        'class A:\n    """Per PR #5."""\n',
        'def f() -> None:\n    """Line one.\n\n    Then round-2 review."""\n',
        'async def f() -> None:\n    """Per issue 5."""\n',
    ],
)
def test_module_class_and_function_docstrings_are_scanned(source: str) -> None:
    analysis = cd.analyse_python(source)
    hits = cd.find_citations(analysis, set(range(1, len(analysis.lines) + 1)))
    assert hits
    assert {where for _, _, where in hits} == {"docstring"}


def test_a_citation_on_a_line_the_diff_did_not_add_is_ignored() -> None:
    analysis = cd.analyse_python("# per issue 12\nx = 1\n# another line\n")
    assert cd.find_citations(analysis, {3}) == []
    assert cd.find_citations(analysis, {1}) != []


def test_added_rows_past_the_end_of_the_file_are_ignored() -> None:
    assert cd.find_citations(cd.analyse_python("x = 1\n"), {0, 9}) == []


def test_failures_name_the_file_the_line_and_what_to_do() -> None:
    analysis = cd.analyse_python("x = 1\n# per issue 12\n")
    [message] = cd.citation_failures("pkg/mod.py", analysis, {2})
    assert message.startswith("pkg/mod.py:2:")
    assert "commit message or PR body" in message
    assert "FR-nn" in message

    analysis = cd.analyse_python("# round-2 review\n")
    [message] = cd.citation_failures("pkg/mod.py", analysis, {1})
    assert "ADR" in message
    assert "code" in message


# --- diff parsing -----------------------------------------------------------------------

DIFF = """diff --git a/pkg/mod.py b/pkg/mod.py
index 111..222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -3,0 +4,2 @@ def f():
+# one
+# two
@@ -10 +12 @@ def g():
-old
+new
@@ -20,2 +21,0 @@
-gone
-gone
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,3 @@
+a = 1
++++ b/pkg/fake.py
+c = 3
diff --git a/pkg/removed.py b/pkg/removed.py
deleted file mode 100644
--- a/pkg/removed.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/pkg/old_name.py b/pkg/new_name.py
similarity index 100%
rename from pkg/old_name.py
rename to pkg/new_name.py
"""


def test_added_lines_are_read_from_hunk_headers() -> None:
    added = cd.parse_added_lines(DIFF)
    assert added["pkg/mod.py"] == {4, 5, 12}
    assert added["pkg/new.py"] == {1, 2, 3}


def test_a_content_line_that_looks_like_a_file_header_is_not_one() -> None:
    assert "pkg/fake.py" not in cd.parse_added_lines(DIFF)


def test_deleted_and_pure_renamed_files_add_nothing() -> None:
    added = cd.parse_added_lines(DIFF)
    assert "pkg/removed.py" not in added
    assert not added.get("pkg/new_name.py")


# --- end to end against a throwaway repository ------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _stage(repo: Path) -> None:
    _git(repo, "add", "-A")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    return path


def _check(repo: Path, *files: str) -> tuple[int, str]:
    baseline = repo.parent / "baseline.json"
    if not baseline.exists():
        baseline.write_text("{}\n", encoding="utf-8", newline="\n")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = cd.main(["--check", *files], root=repo, baseline_path=baseline)
    return code, err.getvalue()


def _record_baseline(repo: Path) -> None:
    cd.main(["--update-baseline"], root=repo, baseline_path=repo.parent / "baseline.json")


MOD = "backend/src/pkg/mod.py"


def test_the_hook_fails_when_prose_rises_past_the_allowance(repo: Path) -> None:
    code_lines = "".join(f"x{i} = {i}\n" for i in range(30))
    _write(repo / MOD, code_lines)
    _commit(repo, "base")
    _record_baseline(repo)

    _write(repo / MOD, code_lines + "".join(f"# note {i}\n" for i in range(40)))
    _stage(repo)
    status, err = _check(repo, MOD)

    assert status == 1
    assert f"{MOD}:31:" in err
    assert "--update-baseline" in err
    assert f"{MOD}:32: added prose: # note 1" in err


def test_the_hook_lists_at_most_a_capped_number_of_added_prose_lines(repo: Path) -> None:
    _write(repo / MOD, "x = 1\n")
    _commit(repo, "base")
    _record_baseline(repo)

    _write(repo / MOD, "x = 1\n" + "".join(f"# note {i}\n" for i in range(50)))
    _stage(repo)
    _, err = _check(repo, MOD)

    assert err.count("added prose:") == cd.MAX_LISTED_LINES
    assert "and 30 more added prose lines" in err


def test_the_hook_passes_when_only_code_is_removed(repo: Path) -> None:
    code_lines = "".join(f"x{i} = {i}\n" for i in range(60))
    notes = "".join(f"# note {i}\n" for i in range(45))
    _write(repo / MOD, notes + code_lines)
    _commit(repo, "base")
    _record_baseline(repo)

    _write(repo / MOD, notes + "".join(f"x{i} = {i}\n" for i in range(30)))
    _stage(repo)
    assert _check(repo, MOD) == (0, "")


def test_the_hook_fails_a_new_file_above_the_floor_ratio(repo: Path) -> None:
    _write(repo / "README.txt", "seed\n")
    _commit(repo, "base")
    _record_baseline(repo)

    _write(
        repo / "shared/src/new.py",
        "".join(f"x{i} = {i}\n" for i in range(20)) + "".join(f"# n{i}\n" for i in range(15)),
    )
    _stage(repo)
    status, err = _check(repo, "shared/src/new.py")
    assert status == 1
    assert "no baseline" in err


def test_the_hook_does_not_ratchet_files_outside_the_source_trees(repo: Path) -> None:
    _write(repo / "README.txt", "seed\n")
    _commit(repo, "base")
    _write(repo / "scripts/tool.py", "x = 1\n" + "".join(f"# n{i}\n" for i in range(40)))
    _stage(repo)
    assert _check(repo, "scripts/tool.py") == (0, "")


def test_the_hook_fails_a_new_citation_and_names_the_line(repo: Path) -> None:
    _write(repo / "backend/tests/test_x.py", "def test_a() -> None:\n    pass\n")
    _commit(repo, "base")

    _write(
        repo / "backend/tests/test_x.py",
        "def test_a() -> None:\n    # fixes issue 12\n    pass\n",
    )
    _stage(repo)
    status, err = _check(repo, "backend/tests/test_x.py")

    assert status == 1
    assert err.startswith("backend/tests/test_x.py:2:")
    assert "commit message or PR body" in err


def test_the_hook_passes_an_existing_citation_the_diff_did_not_touch(repo: Path) -> None:
    _write(repo / "backend/tests/test_x.py", "# per issue 12\nx = 1\ny = 2\n")
    _commit(repo, "base")

    _write(repo / "backend/tests/test_x.py", "# per issue 12\nx = 1\ny = 3\n")
    _stage(repo)
    assert _check(repo, "backend/tests/test_x.py") == (0, "")


def test_the_hook_passes_a_renamed_file_that_keeps_its_citations(repo: Path) -> None:
    body = "# per issue 12\n" + "".join(f"x{i} = {i}\n" for i in range(20))
    _write(repo / "backend/tests/test_old.py", body)
    _commit(repo, "base")

    _git(repo, "mv", "backend/tests/test_old.py", "backend/tests/test_new.py")
    _write(repo / "backend/tests/test_new.py", body + "z = 1\n")
    _stage(repo)
    assert _check(repo, "backend/tests/test_new.py") == (0, "")


def test_the_hook_flags_a_citation_added_while_renaming(repo: Path) -> None:
    body = "".join(f"x{i} = {i}\n" for i in range(20))
    _write(repo / "backend/tests/test_old.py", body)
    _commit(repo, "base")

    _git(repo, "mv", "backend/tests/test_old.py", "backend/tests/test_new.py")
    _write(repo / "backend/tests/test_new.py", body + "# per issue 12\n")
    _stage(repo)
    status, err = _check(repo, "backend/tests/test_new.py")
    assert status == 1
    assert "test_new.py:21:" in err


def test_the_hook_diffs_against_the_merge_base_with_origin_main(repo: Path) -> None:
    _write(repo / "backend/tests/test_x.py", "x = 1\n")
    _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo / "backend/tests/test_x.py", "x = 1\n# per issue 12\n")
    _commit(repo, "already committed on the branch")

    status, err = _check(repo, "backend/tests/test_x.py")
    assert status == 1
    assert "test_x.py:2:" in err


def test_the_hook_ignores_string_literals_and_stable_identifiers(repo: Path) -> None:
    _write(repo / "README.txt", "seed\n")
    _commit(repo, "base")
    _write(
        repo / "backend/tests/test_x.py",
        'NOTE = "see issue #42 and round-3 review"\n'
        "# FR-07 and NFR-08 apply; see ADR-0013\n"
        '"""Cites FR-07."""\n',
    )
    _stage(repo)
    assert _check(repo, "backend/tests/test_x.py") == (0, "")


def test_the_hook_reports_a_file_it_cannot_parse(repo: Path) -> None:
    _write(repo / "README.txt", "seed\n")
    _commit(repo, "base")
    _write(repo / "scripts/broken.py", "def (:\n")
    _stage(repo)
    status, err = _check(repo, "scripts/broken.py")
    assert status == 1
    assert err.startswith("scripts/broken.py:1: cannot parse")


def test_the_hook_ignores_non_python_and_missing_files(tmp_path: Path) -> None:
    assert cd.run_check(tmp_path, tmp_path / "baseline.json", ["README.md", "gone.py"]) == []


def test_the_hook_reports_a_missing_baseline(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo / "scripts/a.py", "x = 1\n")
    status = cd.main(
        ["--check", "scripts/a.py"], root=repo, baseline_path=repo.parent / "absent.json"
    )
    assert status == 1
    assert "comment_density:" in capsys.readouterr().err


def test_the_hook_reports_a_git_failure(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo / "scripts/a.py", "x = 1\n")
    status = cd.main(["--check", "scripts/a.py"], root=repo, baseline_path=_empty_baseline(repo))
    assert status == 1
    assert "git diff failed" in capsys.readouterr().err


def _empty_baseline(repo: Path) -> Path:
    path = repo.parent / "empty.json"
    path.write_text("{}\n", encoding="utf-8", newline="\n")
    return path


def test_absolute_paths_are_made_repo_relative(tmp_path: Path) -> None:
    assert cd.repo_relative(tmp_path, str(tmp_path / "a" / "b.py")) == "a/b.py"
    assert cd.repo_relative(tmp_path, "a/b.py") == "a/b.py"


# --- the migration template and the hook wiring ------------------------------------------


def test_the_migration_template_prompts_for_a_requirement_not_an_issue() -> None:
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "Issue #" not in body
    assert "FR-nn or NFR-nn" in body
    assert "<FILL IN" in body


def test_a_migration_generated_from_the_template_passes_the_citation_check() -> None:
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    generated = re.sub(r"\$\{[^}]*\}", "x", body)
    analysis = cd.analyse_python(generated)
    assert cd.find_citations(analysis, set(range(1, len(analysis.lines) + 1))) == []


def _hooks() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        hook["id"]: hook
        for entry in config["repos"]
        if entry["repo"] == "local"
        for hook in entry["hooks"]
    }


def test_the_hook_is_wired_as_one_serial_python_hook() -> None:
    hook = _hooks()["comment-density"]
    assert hook["entry"] == "uv run python scripts/comment_density.py --check"
    assert hook["language"] == "system"
    assert hook["types"] == ["python"]
    assert hook["require_serial"] is True
    assert "pass_filenames" not in hook
