"""Unit tests for scripts/generate_openapi.py (issue #143, FR-20).

Exercises the check/write logic against a scratch `OPENAPI_PATH`. The document
content itself comes from the real `nptc.api.openapi_document.build_document` -
these tests are about the script's file handling, not the app's route table
(that's `backend/tests/test_openapi_document.py`'s job).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate_openapi as go


@pytest.fixture(autouse=True)
def _isolate_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(go, "OPENAPI_PATH", tmp_path / "openapi.json")


def test_check_fails_when_the_file_does_not_exist(capsys: pytest.CaptureFixture[str]) -> None:
    assert go.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert str(go.OPENAPI_PATH) in err
    assert go.REGENERATE_COMMAND in err


def test_check_passes_when_the_committed_document_matches() -> None:
    go.OPENAPI_PATH.write_text(go.rendered_document(), encoding="utf-8", newline="\n")
    assert go.main(["--check"]) == 0


def test_check_fails_and_names_the_file_when_the_document_is_stale(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The principal failure mode: a route or response model changed but nobody
    regenerated the committed document - the exact defect this gate exists to catch."""
    go.OPENAPI_PATH.write_text('{"openapi": "3.1.0", "stale": true}\n', encoding="utf-8")

    assert go.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert str(go.OPENAPI_PATH) in err
    assert go.REGENERATE_COMMAND in err


def test_write_mode_produces_the_rendered_document() -> None:
    assert go.main([]) == 0
    assert go.OPENAPI_PATH.read_text(encoding="utf-8") == go.rendered_document()


def test_write_mode_is_idempotent() -> None:
    go.main([])
    first = go.OPENAPI_PATH.read_bytes()
    go.main([])
    assert go.OPENAPI_PATH.read_bytes() == first


def test_rendered_document_ends_in_exactly_one_lf_newline() -> None:
    text = go.rendered_document()
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text
