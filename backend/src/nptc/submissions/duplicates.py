"""Finding what a submission would duplicate (FR-25).

A match is an active catalogue entry or an open submission that shares the submission's SNOMED CT
code, or has a preferred term or synonym similar to one of its terms. The check is advisory: a
match asks the submitter to confirm, and never refuses outright.

**Trigram similarity only.** It reuses the catalogue search's `SIMILARITY_THRESHOLD`,
`nptc_search_text` and `%` operator, so "similar" means one thing across the platform and the
catalogue side is served by the existing GIN trigram indexes. It does not call the hybrid
`search_entries`, whose full-text branches match a common word such as "test" at a low score.

**Only what a submitter may already see.** The catalogue side is `PUBLIC_STATUSES`, so a draft or
deprecated entry never matches. A submission is open in every state except
`CLOSED_SUBMISSION_STATES`, and the filter excludes the closed states, so a state added later is
open until it is listed there. No match names a submitter (FR-42).

**Codes are compared as text (FR-06).** The check never calls the terminology server, so it works
during an outage; resolving the code stays the create route's job.

**Bounded.** At most `MAX_MATCHES_PER_SOURCE` matches come back for each source, best first, so a
broad term cannot return an unbounded list. An entry or submission that matches on several terms
appears once, on its best match. A code match counts as the best, ahead of an exact term match.

The SQL binds every value (NFR-22).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from nptc.catalogue.queries import PUBLIC_STATUSES
from nptc.catalogue.search import apply_similarity_threshold
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES
from nptc.submissions.terms import (
    SubmissionCodeMalformedError,
    TermField,
    clean_submission_term,
    distinct_synonyms,
)
from nptc_shared.sctid import SCTID, InvalidSCTIDError

__all__ = [
    "MAX_MATCHES_PER_SOURCE",
    "DuplicateMatch",
    "MatchSource",
    "MatchedOn",
    "check_duplicates",
    "find_duplicates",
]

MAX_MATCHES_PER_SOURCE: Final = 20

_SIMILARITY_DIGITS: Final = 3


class MatchSource(StrEnum):
    CATALOGUE_ENTRY = "catalogue_entry"
    SUBMISSION = "submission"


class MatchedOn(StrEnum):
    PREFERRED_TERM = "preferred_term"
    SYNONYM = "synonym"
    CODE = "code"


@dataclass(frozen=True)
class DuplicateMatch:
    """One entry or submission that the new submission may duplicate.

    `key` is the entry's business key or the submission's id. `preferred_term` is that record's own
    preferred term, and `term` is the text that matched, which is the same for a preferred-term or
    code match. `similarity` is `None` for a code match, where it has no meaning."""

    source: MatchSource
    matched_on: MatchedOn
    key: str
    preferred_term: str
    term: str
    similarity: float | None

    def as_record(self) -> dict[str, Any]:
        """What is stored on the submission: the identity of the match and why it matched. The
        text of another record is left out, so a stored record never outlives a change to it."""
        return {
            "source": self.source.value,
            "key": self.key,
            "matched_on": self.matched_on.value,
            "similarity": self.similarity,
        }


#: `q` holds the distinct terms. `matches` is one branch per source and field, so each trigram
#: branch is its own index scan on the catalogue side. A code match scores `NULL` and orders as 1.
#: The `d.status = 'active'` and `cb.status = 'active'` literals match the partial-index
#: predicates, as in `nptc.catalogue.search`.
_DUPLICATES_SQL = text("""
WITH q AS (
    SELECT DISTINCT t.term AS term FROM unnest(CAST(:terms AS text[])) AS t(term)
),
matches AS (
    SELECT
        'catalogue_entry' AS source,
        'preferred_term' AS matched_on,
        e.business_key AS match_key,
        e.preferred_term AS preferred_term,
        e.preferred_term AS term,
        similarity(nptc_search_text(e.preferred_term), nptc_search_text(q.term)) AS score
    FROM q
    JOIN catalogue_entry AS e
        ON nptc_search_text(e.preferred_term) % nptc_search_text(q.term)
    WHERE e.status = ANY(:statuses)
    UNION ALL
    SELECT
        'catalogue_entry',
        'synonym',
        e.business_key,
        e.preferred_term,
        d.term,
        similarity(nptc_search_text(d.term), nptc_search_text(q.term))
    FROM q
    JOIN designation AS d
        ON d.status = 'active'
       AND nptc_search_text(d.term) % nptc_search_text(q.term)
    JOIN catalogue_entry AS e ON e.id = d.entry_id
    WHERE e.status = ANY(:statuses)
    UNION ALL
    SELECT
        'catalogue_entry',
        'code',
        e.business_key,
        e.preferred_term,
        e.preferred_term,
        CAST(NULL AS real)
    FROM code_binding AS cb
    JOIN catalogue_entry AS e ON e.id = cb.entry_id
    WHERE cb.status = 'active'
      AND cb.code = :code
      AND e.status = ANY(:statuses)
    UNION ALL
    SELECT
        'submission',
        'preferred_term',
        CAST(s.id AS text),
        s.preferred_term,
        s.preferred_term,
        similarity(nptc_search_text(s.preferred_term), nptc_search_text(q.term))
    FROM q
    JOIN submission AS s
        ON nptc_search_text(s.preferred_term) % nptc_search_text(q.term)
    WHERE s.state <> ALL(:closed_states)
    UNION ALL
    SELECT
        'submission',
        'synonym',
        CAST(s.id AS text),
        s.preferred_term,
        syn.term,
        similarity(nptc_search_text(syn.term), nptc_search_text(q.term))
    FROM submission AS s
    CROSS JOIN LATERAL jsonb_array_elements_text(s.synonyms) AS syn(term)
    JOIN q ON nptc_search_text(syn.term) % nptc_search_text(q.term)
    WHERE s.state <> ALL(:closed_states)
    UNION ALL
    SELECT
        'submission',
        'code',
        CAST(s.id AS text),
        s.preferred_term,
        s.preferred_term,
        CAST(NULL AS real)
    FROM submission AS s
    WHERE s.snomed_code = :code
      AND s.state <> ALL(:closed_states)
),
scored AS (
    SELECT matches.*, COALESCE(matches.score, 1) AS order_score FROM matches
),
best AS (
    SELECT DISTINCT ON (source, match_key) *
    FROM scored
    ORDER BY source, match_key, order_score DESC, (matched_on = 'code') DESC, term
),
ranked AS (
    SELECT
        best.*,
        row_number() OVER (PARTITION BY source ORDER BY order_score DESC, match_key) AS position
    FROM best
)
SELECT source, matched_on, match_key, preferred_term, term, score
FROM ranked
WHERE position <= :per_source
ORDER BY source, order_score DESC, match_key
""")


def find_duplicates(
    session: Session,
    *,
    preferred_term: str,
    synonyms: Sequence[str],
    snomed_code: str | None,
) -> list[DuplicateMatch]:
    """The matches for a submission's cleaned terms and its validated code, catalogue entries
    first, each source best first. Reads only. The caller cleans the terms (see
    `nptc.submissions.terms`) and validates the code."""
    apply_similarity_threshold(session)
    rows = session.execute(
        _DUPLICATES_SQL,
        {
            "terms": [preferred_term, *synonyms],
            "code": snomed_code,
            "statuses": list(PUBLIC_STATUSES),
            "closed_states": list(CLOSED_SUBMISSION_STATES),
            "per_source": MAX_MATCHES_PER_SOURCE,
        },
    ).all()
    return [
        DuplicateMatch(
            source=MatchSource(row.source),
            matched_on=MatchedOn(row.matched_on),
            key=row.match_key,
            preferred_term=row.preferred_term,
            term=row.term,
            similarity=None if row.score is None else round(float(row.score), _SIMILARITY_DIGITS),
        )
        for row in rows
    ]


def check_duplicates(
    session: Session,
    *,
    preferred_term: str,
    synonyms: Sequence[str],
    snomed_code: str | None,
) -> list[DuplicateMatch]:
    """`find_duplicates` for terms exactly as a caller typed them: cleaned and de-duplicated the way
    the create route stores them, and a code checked for format and check digit only. Raises
    `nptc.catalogue.term_hygiene.TermCleaningError` for a term that cannot be cleaned and
    `nptc_shared.sctid.InvalidSCTIDError` for a malformed code. The terminology server is not
    asked."""
    cleaned = clean_submission_term(preferred_term, TermField.PREFERRED_TERM)
    return find_duplicates(
        session,
        preferred_term=cleaned,
        synonyms=distinct_synonyms(cleaned, synonyms),
        snomed_code=None if snomed_code is None else _checked_code(snomed_code),
    )


def _checked_code(code: str) -> str:
    try:
        return SCTID(code).value
    except InvalidSCTIDError as malformed:
        raise SubmissionCodeMalformedError(str(malformed)) from None
