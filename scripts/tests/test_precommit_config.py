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


def _ruff_hooks() -> dict[str, dict[str, Any]]:
    return {
        hook["id"]: hook
        for repo in _config()["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] in {"ruff", "ruff-format"}
    }


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
    entry would pick up whatever happens to be on the contributor's PATH.

    Asserted as a prefix, not an exact string: flags may legitimately be added to
    these entries (`--force-exclude` already is), and a test that forbids that
    pins today's spelling rather than the invariant it exists to hold.
    """
    for hook_id, subcommand in (("ruff", "check"), ("ruff-format", "format")):
        entry = _ruff_hooks()[hook_id]["entry"]
        assert entry.startswith(f"uv run ruff {subcommand} "), (
            f"the {hook_id} hook must run `uv run ruff {subcommand}`, so it resolves "
            f"the ruff uv.lock pins rather than one from PATH; got {entry!r}"
        )


def test_the_local_ruff_hooks_force_exclude() -> None:
    """pre-commit passes filenames explicitly, and ruff applies [tool.ruff]
    `exclude` to explicitly-passed paths only under `--force-exclude`. Without the
    flag, the first `exclude` added to pyproject.toml would have pre-commit lint
    and autofix files CI's `ruff check .` skips - #339's divergence again."""
    for hook_id, hook in _ruff_hooks().items():
        assert "--force-exclude" in hook["entry"], (
            f"the {hook_id} hook must pass --force-exclude, or a future "
            "[tool.ruff] exclude will apply in CI but not in pre-commit"
        )


def test_the_local_ruff_hooks_cover_what_ci_covers() -> None:
    """CI runs `ruff check .` / `ruff format --check .`, which recurse over every
    file type ruff handles. These hooks see only what their selectors name, so a
    narrower selector lets a file pass pre-commit and fail CI.

    `ruff format` formats Python code blocks in markdown, and CI's own run already
    covers every tracked .md file - so markdown is a live gap, not a hypothetical
    one. The sets below match upstream ruff-pre-commit's .pre-commit-hooks.yaml.
    """
    expected = {
        "ruff": ["python", "pyi", "jupyter"],
        "ruff-format": ["python", "pyi", "jupyter", "markdown"],
    }
    for hook_id, types_or in expected.items():
        hook = _ruff_hooks()[hook_id]
        assert "types" not in hook, (
            f"the {hook_id} hook uses `types:`, which is an AND across tags and "
            "cannot express 'python or pyi or jupyter' - use `types_or:`"
        )
        assert hook["types_or"] == types_or
