"""Shared fixtures for transform/tests."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest


@pytest.fixture(scope="session")
def sample_workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A minimal, valid .xlsx fixture - no real SPIA data, built in-process."""
    workbook_dir = tmp_path_factory.mktemp("workbook")
    path = workbook_dir / "sample.xlsx"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(["code", "designation"])
    sheet.append(["12345678", "Sample test"])
    workbook.save(path)

    return path
