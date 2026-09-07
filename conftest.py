"""Root-level pytest hook: `--req=NFR-08` selects only tests carrying
`@pytest.mark.req("NFR-08")` with that exact ID.

CLAUDE.md documents `uv run pytest -m "req('FR-07')"` for this, but
pytest's own `-m` markexpr grammar has no call syntax - only bare
identifiers and `and`/`or`/`not`/parens (see `_pytest.mark.expression`) - so
that command is a `SyntaxError: Wrong expression passed to '-m': ...
expected identifier; got string literal`, not a working selection. `-m
"req"` alone (every `req`-marked test, regardless of which ID) is the only
form the built-in grammar accepts; picking one specific ID needs a real CLI
option, which is what this hook adds.

Lives at the repo root, not under one of the four `tests/` trees
(`backend`, `transform`, `shared`, `scripts`): a single pytest session
collects across all four via `testpaths`, and `pytest_addoption` errors if
more than one collected `conftest.py` registers the same flag - the repo
root is the one place loaded exactly once regardless of which of the four
trees is actually being run.

`--req`'s logic (`pytest_configure`/`pytest_collection_modifyitems` below)
is unit-tested directly in `scripts/tests/test_req_marker_selection.py`
against lightweight fakes, not via pytest's own `pytester` plugin - that
would need `pytest_plugins = ["pytester"]` here (the one location pytest
allows it: a non-rootdir conftest.py raises an error for the same
declaration), which loads `pytester` into every session in the repo for
one file's benefit. Direct unit tests avoid that global surface entirely.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


def _known_requirement_ids() -> frozenset[str]:
    """Every requirement ID in `docs/requirements/requirements.yaml`, via
    `scripts/traceability_check.py`'s own loader - not a second YAML
    parser that could drift from what that file already enforces."""
    scripts_dir_str = str(_SCRIPTS_DIR)
    if scripts_dir_str not in sys.path:
        sys.path.insert(0, scripts_dir_str)
    import traceability_check as tc

    requirements, _errors = tc.load_requirements()
    return frozenset(requirements)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--req",
        action="store",
        default=None,
        metavar="ID",
        help=(
            "Select only tests marked @pytest.mark.req(ID) with this exact "
            "requirement ID, e.g. --req=NFR-08. A test tagged with more "
            "than one req() (rare) matches if any of its IDs is this one."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Fails loudly for a typo'd/unknown ID rather than silently
    deselecting every test - without this, `--req=NFR-99` reports
    'collected 0 items' (or 'no tests ran' further down the pipeline),
    which reads as "did I break something" rather than "that ID doesn't
    exist"."""
    req_id = config.getoption("--req")
    if not req_id:
        return
    known = _known_requirement_ids()
    if req_id not in known:
        raise pytest.UsageError(
            f"--req: {req_id!r} is not a requirement ID in docs/requirements/requirements.yaml"
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    req_id = config.getoption("--req")
    if not req_id:
        return

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        marker_ids = {mark.args[0] for mark in item.iter_markers(name="req") if mark.args}
        (selected if req_id in marker_ids else deselected).append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
