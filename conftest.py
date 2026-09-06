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
"""

from __future__ import annotations

import pytest

#: Enables the `pytester` fixture (`scripts/tests/test_req_marker_selection.py`
#: exercises this file's own hooks by running a real, isolated pytest
#: subprocess against synthetic tests) - only loadable from a rootdir
#: conftest.py, which is exactly where this file lives.
pytest_plugins = ["pytester"]


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
