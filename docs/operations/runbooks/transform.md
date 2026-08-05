# Transform CLI

`nptc-transform` converts the published SPIA Requesting workbook into either a
defect report or an import dataset (PRD §12). This runbook covers the `run`
command that lands with backlog issue [P0-1](https://github.com/aehrc/nptc-platform/issues/23):
the entrypoint, the report-only guarantee (FR-70) and the determinism/idempotency
contract (FR-73). It does not yet read the workbook or classify anything - see
"Not implemented yet" below.

## Usage

```powershell
uv run nptc-transform run --workbook path/to/SPIA-Requesting.xlsx
```

| Flag | Default | Meaning |
|---|---|---|
| `--workbook` | *(required)* | Path to the source `.xlsx`. Must exist and be readable. |
| `--report-dir` | `transform-report` | Directory the report files are written into. Created if missing. |
| `--emit-dataset` | off | Opt into the mutating mode. **Not implemented yet** - see below. |

Running with no flags at all prints help and exits 0; `--workbook` is required
to actually run.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ran to completion. In report-only mode, this does not mean the workbook is clean - check `finding_count` in `report.json`. |
| `1` | Reserved for blocking findings once band classification (P0-3) lands. Unreachable today. |
| `2` | Usage error: the workbook doesn't exist or isn't readable, or `--emit-dataset` was passed. |

## The report-only guarantee (FR-70)

Report-only is the default and, until P0-9 lands, the *only* mode that writes
anything. A `run` invocation without `--emit-dataset` writes exactly two files
into `--report-dir` - `report.json` and `report.md` - and touches nothing else
on disk. `--emit-dataset` is reserved: passing it writes nothing at all,
prints an explanation to stderr, and exits `2`. This is deliberate, not a
missing feature - see "Not implemented yet".

## The determinism and idempotency contract (FR-73)

Running the transform twice against the same workbook, or re-running it into
the same `--report-dir`, produces byte-identical `report.json` and
`report.md` files. This holds because:

- **No clock-derived value appears in either report file.** The run start
  time, its duration, and the tool version banner are written to stderr only
  - never into `report.json` or `report.md`. If you need to know when a
  report was produced, check the file's modified time or the stderr log from
  the run that produced it.
- **No absolute path appears in either report file.** The report identifies
  the source workbook by filename and SHA-256 hash, not by the path it was
  read from.
- Every collection in the report is explicitly sorted before being written -
  finding order is never dict- or set-iteration order, and does not depend on
  `PYTHONHASHSEED`.
- Both files are written as UTF-8 with `\n` line endings, regardless of
  platform.
- Both files are overwritten in place on every run - never appended to, never
  numbered (`report-2.json`). Re-running into a report directory that already
  holds a report from a previous run replaces it exactly; it does not
  accumulate findings or skip the write.

## Not implemented yet

This issue delivers the entrypoint and the writing discipline, not the
transform. The following are owned by later P0 issues (see
[`docs/backlog/p0.yaml`](../../backlog/p0.yaml) for the current issue numbers)
and will change what `report.json`/`report.md` contain, but not the
guarantees above:

- Reading the workbook and detecting invisible/non-printing characters - P0-2
- Three-band defect classification - P0-3
- Terminology client and batch validation - P0-4, P0-5
- Designation reconciliation - P0-6
- Misspelling and semantic-drift heuristics - P0-7
- Report content grouped by defect class with cell references - P0-8
- **`--emit-dataset`: import dataset emission and the synthetic baseline release** - P0-9
- SCTID/Verhoeff library - P0-10
