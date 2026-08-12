"""Golden fixture test (issue #31, P0-9): the committed real 50-row excerpt,
through the full pipeline.

No ``pytest.skip`` if the fixture is missing - a skipping test is a test that
silently does not run. If ``spia-requesting-sample.xlsx`` is absent, every
test below fails loudly with ``FileNotFoundError`` instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nptc_transform.dataset import build_dataset
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.workbook import read_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "spia-requesting-sample.xlsx"

#: Recorded once, from a real ``--report-only`` run against the fixture, per
#: the plan's Step 0: "the first thing I do after the file lands is run
#: --report-only against it and record the real band counts". All findings
#: are auto-correctable or informational - no blocking finding - so
#: --emit-dataset succeeds end to end.
EXPECTED_ENTRY_COUNT = 50
EXPECTED_AUTO_CORRECTABLE = 154
EXPECTED_REQUIRES_HUMAN_DECISION = 0
EXPECTED_DATA_DEFECT = 0
EXPECTED_INFORMATIONAL = 13


@pytest.mark.req("FR-70")
@pytest.mark.req("FR-76")
def test_the_real_excerpt_has_no_blocking_finding() -> None:
    result = run_transform(FIXTURE, mode=Mode.EMIT_DATASET)

    counts = result.band_counts
    assert not result.has_blocking_findings, result.findings
    assert counts["auto-correctable"] == EXPECTED_AUTO_CORRECTABLE
    assert counts["requires-human-decision"] == EXPECTED_REQUIRES_HUMAN_DECISION
    assert counts["data-defect"] == EXPECTED_DATA_DEFECT
    assert counts["informational"] == EXPECTED_INFORMATIONAL


@pytest.mark.req("FR-70")
@pytest.mark.req("FR-76")
def test_the_real_excerpt_emits_every_entry_with_every_code_a_string() -> None:
    result = run_transform(FIXTURE, mode=Mode.EMIT_DATASET)
    sheets = read_workbook(FIXTURE)

    dataset = build_dataset(sheets, result, release_name="2026-06")

    assert len(dataset.entries) == EXPECTED_ENTRY_COUNT
    for entry in dataset.entries:
        for binding in entry.code_bindings:
            assert isinstance(binding.code, str)
            assert binding.code.isdigit()


@pytest.mark.req("FR-03")
def test_the_real_excerpts_business_keys_are_sequential_and_gap_free() -> None:
    result = run_transform(FIXTURE, mode=Mode.EMIT_DATASET)
    sheets = read_workbook(FIXTURE)

    dataset = build_dataset(sheets, result, release_name="2026-06")

    assert [entry.business_key for entry in dataset.entries] == [
        f"NPTC-{n:06d}" for n in range(1, EXPECTED_ENTRY_COUNT + 1)
    ]
