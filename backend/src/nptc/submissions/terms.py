"""Cleaning the terms of a submission, shared by the create route and the duplicate check (FR-25,
FR-63).

The duplicate check must compare the same text the create route would store, so both clean a term
the one way.
"""

from __future__ import annotations

from collections.abc import Sequence

from nptc.catalogue.term_hygiene import clean_term
from nptc_shared.similarity import collision_key

__all__ = ["distinct_synonyms"]


def distinct_synonyms(preferred_term: str, terms: Sequence[str]) -> list[str]:
    """Every term cleaned, then each dropped if its comparison key was already seen, the
    preferred term's included. Cleaning runs first, so a bad term is refused even when a duplicate.
    The first spelling is kept, in the order given."""
    seen = {collision_key(preferred_term)}
    distinct: list[str] = []
    for term in terms:
        cleaned = clean_term(term)
        key = collision_key(cleaned)
        if key not in seen:
            seen.add(key)
            distinct.append(cleaned)
    return distinct
