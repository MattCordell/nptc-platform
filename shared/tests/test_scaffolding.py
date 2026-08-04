"""Proves the workspace, the import path and the test runner are wired up.

Delete or replace once P0-10 (the shared SCTID/Verhoeff library) lands with real
tests of its own.
"""

import nptc_shared


def test_package_imports_and_reports_a_version() -> None:
    assert nptc_shared.__version__ == "0.0.0"
