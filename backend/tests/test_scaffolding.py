"""Proves the workspace, the import path and the test runner are wired up.

Delete or replace once real backend tests land (starting with P1-1).
"""

import nptc


def test_package_imports_and_reports_a_version() -> None:
    assert nptc.__version__ == "0.0.0"
