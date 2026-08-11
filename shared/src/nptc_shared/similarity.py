"""Bounded edit-distance tokenising and near-match primitives (FR-79, H-04).

Lives in ``shared``, not ``transform``, because FR-79's misspelling
detection has two call sites that must never independently drift (FR-74,
ADR-0003): the seeding transform (this PR) and, once FR-36 lands, the same
check on save in the application. A new module rather than an addition to
``nptc_shared.text`` - that module's own docstring scopes it to Unicode
hygiene (NFC normalisation, invisible-character detection), not to fuzzy
comparison. ``tokenise`` and ``near_match_distance`` build on
``text.normalise_for_comparison`` rather than reimplementing it.

**Suspect vs. reference eligibility, and where the line sits.** FR-79's
heuristic needs two related but distinct gates: every token that can be
compared at all (too short, or carrying a digit, is never worth comparing -
an abbreviation like ``ADA``/``AFP``/``CSF`` is 2-4 characters and a code
like ``ADA2``/``5HIAA`` is definitionally not a spelling question), and a
*stricter* gate for a token that could be the misspelled one - an
all-uppercase surface form (``ALPHAFETOPROTEIN``) is always a fine
*reference* to compare against, but must never itself be flagged as a
*suspect*, since an initialism/acronym rendered in caps is not "probably a
typo" the way a mixed-case word is. This module owns only the first, looser
gate (``is_comparable_token``: length and digit content, applied to both
suspects and references); the second, case-based restriction is
misspelling-specific policy, not a text-shape primitive, and lives in
``nptc_transform.misspelling`` instead.
"""

from __future__ import annotations

import re

from nptc_shared.text import normalise_for_comparison

#: Every RCPA Appendix A.5 abbreviation (ADA, AFP, CSF, Ab, RBC) is 2-4
#: characters - below this length a token is not a comparable word at all,
#: only ever noise for this heuristic.
MIN_TOKEN_LENGTH = 5

#: FR-79's own words, "one or two characters", as a hard ceiling on what
#: ``near_match_distance`` will ever admit.
MAX_EDIT_DISTANCE = 2

#: Distance 2 is only admissible between tokens at least this long - below
#: it, two edits is too large a fraction of the word to be a confident
#: misspelling signal rather than two genuinely different short words.
LONG_TOKEN_LENGTH = 8

_TOKEN_PATTERN = re.compile(r"[^\W_]+")


def tokenise(text: str) -> tuple[str, ...]:
    """Splits ``text`` into its word/number runs, delimiter-independent.

    Built on ``normalise_for_comparison`` (NFC, every non-ASCII space
    collapsed to an ordinary one, edge-stripped) so an interior non-breaking
    space is a separator exactly like an ordinary one. ``[^\\W_]+`` matches
    runs of letters and digits, excluding underscore - so a comma, a
    semicolon, a hyphen and a bare space are all equally non-word
    separators. This is what makes ``'ADA RBC, ADA red cells'``, the same
    text with semicolons in place of commas, and the same text with bare
    spaces in place of every delimiter, tokenise identically - sidestepping
    FR-71's own unresolved comma-vs-semicolon delimiter question for the
    ``RCPA Synonyms`` column (PRD Appendix A.4) rather than having to answer
    it.
    """
    return tuple(_TOKEN_PATTERN.findall(normalise_for_comparison(text)))


def token_key(token: str) -> str:
    """A casefolded comparison key for ``token`` - never for display.

    Every message quoting a token must quote the original surface form
    (``escape_invisible``-wrapped); this exists only so two tokens differing
    solely in case are recognised as the same word for counting, authority-set
    membership and edit-distance comparison.
    """
    return token.casefold()


def is_comparable_token(token: str) -> bool:
    """The length+digit gate every comparable token must pass.

    Shared by suspect- and reference-eligibility (see the module docstring):
    a token shorter than ``MIN_TOKEN_LENGTH``, or containing any digit, is
    never worth comparing at all, in either role. The stricter,
    suspect-only "not all-uppercase" restriction is layered on top of this
    by ``nptc_transform.misspelling``, not here.
    """
    return len(token) >= MIN_TOKEN_LENGTH and not any(ch.isdigit() for ch in token)


def bounded_edit_distance(a: str, b: str, *, max_distance: int) -> int | None:
    """Levenshtein distance between ``a`` and ``b``, or ``None`` past ``max_distance``.

    Deliberately plain Levenshtein, not Damerau-Levenshtein: an adjacent
    transposition costs two edits here (a substitution each way, or a
    delete-then-insert), never one - see ``test_similarity.py`` for this as
    a documented design fact, not an oversight.

    A length prefilter is checked first (``None`` immediately if the length
    difference alone exceeds ``max_distance`` - no edit sequence can close a
    length gap wider than the budget). The DP itself is restricted to a
    band of width ``2 * max_distance + 1`` around the main diagonal - a cell
    outside the band can only be reached by an edit count already past
    ``max_distance``, so it is never computed, only assigned a sentinel
    value larger than the budget. The running minimum of each completed row
    is checked before starting the next; once it exceeds ``max_distance``,
    every path through that row already does too, so the function returns
    ``None`` without finishing the remaining rows.
    """
    if abs(len(a) - len(b)) > max_distance:
        return None
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    sentinel = max_distance + 1
    previous = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        current = [sentinel] * (len_b + 1)
        current[0] = i
        lo = max(1, i - max_distance)
        hi = min(len_b, i + max_distance)
        for j in range(lo, hi + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(
                previous[j] + 1,  # deletion from a
                current[j - 1] + 1,  # insertion into a
                previous[j - 1] + cost,  # substitution
            )
        if min(current) > max_distance:
            return None
        previous = current
    result = previous[len_b]
    return result if result <= max_distance else None


def near_match_distance(a: str, b: str, *, max_distance: int = MAX_EDIT_DISTANCE) -> int | None:
    """The admissible edit distance between ``a`` and ``b``, or ``None``.

    Distance 1 is always admissible. Distance 2 is admissible only when the
    *shorter* of the two tokens has length at least ``LONG_TOKEN_LENGTH`` -
    below that, two edits is too large a fraction of a short word to be a
    confident misspelling signal (``urine``/``urate``, both length 5, must
    be refused even though they are exactly distance 2 apart).
    """
    distance = bounded_edit_distance(a, b, max_distance=1)
    if distance is not None:
        return distance
    if min(len(a), len(b)) < LONG_TOKEN_LENGTH:
        return None
    return bounded_edit_distance(a, b, max_distance=max_distance)
