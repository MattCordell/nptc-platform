"""Invariants on .pre-commit-config.yaml (issue #339).

The regression this file exists to prevent: a *second* pinned ruff. The config
used to carry an `astral-sh/ruff-pre-commit` mirror at `rev: v0.16.1` while
`uv.lock` resolved 0.16.8, so `pre-commit run --all-files` autofixed with one
ruff and CI then checked the result with another. Nothing updates a `rev:` line
- Dependabot has no `pre-commit` ecosystem - so the drift only widened, and it
surfaced as a CI failure after a push rather than at commit time.

Running ruff as a local `uv run` hook makes that impossible rather than merely
detectable: there is one resolved ruff, the lockfile's. These tests hold that
shape in place against a well-meant "tidy" back to the upstream mirror.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / ".pre-commit-config.yaml"

RUFF_MIRROR = "astral-sh/ruff-pre-commit"


def _config() -> dict[str, Any]:
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _local_hook_ids() -> set[str]:
    return {
        hook["id"]
        for repo in _config()["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }


def test_no_second_pinned_ruff() -> None:
    """The principal failure mode: re-adding the mirror reintroduces the drift."""
    mirrors = [repo["repo"] for repo in _config()["repos"] if RUFF_MIRROR in repo["repo"]]
    assert mirrors == [], (
        f"{RUFF_MIRROR} is pinned again in .pre-commit-config.yaml. That is a second "
        "ruff version alongside the one uv.lock resolves, and the two drift apart "
        "silently (issue #339). Run ruff as a local `uv run` hook instead."
    )


def test_ruff_runs_as_a_local_uv_hook() -> None:
    """A rename must not silently drop lint or formatting from pre-commit."""
    assert {"ruff", "ruff-format"} <= _local_hook_ids()


def test_the_local_ruff_hooks_invoke_uv() -> None:
    """`uv run` is what ties the hook to uv.lock's resolved ruff - a bare `ruff`
    entry would pick up whatever happens to be on the contributor's PATH."""
    entries = {
        hook["id"]: hook["entry"]
        for repo in _config()["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] in {"ruff", "ruff-format"}
    }
    assert entries == {
        "ruff": "uv run ruff check --fix",
        "ruff-format": "uv run ruff format",
    }
