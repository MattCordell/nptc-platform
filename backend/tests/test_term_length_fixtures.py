"""FR-85, ADR-0030: the shared fixture the browser's term-length mirror is tested against.

`frontend/tests/term-length-mirror.test.ts` reads the same file. Its expected
lengths come from `preferred_term_length`, so this test is what keeps the
fixture honest and the mirror's test meaningful.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nptc.catalogue.term_hygiene import preferred_term_length

FIXTURE = Path(__file__).resolve().parents[2] / "shared/tests/fixtures/term-length-cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_ascii_only() -> None:
    # NFR-38 test 2: an invisible character never appears verbatim in a committed file.
    assert FIXTURE.read_text(encoding="utf-8").isascii()


@pytest.mark.req("FR-85")
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_length_matches_the_server_count(case: dict[str, object]) -> None:
    assert preferred_term_length(str(case["term"])) == case["length"]
