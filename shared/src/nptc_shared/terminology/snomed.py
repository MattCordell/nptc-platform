"""Pure SNOMED CT URI and ECL string builders.

No I/O, no FHIR parsing - just the string construction ``ontoserver.py`` and
callers of this package share, kept in one place so it is never re-derived
slightly differently at two call sites.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

from nptc_shared.sctid import has_valid_format
from nptc_shared.terminology.models import Edition


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
