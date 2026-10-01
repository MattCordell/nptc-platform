# Code size: re-measurement after the prose sweep

This note compares prose density and function length with the milestone-one review
(`MILESTONE-1-REVIEW.md`, finding 16; the file is kept outside version control). It also
records which long functions to split. Read it before you open a "this function is too
long" issue, so you do not repeat the work. The re-measurement is issue #366, under
epic #349.

## Short answer

Prose fell by a third and function length fell sharply. Two functions still need splitting.

- **Prose:** 9,832 prose lines against 17,344 code lines, a 0.57 : 1 ratio and 36% of
  non-blank lines. The review reported 0.89 : 1 and 47%. Measured with today's script, the
  baseline commit gives 0.84 : 1 and 46%.
- **Functions over 100 lines:** 11 at the baseline, 6 now, counting `backend/src` only, as
  the review did.
- **Functions over 60 lines:** 45 at the baseline, 33 now, on the same scope.
- **Decision:** split 2 of the 9 functions that are over 100 lines in any production tree.
  Leave the other 7, with a reason for each.

## How the counts were taken

| | |
|---|---|
| Date | 2026-10-02 |
| Commit measured | `19b53d5` (`main`) |
| Baseline commit | `5d15523`, the commit the review measured |

**Function length.** Every `def` counts, methods and nested functions included. Length is
`end_lineno - lineno + 1` from Python's `ast` module, so it includes the docstring, comments
and blank lines. The "code" column drops blank lines, comment-only lines and the docstring.
This method reproduces the review's figures exactly at `5d15523`: 491 functions, 45 over 60
lines and 11 over 100 lines, in `backend/src`.

**Prose density.** `scripts/comment_density.py --summary`. To compare like with like, the
baseline run used today's script copied into a checkout of `5d15523`.

```powershell
uv run python scripts/comment_density.py --summary
git worktree add --detach ../nptc-baseline 5d15523
Copy-Item scripts/comment_density.py ../nptc-baseline/scripts/comment_density.py
uv run python ../nptc-baseline/scripts/comment_density.py --summary
git worktree remove --force ../nptc-baseline
```

The function counts need a short script that walks each file with `ast.walk` and prints
`end_lineno - lineno + 1` per `def`. It is not kept in the repository, because no check
depends on it.

## Prose density

Production Python is `backend/src`, `transform/src` and `shared/src`.

| | Review (as reported) | `5d15523`, today's script | `19b53d5`, today's script |
|---|---:|---:|---:|
| Code lines | 16,298 | 17,337 | 17,344 |
| Comment lines | not reported | 3,666 | 2,123 |
| Docstring lines | not reported | 10,858 | 7,709 |
| Prose lines (comment + docstring) | 14,568 | 14,524 | 9,832 |
| Prose : code | 0.89 : 1 | 0.84 : 1 | 0.57 : 1 |
| Prose share of non-blank lines | 47% | 46% | 36% |

Prose lines agree closely between the review and today's script (14,568 and 14,524). The
code-line totals differ by about 6%, so the review counted code differently. Use the middle
column when you judge progress, and the first column only to see what the review reported.

Prose fell by 4,692 lines, or 32%. Code moved by 7 lines, so the drop is prose and not
deleted code.

Supplementary figures, not part of the review's headline: the frontend source tree holds
6,593 code lines and 3,436 comment lines (0.52 : 1), identical at both commits. The script
approximates these figures with a line scan that does not parse regex literals or JSX text.

## Function length

| Scope | Functions | Over 60 lines | Over 100 lines |
|---|---:|---:|---:|
| `backend/src` at `5d15523` (the review) | 491 | 45 | 11 |
| `backend/src` at `19b53d5` | 455 | 33 | 6 |
| `shared/src` at `19b53d5` (supplementary) | 127 | 4 | 0 |
| `transform/src` at `19b53d5` (supplementary) | 135 | 8 | 3 |
| All three trees at `19b53d5` | 717 | 45 | 9 |

The review measured `backend/src` only, so the first two rows are the like-for-like
comparison. The three over-100 functions in `transform/` also existed at the baseline
(201, 173 and 106 lines) and fall in the decisions below.

### What happened to the review's eleven

| Function | Review | Now | Code lines now |
|---|---:|---:|---:|
| `backend/src/nptc/api/errors.py::register_exception_handlers` | 725 | 151 | 114 |
| `backend/src/nptc/catalogue/property_values.py::save_property_values_for_entries` | 233 | 190 | 120 |
| `backend/src/nptc/catalogue/property_values.py::save_property_values` | 168 | 137 | 87 |
| `backend/src/nptc/catalogue/collisions.py::acknowledge_collision` | 143 | 104 | 56 |
| `backend/src/nptc/catalogue/entries.py::save_entry` | 137 | 115 | 65 |
| `backend/src/nptc/db/property_reconciler.py::_reconcile_locked` | 129 | 100 | 77 |
| `backend/src/nptc/catalogue/history.py::load_history` | 116 | 109 | 84 |
| `backend/src/nptc/catalogue/bindings.py::create_binding` | 112 | 93 | 72 |
| `backend/src/nptc/catalogue/entries.py::entry_child_write` | 102 | 76 | 30 |
| `backend/src/nptc/api/app.py::create_app` | 102 | 73 | 32 |
| `backend/src/nptc/catalogue/collisions.py::assert_no_error_collisions` | 101 | 84 | 50 |

`_reconcile_locked` is exactly 100 lines, so it is not over the threshold. The last four
functions in the table are now clearly under it.

## The test for splitting

A function over 100 lines is split only if both are true:

1. It holds more than 100 lines of code, not counting docstring, comments and blank lines.
2. It has repeated blocks or nested branches that a helper would remove.

If either fails, the length comes from prose or from a flat sequence of steps. A split would
add names and parameter passing without making the logic easier to follow, so the decision
is "leave".

## Decisions

| Function | Span | Code | Decision | Reason |
|---|---:|---:|---|---|
| `backend/src/nptc/catalogue/property_values.py::save_property_values_for_entries` | 190 | 120 | **Split** (#410) | Builds `BulkPropertyOutcome` seven times, in two identical pairs, and nests a recovery path inside the loop. A per-target helper removes both. |
| `transform/src/nptc_transform/semantic_drift.py::check_semantic_drift` | 187 | 155 | **Split** (#411) | Five labelled phases share local counters in one scope. Each phase can stand alone. |
| `transform/src/nptc_transform/cli.py::run` | 165 | 128 | Leave | Passes test 1 but not test 2. About 50 lines are Typer option declarations. The rest maps each failure to an exit code, which reads better in one place. |
| `backend/src/nptc/api/errors.py::register_exception_handlers` | 151 | 114 | Leave | Passes test 1 but not test 2. The repetition left with the table collapse. What remains is a flat run of independent handlers with no shared control flow. |
| `backend/src/nptc/catalogue/property_values.py::save_property_values` | 137 | 87 | Leave | Under 100 lines of code. A straight sequence of guards. 50 of its 137 lines are docstring, comments and blank lines. |
| `backend/src/nptc/catalogue/entries.py::save_entry` | 115 | 65 | Leave | Under 100 lines of code. Linear, with one `try` block. 50 of its 115 lines are docstring, comments and blank lines. |
| `backend/src/nptc/catalogue/history.py::load_history` | 109 | 84 | Leave | Under 100 lines of code. Linear query building. |
| `backend/src/nptc/catalogue/collisions.py::acknowledge_collision` | 104 | 56 | Leave | Under 100 lines of code. 48 of its 104 lines are docstring, comments and blank lines. |
| `transform/src/nptc_transform/designation_check.py::check_designations` | 101 | 82 | Leave | Under 100 lines of code and one line over the threshold. It has two clear phases. |

Both splits are pure refactors. Each issue lists the constraints the refactor must keep:
lock-first audit ordering and the savepoint scope for `save_property_values_for_entries`
(ADR-0035), and the `2 + G` request count for `check_semantic_drift` (ADR-0008, Decision 6).

## When to measure again

Measure again if a review asks for it, or after a change that adds long functions. The
comment-density ratchet already guards prose. No check guards function length, and the
review did not ask for one.
