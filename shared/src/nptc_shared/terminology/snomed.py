"""Pure SNOMED CT URI, ECL and FSN string helpers.

No I/O, no FHIR parsing: the string construction ``ontoserver.py`` and callers
of this package share, kept in one place so it is never derived slightly
differently at two call sites.

The URI parsers raise ``ValueError`` on any shape they do not recognise. A
property's stored ``value_set_uri`` and ``edition`` are server-side data, not
caller input, so a bad value is a data-integrity fault for the caller to
classify.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import quote, unquote

from nptc_shared.sctid import has_valid_format
from nptc_shared.terminology.models import (
    SNOMED_CT_AU,
    SNOMED_CT_INTERNATIONAL,
    SNOMED_SYSTEM,
    Edition,
)

# The final parenthesised group of an FSN, and nothing nested inside another
# group: "Microscopy (acid fast bacilli) (procedure)" has to yield "procedure",
# never "acid fast bacilli) (procedure".
_SEMANTIC_TAG = re.compile(r"\(([^()]*)\)\s*$")


def implicit_value_set_url(ecl: str, edition: Edition) -> str:
    """``http://snomed.info/sct/<module>[/version/<v>]?fhir_vs=ecl/<encoded ECL>``.

    The ECL is percent-encoded once, with no safe characters, because ``<``,
    ``|``, spaces and ``:`` all carry meaning in ECL. The edition rides in the
    base of the URL rather than a ``system-version`` parameter, so pinning
    (FR-49) is one string and the pinned and unpinned forms differ only by a
    path segment.
    """
    return f"{edition.system_version_uri}?fhir_vs=ecl/{quote(ecl, safe='')}"


def ecl_from_implicit_value_set_url(uri: str) -> str:
    """The ECL a SNOMED implicit value set URI encodes, the inverse of
    ``implicit_value_set_url``.

    Only the ``?fhir_vs=ecl/<percent-encoded ECL>`` form (FR-10's binding shape
    for a coded property) is recognised. Any other form (``?fhir_vs=isa/...``,
    ``?fhir_vs=refset/...``, or no ``fhir_vs`` at all) raises ``ValueError``
    rather than guess.
    """
    marker = "?fhir_vs=ecl/"
    if marker not in uri:
        raise ValueError(
            f"{uri!r} is not a SNOMED implicit ECL value set URI (expected {marker!r})"
        )
    _base, _, encoded_ecl = uri.partition(marker)
    if not encoded_ecl:
        raise ValueError(f"{uri!r} has no ECL after {marker!r}")
    return unquote(encoded_ecl)


#: The base of a module-qualified implicit value set URI,
#: ``<system>/<module>[/version/<v>]``, the shape `Edition.system_version_uri`
#: builds.
_VALUE_SET_BASE = re.compile(
    rf"^{re.escape(SNOMED_SYSTEM)}/(?P<module_id>[^/]+)(?:/version/(?P<version>[^/]+))?$"
)


def edition_from_implicit_value_set_url(
    uri: str,
    *,
    label: str | None = None,
    known_editions: Iterable[Edition] = (SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL),
) -> Edition:
    """The `Edition` a SNOMED implicit value set URI encodes.

    It reads the base of the URI, where `ecl_from_implicit_value_set_url`
    reads the query string. Two shapes are recognised:

    - **Module-qualified** (`implicit_value_set_url`'s output):
      `<system>/<module>[/version/<v>]?fhir_vs=ecl/...`. The module id and any
      pinned version come from the URI and are matched against
      `known_editions`. `label` is not consulted, so a pinned version is never
      dropped in favour of a caller-supplied edition.
    - **Bare system** (the PRD's S6.6 worked example, and what
      `nptc.db.bootstrap` stores for the seeded `specimen` binding):
      `<system>?fhir_vs=ecl/...`. This shape names no edition, so `label`
      (normally the same `PropertyDefinition.edition`) is matched against
      `known_editions.label`. Raises `ValueError` if `label` is `None` or
      matches none, so an `Edition` is never built from an arbitrary label.

    Any other shape, or a module id matching neither known edition, also
    raises `ValueError`.
    """
    marker = "?fhir_vs=ecl/"
    base, separator, _ = uri.partition(marker)
    if not separator:
        raise ValueError(
            f"{uri!r} is not a SNOMED implicit ECL value set URI (expected {marker!r})"
        )
    if base == SNOMED_SYSTEM:
        for candidate in known_editions:
            if candidate.label == label:
                return candidate
        raise ValueError(
            f"{uri!r} names no SNOMED module, and label {label!r} does not match a "
            "recognised edition"
        )
    match = _VALUE_SET_BASE.match(base)
    if match is None:
        raise ValueError(
            f"{uri!r}'s base {base!r} is not a recognised SNOMED system[/module[/version]] URI"
        )
    module_id = match.group("module_id")
    version = match.group("version")
    for candidate in known_editions:
        if candidate.module_id == module_id:
            return candidate.pinned_to(version) if version is not None else candidate
    raise ValueError(f"{uri!r} names an unrecognised SNOMED module id {module_id!r}")


def ecl_set_of(codes: Iterable[str]) -> str:
    """The ECL enumerating exactly ``codes`` and nothing else: ``a OR b OR c``.

    Every code is checked with ``nptc_shared.sctid.has_valid_format`` first, so
    a stray ECL operator or control character in upstream data cannot be
    injected into the query.

    FR-84 writes ``(<code1> OR ... OR <codeN>) MINUS <<71388002``, but the
    angle brackets around the codes are the PRD's placeholder notation, not
    ECL's descendant-of operator. A literal ``<123038009`` asks for that
    code's *descendants*, which for a leaf procedure is the empty set, so every
    code would silently pass an FR-84 check built that way. Appendix A.10 gives
    the intent: build the disjunction here, and apply ``MINUS <<71388002`` (or
    ``<<`` over whatever root is being checked) around it yourself.

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
    """The FSN's semantic tag: the text inside its final parenthesised group.

    This reads the tag and never removes it; FR-83's one legitimate strip is
    in the export renderer. FR-99 is the caller: a concept subsumed by
    ``<<71388002`` whose tag is not ``procedure`` is a warning.
    ``71388002`` \\|Procedure\\| really does subsume ``243120004``
    \\|Regime/therapy (regime/therapy)\\| (PRD Appendix A.10), so the tag has
    to be read rather than inferred.

    ``None`` when there is no trailing group, or an empty one. That is not
    "the tag is not ``procedure``": an absent tag means the label was not a
    served FSN (the SPIA workbook's "Fully Specified Name" column has no tags,
    Appendix A.8), which is a different finding. An FR-99 caller must not treat
    it as a violation.
    """
    match = _SEMANTIC_TAG.search(fully_specified_name)
    if match is None:
        return None
    tag = match.group(1).strip()
    return tag or None


def strip_semantic_tag(fully_specified_name: str) -> str:
    """``fully_specified_name`` with its final parenthesised group removed, once.

    A second, narrowly scoped strip beside the export renderer's (FR-83), for
    FR-97's seeding-time reconciliation. It yields a value to compare against a
    workbook label, never to store or display. The no-double-strip invariant
    FR-83 protects holds because the caller reads a served FSN fresh off the
    wire and discards the result after one comparison (ADR-0006).

    Uses ``_SEMANTIC_TAG``, so this and ``semantic_tag`` cannot disagree about
    where the tag starts: ``Microscopy (acid fast bacilli) (procedure)`` strips
    to ``Microscopy (acid fast bacilli)`` (PRD Appendix A.10, row 29).

    Returns the input unchanged, and never raises, when there is no trailing
    group (the "not a served FSN" case ``semantic_tag`` reports as ``None``).
    Raising would abort a seeding run over a value that was never a served FSN,
    and the caller already counts that case (``unresolved_fsn_count``).
    """
    match = _SEMANTIC_TAG.search(fully_specified_name)
    if match is None:
        return fully_specified_name
    return fully_specified_name[: match.start()].rstrip()
