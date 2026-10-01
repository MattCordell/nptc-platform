"""The `integration` marker is derived from fixture use by the hook in
backend/tests/conftest.py, so a new test that requests `db` is classed as
integration without its author writing the marker.

Run as a real pytest session in a subprocess over a throwaway tree. A fake
item list would need a hand-built fixture closure, which would prove neither
that `db` reaches `postgres_container` nor that the hook runs before pytest's
own `-m` deselection. The stub fixtures mirror the real dependency chain and
start nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REAL_CONFTEST = Path(__file__).with_name("conftest.py")

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
