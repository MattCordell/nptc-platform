"""Pure SNOMED CT URI, ECL and FSN string helpers.

No I/O, no FHIR parsing - just the string construction ``ontoserver.py`` and
callers of this package share, kept in one place so it is never re-derived
slightly differently at two call sites.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import quote

from nptc_shared.sctid import has_valid_format
from nptc_shared.terminology.models import Edition

# The final parenthesised group of an FSN, and nothing nested inside another
# group: "Microscopy (acid fast bacilli) (procedure)" has to yield "procedure",
# never "acid fast bacilli) (procedure".
_SEMANTIC_TAG = re.compile(r"\(([^()]*)\)\s*$")


def implicit_value_set_url(ecl: str, edition: Edition) -> str:
    """``http://snomed.info/sct/<module>[/version/<v>]?fhir_vs=ecl/<encoded ECL>``.

    The ECL is percent-encoded exactly once, with no safe characters: ``<``,
    ``|``, spaces and ``:`` all carry meaning in ECL and all have to survive a
    query string. The edition rides in the *base* of the implicit URL rather
    than a separate ``system-version`` parameter, so pinning (FR-49) is one
    string and the pinned and unpinned forms differ only by a path segment.
    """
    return f"{edition.system_version_uri}?fhir_vs=ecl/{quote(ecl, safe='')}"


def ecl_set_of(codes: Iterable[str]) -> str:
    """The ECL enumerating exactly ``codes`` and nothing else: ``a OR b OR c``.

    Every code is checked with ``nptc_shared.sctid.has_valid_format`` before
    being concatenated into the query - a value that is not six to eighteen
    digits cannot be an SCTID, and refusing it here is what stops a stray
    ECL operator or control character in upstream data from being injected
    into the query this builds.

    Note what this deliberately does *not* emit. FR-84 writes the hierarchy
    idiom as ``(<code1> OR <code2> OR ... OR <codeN>) MINUS <<71388002``;
    those angle brackets are the PRD's own placeholder notation, not ECL's
    descendant-of operator. Emitting a literal ``<123038009`` would ask for
    that code's *descendants*, which for a leaf procedure is the empty set -
    so every code would silently pass an FR-84 check built that way, and the
    check would report nothing, forever. Appendix A.10's own wording
    ("Expanding ``(all 50 codes) MINUS <<71388002``") is the authority on the
    intent: build the disjunction with this function, and apply ``MINUS
    <<71388002`` (or ``<<`` over whatever root is being checked) around its
    result yourself.

    Raises ``ValueError`` if ``codes`` is empty or any code fails format
    validation.
    """
    values = tuple(codes)
    if not values:
        raise ValueError("ecl_set_of requires at least one code")
    for code in values:
        if not has_valid_format(code):
            raise ValueError(f"{code!r} is not a valid SCTID (expected 6-18 digits)")
    return " OR ".join(values)


def semantic_tag(fully_specified_name: str) -> str | None:
    """The FSN's semantic tag - the text inside its final parenthesised group.

    Reading the tag, never removing it: FR-83 puts the one legitimate strip in
    the export renderer, and this function is deliberately not that. FR-99's
    check is the caller here - a concept subsumed by ``<<71388002`` whose tag
    is not ``procedure`` is a warning, and ``71388002`` \\|Procedure\\| really
    does subsume ``243120004`` \\|Regime/therapy (regime/therapy)\\| (PRD
    Appendix A.10), so the tag has to be read rather than inferred.

    ``None`` when the value carries no trailing group at all, or an empty one
    - and ``None`` is *not* the same as "the tag is not ``procedure``". A
    caller acting on FR-99 must not treat an absent tag as a violation: it
    means the label it was handed was not a served FSN (the SPIA workbook's
    "Fully Specified Name" column contains no tags at all - Appendix A.8),
    which is a different finding entirely.
    """
    match = _SEMANTIC_TAG.search(fully_specified_name)
    if match is None:
        return None
    tag = match.group(1).strip()
    return tag or None


def strip_semantic_tag(fully_specified_name: str) -> str:
    """``fully_specified_name`` with its final parenthesised group removed, once.

    FR-83 puts the one legitimate strip in the export renderer; this is a
    second, narrowly scoped call site for FR-97's seeding-time reconciliation,
    which needs "the tag-stripped FSN" purely as a value to compare against a
    workbook label, never to store or display. The invariant FR-83 actually
    protects - no double strip, because every input is a served FSN read
    fresh off the wire rather than a value that might already be stripped -
    holds here too: the caller reads this straight from a ``$expand``/
    ``$lookup`` response in the same run and immediately discards the result
    after one equality comparison (see ADR-0006).

    Uses the same ``_SEMANTIC_TAG`` pattern as ``semantic_tag``, so the two
    functions can never disagree about where the tag starts - PRD Appendix
    A.10's row 29 caution (``Microscopy (acid fast bacilli) (procedure)``
    strips to ``Microscopy (acid fast bacilli)``, not ``Microscopy``) applies
    identically to both.

    Returns ``fully_specified_name`` unchanged, never raises, when there is no
    trailing parenthesised group at all - the same "not a served FSN" case
    ``semantic_tag`` reports as ``None`` (Appendix A.8: the SPIA workbook's own
    "Fully Specified Name" column carries no tags). Raising here would abort a
    seeding run over a value that was never a served FSN to begin with; the
    caller already counts this case separately (``unresolved_fsn_count``) and
    falls through to comparing against the concept's raw designation set.
    """
    match = _SEMANTIC_TAG.search(fully_specified_name)
    if match is None:
        return fully_specified_name
    return fully_specified_name[: match.start()].rstrip()
