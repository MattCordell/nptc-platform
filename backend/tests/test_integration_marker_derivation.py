"""The `integration` marker is derived from fixture use by the hook in
backend/tests/conftest.py, so a new test that requests `db` is classed as
integration without its author writing the marker.

Two kinds of check, because each proves a different thing:

- A real pytest session in a subprocess over a throwaway tree proves the hook
  and its place before pytest's own `-m` deselection. Its stub fixtures copy
  the real dependency chain by hand and start nothing, so they cannot notice
  the real chain changing.
- A collection of the real `backend/tests` tree proves the real `db`,
  `app_db` and `app_engine` still reach `postgres_container`. It checks the
  shared fixtures named in `SHARED_DB_FIXTURES`; a container fixture added
  outside `CONTAINER_FIXTURES` is not found by either check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REAL_CONFTEST = Path(__file__).with_name("conftest.py")
REPO_ROOT = Path(__file__).resolve().parents[2]

SHARED_DB_FIXTURES = (
    "postgres_container",
    "owner_engine",
    "migrated",
    "app_engine",
    "app_db",
    "db",
)

_PYTEST_INI = """\
[pytest]
addopts = --strict-markers --import-mode=importlib -p no:cacheprovider
markers =
    integration: derived from fixture use
"""

_CONFTEST = f"""\
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location("real_backend_conftest", {str(REAL_CONFTEST)!r})
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)
pytest_collection_modifyitems = _real.pytest_collection_modifyitems


@pytest.fixture(scope="session")
def postgres_container():
    return None


@pytest.fixture(scope="session")
def owner_engine(postgres_container):
    return None


@pytest.fixture(scope="session")
def migrated(owner_engine):
    return None


@pytest.fixture(scope="session")
def app_engine(postgres_container, migrated):
    return None


@pytest.fixture
def db(owner_engine, migrated):
    return None


@pytest.fixture
def app_db(app_engine):
    return None
"""

_TESTS = """\
def test_requests_postgres_container(postgres_container):
    pass


def test_requests_db(db):
    pass


def test_requests_app_db(app_db):
    pass


def test_requests_nothing():
    pass
"""


@pytest.fixture(scope="module")
def sample_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("marker_derivation")
    (root / "pytest.ini").write_text(_PYTEST_INI, encoding="utf-8")
    (root / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (root / "test_sample.py").write_text(_TESTS, encoding="utf-8")
    return root


def _selected(tree: Path, markexpr: str) -> set[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", markexpr],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.split("::", 1)[1] for line in result.stdout.splitlines() if "::" in line}


def test_a_test_requesting_the_container_or_a_fixture_over_it_is_integration(
    sample_tree: Path,
) -> None:
    assert _selected(sample_tree, "integration") == {
        "test_requests_postgres_container",
        "test_requests_db",
        "test_requests_app_db",
    }


def test_a_test_requesting_no_container_fixture_is_not_integration(sample_tree: Path) -> None:
    assert _selected(sample_tree, "not integration") == {"test_requests_nothing"}


# trylast: runs after the hook under test, so it sees the markers it added.
_REPORT_PLUGIN = """import json
import os

import pytest

SHARED = {shared!r}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    report = [
        {{
            "nodeid": item.nodeid,
            "requests": sorted(set(SHARED).intersection(getattr(item, "fixturenames", ()))),
            "integration": item.get_closest_marker("integration") is not None,
        }}
        for item in items
    ]
    with open(os.environ["MARKER_REPORT"], "w", encoding="utf-8") as handle:
        json.dump(report, handle)
"""


def test_every_real_backend_test_reaching_a_shared_db_fixture_is_integration(
    tmp_path: Path,
) -> None:
    (tmp_path / "marker_report_plugin.py").write_text(
        _REPORT_PLUGIN.format(shared=SHARED_DB_FIXTURES), encoding="utf-8"
    )
    report_path = tmp_path / "report.json"
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    env["MARKER_REPORT"] = str(report_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "backend/tests",
            "--collect-only",
            "-q",
            "-p",
            "marker_report_plugin",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    report = json.loads(report_path.read_text(encoding="utf-8"))
    reaching = [entry for entry in report if entry["requests"]]
    assert reaching, "no collected backend test requests a shared DB fixture"
    assert {"db", "app_db"} <= {name for entry in reaching for name in entry["requests"]}
    unmarked = [entry["nodeid"] for entry in reaching if not entry["integration"]]
    assert unmarked == []
