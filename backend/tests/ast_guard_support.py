"""Shared scaffolding for this tree's pure-`ast` pytest guards (NFR-22's
`test_sql_parameterisation.py`, FR-77's `test_datatype_dispatch.py`).

Extracted so the two guards share **one** `SCAN_DIRS` constant rather than
two that happen to look alike (ADR-0013 SS5's explicit requirement) - a
future third directory added to one guard's scan is added here once, for
both, instead of risked drifting apart in two copies.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "backend" / "src", REPO_ROOT / "backend" / "migrations"]


def display_path(path: Path) -> str:
    """`path` relative to the repo root, POSIX-separated - stable across
    Windows and CI regardless of which OS ran the guard."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_source_files(scan_dirs: list[Path] = SCAN_DIRS) -> list[Path]:
    """Every `*.py` file under `scan_dirs`, sorted for deterministic
    ordering (matters for the exact-list assertions each guard makes)."""
    files: list[Path] = []
    for base in scan_dirs:
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files
