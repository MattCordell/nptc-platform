"""FR-86 and FR-87 share one boundary: the entries the route would warn about
at a maximum are exactly the entries the report counts in `entries_exceeding`
for that length.

Transient entries and pure functions - the agreement is a property of the
two call sites, so it needs no database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from nptc.api.routers.catalogue_designations import _length_warning
from nptc.catalogue.length_report import distribution_from_buckets
from nptc.db.models.catalogue_entry import CatalogueEntry


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hermetic_api_settings = _load("hermetic_settings_support").hermetic_api_settings


@pytest.mark.req("FR-86")
@pytest.mark.req("FR-87")
def test_the_route_warns_about_exactly_the_entries_the_report_counts_as_exceeding() -> None:
    entries = [CatalogueEntry(preferred_term="x" * length) for length in (9, 10, 10, 11, 14)]
    distribution = distribution_from_buckets([(entry.length, 1) for entry in entries])

    for bucket in distribution.buckets:
        settings = hermetic_api_settings(max_preferred_term_length=bucket.length)
        warned = [entry for entry in entries if _length_warning(entry, settings) is not None]
        assert len(warned) == bucket.entries_exceeding, f"disagreement at maximum {bucket.length}"

    at_ten = next(bucket for bucket in distribution.buckets if bucket.length == 10)
    assert at_ten.count == 2
    assert at_ten.entries_exceeding == 2


@pytest.mark.req("FR-86")
def test_no_maximum_never_reads_the_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """The warning check must not compute a length it has nothing to compare
    with."""
    entry = CatalogueEntry(preferred_term="x" * 500)
    monkeypatch.setattr(
        CatalogueEntry,
        "length",
        property(lambda _self: pytest.fail("length read with no maximum configured")),
    )

    assert _length_warning(entry, hermetic_api_settings()) is None
