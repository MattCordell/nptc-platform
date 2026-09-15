"""FR-87: the preferred-term length distribution report (issue #152).

RCPA-QAP cannot nominate a maximum preferred-term length (FR-86, the setting
`ApiSettings.max_preferred_term_length` gates) until they can see how many
entries each candidate value would affect - this is PRD open item OI-1's
remaining half. This module answers that with one statement, not one per
candidate threshold, following `nptc.catalogue.facets`'s own precedent
(issue #275, ADR-0032) for why a per-candidate loop does not belong here:
the catalogue is scanned once regardless of how many thresholds a caller
might consider.

**One grouping dimension, so no `UNION ALL`/rank machinery is needed.**
`facets.build_facet_counts_statement` combines several *different* grouping
expressions (one per facetable property) into one statement. This report has
exactly one: `char_length(preferred_term)`. A plain
`SELECT char_length(preferred_term), count(*) ... GROUP BY 1` is the whole
query; the maximum and the per-length "how many entries exceed this" figure
FR-87 asks for are both derived from that histogram alone, in Python, as a
suffix sum - no second statement.

**`char_length` on the stored column, not a second `preferred_term_length`
implementation.** `nptc.catalogue.term_hygiene.preferred_term_length`'s own
docstring makes "the one function every caller must use" the rule this
report would otherwise break by recomputing a length in SQL. It does not:
`preferred_term` is stored already cleaned (`CatalogueEntry`'s
`@validates("preferred_term")` hook runs `clean_term` before every write), so
`char_length` on the stored value and `preferred_term_length()` on that same
value necessarily agree -
`test_length_report_char_length_matches_preferred_term_length` in
`backend/tests/test_catalogue_length_report.py` asserts that equivalence
explicitly rather than assuming it silently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Select, func
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from nptc.db.models.catalogue_entry import CatalogueEntry

__all__ = [
    "LengthBucket",
    "LengthDistribution",
    "build_length_histogram_statement",
    "compute_length_distribution",
]


@dataclass(frozen=True, slots=True)
class LengthBucket:
    """Every entry whose preferred term is exactly `length` characters long."""

    length: int
    count: int


@dataclass(frozen=True, slots=True)
class LengthDistribution:
    """The whole report: the histogram, its maximum, and - for every length
    that actually occurs - how many entries a maximum set to that length
    would affect (FR-86 warns an entry whose length *exceeds* the configured
    maximum, so this counts strictly greater, matching
    `catalogue_designations._length_warning`'s own comparison).

    `maximum` is `None` only for an empty catalogue - there is no longest
    term to report.
    """

    buckets: tuple[LengthBucket, ...]
    maximum: int | None
    affected_counts: Mapping[int, int]


def build_length_histogram_statement() -> Select[tuple[int, int]]:
    """The one statement: every distinct preferred-term length, and how many
    entries have it. Public and separate from `compute_length_distribution`,
    matching `facets.build_facet_count_statement`'s own precedent - a test
    can inspect this statement directly rather than the whole report.

    Labelled `bucket_count`, not `count`: `facets.build_facet_count_statement`
    already documents why - a SQLAlchemy `Row` inherits `tuple.count`, so a
    column literally named `count` is reachable only positionally and
    `row.count` silently yields the bound method instead of the value.
    """
    length = func.char_length(CatalogueEntry.preferred_term).label("length")
    bucket_count = func.count().label("bucket_count")
    return sa_select(length, bucket_count).group_by(length).order_by(length)


def compute_length_distribution(session: Session) -> LengthDistribution:
    """Runs `build_length_histogram_statement` once, then derives the
    maximum and every length's "entries exceeding it" count as a suffix sum
    over the histogram already in hand - no second query.

    The catalogue's design ceiling (20,000 entries, PRD's planning figure)
    bounds this to one aggregate scan of `catalogue_entry`; the number of
    *distinct* lengths a histogram this size can ever produce is smaller
    still, so the suffix sum below is cheap regardless.
    """
    buckets = tuple(
        LengthBucket(length=row.length, count=row.bucket_count)
        for row in session.execute(build_length_histogram_statement()).all()
    )
    if not buckets:
        return LengthDistribution(buckets=(), maximum=None, affected_counts={})
    affected_counts: dict[int, int] = {}
    running = 0
    for bucket in reversed(buckets):
        affected_counts[bucket.length] = running
        running += bucket.count
    return LengthDistribution(
        buckets=buckets, maximum=buckets[-1].length, affected_counts=affected_counts
    )
