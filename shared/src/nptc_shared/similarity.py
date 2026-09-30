"""Bounded edit-distance tokenising and near-match primitives (FR-79, H-04),
and the FR-05 collision comparison key.

Lives in ``shared``, not ``transform``, because FR-79's misspelling detection
has two call sites that must never drift apart (FR-74, ADR-0003): the seeding
transform and the on-save check (FR-36). It is a module of its own because
``nptc_shared.text`` is scoped to Unicode hygiene, not fuzzy comparison.

``collision_key`` is here, not in ``text.py``, because FR-05 needs a stronger
fold than ``text.normalise_for_comparison`` (case and punctuation as well as
whitespace) and it composes ``tokenise``. ``normalise_for_comparison`` keeps
case and punctuation on purpose: FR-82 treats a case difference as editorial
signal.

``is_comparable_token`` is the looser gate (length and digit content), applied
to suspects and references alike. The stricter suspect-only gate (an
all-uppercase token is never flagged, so an initialism is not read as a typo)
is misspelling policy and lives in ``nptc_transform.misspelling``.
"""

from __future__ import annotations

import re

from nptc_shared.text import normalise_for_comparison

#: Every RCPA Appendix A.5 abbreviation (ADA, AFP, CSF, Ab, RBC) is 2-4
#: characters, so a shorter token is only noise for this heuristic.
MIN_TOKEN_LENGTH = 5

#: FR-79's "one or two characters", as a hard ceiling on what
#: ``near_match_distance`` admits.
MAX_EDIT_DISTANCE = 2

#: Distance 2 is only admissible between tokens at least this long; below it,
#: two edits is too large a fraction of the word to signal a misspelling.
LONG_TOKEN_LENGTH = 8

_TOKEN_PATTERN = re.compile(r"[^\W_]+")


def tokenise(text: str) -> tuple[str, ...]:
    """Splits ``text`` into its word and number runs, whatever the delimiter.

    Built on ``normalise_for_comparison``, so a non-breaking space separates
    like an ordinary one. ``[^\\W_]+`` excludes underscore, so a comma,
    semicolon, hyphen and bare space are all separators. ``'ADA RBC, ADA red
    cells'`` therefore tokenises the same with semicolons or bare spaces,
    which sidesteps FR-71's unresolved comma-versus-semicolon question (PRD
    Appendix A.4).
    """
    return tuple(_TOKEN_PATTERN.findall(normalise_for_comparison(text)))


def token_key(token: str) -> str:
    """A casefolded comparison key for ``token``, never for display.

    Messages must quote the original surface form (``escape_invisible``-wrapped).
    """
    return token.casefold()


def is_comparable_token(token: str) -> bool:
    """The length-and-digit gate every comparable token must pass, as suspect or reference.

    The suspect-only "not all-uppercase" restriction is layered on top by
    ``nptc_transform.misspelling``.
    """
    return len(token) >= MIN_TOKEN_LENGTH and not any(ch.isdigit() for ch in token)


def bounded_edit_distance(a: str, b: str, *, max_distance: int) -> int | None:
    """Levenshtein distance between ``a`` and ``b``, or ``None`` past ``max_distance``.

    Deliberately plain Levenshtein, not Damerau-Levenshtein: an adjacent
    transposition costs two edits, never one (``test_similarity.py`` pins this).

    The DP is restricted to a band of width ``2 * max_distance + 1``. A cell
    outside it needs more than ``max_distance`` edits, so it keeps a sentinel
    above the budget. A row whose minimum exceeds the budget ends the search.
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
        row_min = i if i <= max_distance else sentinel
        lo = max(1, i - max_distance)
        hi = min(len_b, i + max_distance)
        for j in range(lo, hi + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            value = min(
                previous[j] + 1,  # deletion from a
                current[j - 1] + 1,  # insertion into a
                previous[j - 1] + cost,  # substitution
            )
            current[j] = value
            if value < row_min:
                row_min = value
        # The running minimum is taken over the band only: every cell outside
        # it is still ``sentinel``, so it cannot be the minimum.
        if row_min > max_distance:
            return None
        previous = current
    result = previous[len_b]
    return result if result <= max_distance else None


def near_match_distance(a: str, b: str, *, max_distance: int = MAX_EDIT_DISTANCE) -> int | None:
    """The admissible edit distance between ``a`` and ``b``, capped at
    ``max_distance``, or ``None``.

    Distance 2 needs the *shorter* token to be at least ``LONG_TOKEN_LENGTH``
    long: ``urine``/``urate`` (length 5, distance 2) must be refused.

    ``max_distance`` is a real ceiling: ``max_distance=0`` never returns 1,
    because FR-36's on-save check may want a tighter ceiling than FR-79's
    default.
    """
    first_probe = min(1, max_distance)
    distance = bounded_edit_distance(a, b, max_distance=first_probe)
    if distance is not None:
        return distance
    if max_distance <= first_probe:
        return None
    if min(len(a), len(b)) < LONG_TOKEN_LENGTH:
        return None
    return bounded_edit_distance(a, b, max_distance=max_distance)


def collision_key(term: str) -> str:
    """FR-05's comparison form for ``term``: casefolded, with punctuation and
    whitespace treated as separators.

    ``'17-OHP'`` and ``'17 OHP'`` collide. ``'AntiDNA'`` and ``'Anti-DNA'`` do
    not, because the token boundary is kept.

    Tokens are joined by a space, never concatenated: ``'17 OHP'`` as
    ``'17ohp'`` would collide with the unrelated ``'17OHP'``.

    A term with no tokens (only punctuation) falls back to its casefolded,
    whitespace-collapsed form.
    """
    tokens = tokenise(term)
    if not tokens:
        return normalise_for_comparison(term).casefold()
    return " ".join(token_key(t) for t in tokens)
