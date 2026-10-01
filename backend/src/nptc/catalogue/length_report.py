"""FR-87: the preferred-term length distribution report.

RCPA-QAP cannot nominate a maximum preferred-term length (FR-86,
`ApiSettings.max_preferred_term_length`, ADR-0041) until they can see how many
entries each candidate value would affect (PRD open item OI-1). One statement
answers for every candidate threshold, so the catalogue is scanned once
whatever the number of thresholds, as in `nptc.catalogue.facets` (ADR-0032).

**One grouping dimension, so no `UNION ALL`/rank machinery.** The whole query
is `SELECT char_length(preferred_term), count(*) ... GROUP BY 1`. The maximum
and the per-length "how many entries exceed this" figure are both derived from
that histogram in Python, with the `exceeds_maximum_length` predicate FR-86's
warning uses. Each bucket rescans the histogram, which is quadratic in the
number of distinct lengths (a few dozen); that is the price of sharing the
predicate.

**`char_length` on the stored column, not a second `preferred_term_length`.**
`nptc.catalogue.term_hygiene.preferred_term_length` is the one function that
computes the published length. The two agree only while every row reached the
table through the ORM, whose `@validates` hook runs `clean_term`; a Core
`insert()`, `COPY` or data migration skips it. Every loader must therefore
write through `nptc.catalogue.entries.create_entry`.
`test_char_length_matches_preferred_term_length` in
`backend/tests/test_catalogue_length_report.py` pins the agreement for
ORM-written rows, and
`test_a_row_that_skips_the_orm_is_where_char_length_and_the_published_length_disagree`
pins the gap for the rest.

**Every status is counted, deliberately.** The histogram query has no status
filter, so a `draft`, `deprecated` or `withdrawn` entry's preferred term is
counted as an `active` one's is. FR-86's warning can fire for an entry in any
status, because the amendment route resolves the entry without a status
filter: a draft has to be editable before it can become `active`. Counting
`active` entries alone would undercount what a chosen maximum affects.
`docs/user/reading-the-length-distribution-report.md` states this for readers
of the report.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Select, func
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from nptc.catalogue.term_hygiene import exceeds_maximum_length
from nptc.db.models.catalogue_entry import CatalogueEntry

__all__ = [
    "LengthBucket",
    "LengthDistribution",
    "build_length_histogram_statement",
    "compute_length_distribution",
    "distribution_from_buckets",
]


@dataclass(frozen=True, slots=True)
class LengthBucket:
    """Every entry whose preferred term is exactly `length` characters long.

    `entries_exceeding` counts strictly longer entries, because FR-86 warns
    only when a length *exceeds* the configured maximum."""

    length: int
    count: int
    entries_exceeding: int


@dataclass(frozen=True, slots=True)
class LengthDistribution:
    """The whole report: the histogram, ascending by length, and its maximum.

    `maximum` is `None` only for an empty catalogue - there is no longest
    term to report.
    """

    buckets: tuple[LengthBucket, ...]
    maximum: int | None


def build_length_histogram_statement() -> Select[int, int]:
    """The one statement: every distinct preferred-term length, and how many
    entries have it. Public so a test can inspect it directly.

    Labelled `bucket_count`, not `count`, for the reason
    `facets.build_facet_count_statement` documents: `Row` inherits
    `tuple.count`, so `row.count` yields the bound method, not the value.
    """
    length = func.char_length(CatalogueEntry.preferred_term).label("length")
    bucket_count = func.count().label("bucket_count")
    return sa_select(length, bucket_count).group_by(length)


def distribution_from_buckets(histogram: Iterable[tuple[int, int]]) -> LengthDistribution:
    """Builds the report from `(length, count)` pairs in any order, so the
    result never depends on row order and the query needs no `ORDER BY`.
    Pairs sharing a length are summed into one bucket."""
    totals: dict[int, int] = {}
    for length, count in histogram:
        totals[length] = totals.get(length, 0) + count
    buckets = tuple(
        LengthBucket(
            length=length,
            count=count,
            entries_exceeding=sum(
                other_count
                for other_length, other_count in totals.items()
                if exceeds_maximum_length(other_length, length)
            ),
        )
        for length, count in sorted(totals.items())
    )
    return LengthDistribution(
        buckets=buckets, maximum=max((bucket.length for bucket in buckets), default=None)
    )


def compute_length_distribution(session: Session) -> LengthDistribution:
    """Runs `build_length_histogram_statement` once and hands the rows to
    `distribution_from_buckets` - no second query."""
    return distribution_from_buckets(
        (row.length, row.bucket_count)
        for row in session.execute(build_length_histogram_statement()).all()
    )
