"""`nptc.catalogue.length_report` - the FR-87 length distribution report
(issue #152).

Uses an ORM `Session` bound to `app_db`, matching `test_catalogue_
designations.py`'s own precedent - this module needs real rows to aggregate
over, so it is an integration test throughout.

Whole-table aggregate, so this follows CLAUDE.md's shared-container
convention option (b): every assertion is a *relative delta* against a
baseline this test itself establishes, scoped to entries this test itself
created, never an absolute histogram shape - the session-scoped Postgres
container is shared with every other test in the run.
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import event, literal
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import create_entry
from nptc.catalogue.length_report import (
    LengthDistribution,
    build_length_histogram_statement,
    compute_length_distribution,
)
from nptc.catalogue.term_hygiene import preferred_term_length
from nptc.db.models.catalogue_entry import CatalogueEntry


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _new_entry(session: Session, preferred_term: str) -> CatalogueEntry:
    return create_entry(
        session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for FR-87 length report test",
    )


def _baseline(session: Session) -> Counter[int]:
    """This test's own starting histogram, as `{length: count}` - every
    assertion below is a delta against this, never an absolute count
    (CLAUDE.md's shared-container convention)."""
    distribution = compute_length_distribution(session)
    return Counter({bucket.length: bucket.count for bucket in distribution.buckets})


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_the_histogram_counts_entries_by_preferred_term_length(app_session: Session) -> None:
    before = _baseline(app_session)
    _new_entry(app_session, "Iron")  # length 4
    _new_entry(app_session, "Zinc")  # a second, distinct entry, same length
    _new_entry(app_session, "Full blood count")  # length 16
    app_session.flush()

    after = {
        bucket.length: bucket.count for bucket in compute_length_distribution(app_session).buckets
    }

    assert after.get(4, 0) - before.get(4, 0) == 2
    assert after.get(16, 0) - before.get(16, 0) == 1


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_the_maximum_is_the_longest_preferred_term_this_test_created(app_session: Session) -> None:
    longest = "Adenosine deaminase, cerebrospinal fluid, quantitative"
    _new_entry(app_session, longest)
    app_session.flush()

    distribution = compute_length_distribution(app_session)

    assert distribution.maximum is not None
    assert distribution.maximum >= len(longest)


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_affected_counts_are_the_number_of_entries_strictly_exceeding_each_length(
    app_session: Session,
) -> None:
    """FR-86 warns when an entry's length *exceeds* the configured maximum
    (`catalogue_designations._length_warning`'s own `<=` short-circuit) - so
    a candidate threshold set to a given length must count entries longer
    than it, not entries at or past it."""
    short = "Iron"
    long_a = "Full blood count"
    long_b = "Full blood count, automated"
    before = _baseline(app_session)
    _new_entry(app_session, short)
    _new_entry(app_session, long_a)
    _new_entry(app_session, long_b)
    app_session.flush()

    distribution = compute_length_distribution(app_session)

    # Entries this test itself created, strictly longer than `short`'s own
    # length: long_a and long_b, so the delta against baseline is exactly 2.
    baseline_longer_than_short = sum(
        count for length, count in before.items() if length > len(short)
    )
    assert distribution.affected_counts[len(short)] - baseline_longer_than_short == 2
    # Nothing this test created is longer than the longest entry it made.
    baseline_longer_than_long_b = sum(
        count for length, count in before.items() if length > len(long_b)
    )
    assert distribution.affected_counts.get(len(long_b), 0) - baseline_longer_than_long_b == 0


@pytest.mark.req("FR-87")
def test_an_empty_histogram_reports_no_maximum() -> None:
    """Not reachable against the shared container (it is never actually
    empty across a full run), so this is a pure unit test of the dataclass
    `compute_length_distribution` returns for zero buckets - the same
    empty-input branch that function takes whenever `build_length_
    histogram_statement` returns no rows."""
    distribution = LengthDistribution(buckets=(), maximum=None, affected_counts={})

    assert distribution.maximum is None
    assert distribution.affected_counts == {}


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_build_length_histogram_statement_matches_nothing_against_an_impossible_filter(
    app_session: Session,
) -> None:
    """Proves the statement itself is a normal, composable `Select` - a
    caller can narrow it (here, to nothing at all) the same way any other
    `Select` in this codebase can be."""
    statement = build_length_histogram_statement().where(literal(False))

    assert app_session.execute(statement).all() == []


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_char_length_matches_preferred_term_length(app_session: Session) -> None:
    """The drift-equivalence the module docstring promises: `char_length` on
    the stored, already-cleaned `preferred_term` must agree with
    `preferred_term_length()` on that same value, since both are meant to be
    the one published figure (FR-85)."""
    term = "Full blood count"
    entry = _new_entry(app_session, term)
    app_session.flush()
    app_session.refresh(entry)

    distribution = compute_length_distribution(app_session)
    bucket = next(b for b in distribution.buckets if b.length == preferred_term_length(term))

    assert bucket.length == len(entry.preferred_term)
    assert bucket.length == preferred_term_length(entry.preferred_term)


@pytest.mark.req("FR-87")
@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_char_length_matches_preferred_term_length_for_a_non_ascii_term(
    app_session: Session,
) -> None:
    """Issue #152 review: the equivalence above is only load-bearing for a
    *non-ASCII* term - `char_length` and Python `len` cannot disagree over
    plain ASCII, so `test_char_length_matches_preferred_term_length` alone
    never actually exercises the divergence the module docstring is
    guarding against (NFC composing a decomposed combining sequence, and
    `char_length` counting characters rather than UTF-16 code units).

    Crafts a term carrying both: a decomposed combining acute accent
    (`"e" + U+0301`, which `normalise_for_comparison` composes to a single
    `"é"`) and a trailing non-breaking space (PRD Appendix A.1's own
    `clean_term` case) that gets stripped. Counted as a *delta* against this
    test's own baseline at the resulting length, not an absolute bucket
    lookup - the shared container may already hold other entries whose
    cleaned length happens to coincide (CLAUDE.md's shared-container
    convention).
    """
    nbsp = chr(0x00A0)
    combining_acute = "́"
    raw_term = f"Adenosine deaminase, cafe{combining_acute} quantitative{nbsp}"
    before = _baseline(app_session)

    entry = _new_entry(app_session, raw_term)
    app_session.flush()
    app_session.refresh(entry)

    # The combining sequence must actually have composed and the NBSP must
    # actually have been stripped - otherwise this test silently degrades
    # into the ASCII-equivalent case it exists to improve on.
    assert "e" + combining_acute not in entry.preferred_term
    assert nbsp not in entry.preferred_term
    cleaned_length = len(entry.preferred_term)

    distribution = compute_length_distribution(app_session)
    after_count = next(
        (bucket.count for bucket in distribution.buckets if bucket.length == cleaned_length), 0
    )

    assert after_count - before.get(cleaned_length, 0) == 1
    assert cleaned_length == preferred_term_length(entry.preferred_term)


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_the_report_issues_exactly_one_statement(app_db: Connection, app_session: Session) -> None:
    """FR-87's own acceptance criterion: the report runs in one statement,
    not one per candidate threshold - asserted at the statement level,
    matching `test_api_public_search.py`'s own #275 regression test."""
    statements: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(app_db, "before_cursor_execute", _record)
    try:
        compute_length_distribution(app_session)
    finally:
        event.remove(app_db, "before_cursor_execute", _record)

    # `SAVEPOINT` statements come from the `app_session` fixture's own
    # nested-transaction join, not from the report - filtered out so this
    # asserts what FR-87 actually cares about: one aggregate `SELECT`.
    select_statements = [s for s in statements if "char_length" in s]
    assert len(select_statements) == 1, (
        f"expected exactly one aggregate statement, got {len(select_statements)}:\n"
        + "\n---\n".join(select_statements)
    )
