"""Cleaning the terms of a submission, shared by the create route and the duplicate check (FR-25,
FR-63).

The duplicate check must compare the same text the create route would store, so both clean a term
the one way.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from nptc.catalogue.term_hygiene import TermCleaningError, clean_term
from nptc_shared.similarity import collision_key

__all__ = [
    "SubmissionTermRefusedError",
    "TermField",
    "clean_submission_term",
    "distinct_synonyms",
]


class TermField(StrEnum):
    """The term fields of a submission. A fixed set, so the API can name which one was refused."""

    PREFERRED_TERM = "preferred_term"
    SYNONYMS = "synonyms"


class SubmissionTermRefusedError(TermCleaningError):
    """A term of a submission cannot be cleaned (FR-63). Carries the field it came from, so the
    response can name it. The message is the cleaning error's, which quotes no raw character."""

    def __init__(self, field: TermField, cause: TermCleaningError) -> None:
        self.field = field
        super().__init__(str(cause))


def clean_submission_term(term: str, field: TermField) -> str:
    """`clean_term`, refusing with the field the term came from."""
    try:
        return clean_term(term)
    except TermCleaningError as refused:
        raise SubmissionTermRefusedError(field, refused) from None


def distinct_synonyms(preferred_term: str, terms: Sequence[str]) -> list[str]:
    """Every term cleaned, then each dropped if its comparison key was already seen, the
    preferred term's included. Cleaning runs first, so a bad term is refused even when a duplicate.
    The first spelling is kept, in the order given."""
    seen = {collision_key(preferred_term)}
    distinct: list[str] = []
    for term in terms:
        cleaned = clean_submission_term(term, TermField.SYNONYMS)
        key = collision_key(cleaned)
        if key not in seen:
            seen.add(key)
            distinct.append(cleaned)
    return distinct
