"""BCP-47 (RFC 5646) language tag well-formedness, shared by the backend's
designation storage (FR-04, issue #47), export (FHIR ``designation.language``),
and the P0 transform.

This is a **syntactic** check only - "does this string have the shape of a
language tag" - never a registry lookup against IANA's subtag registry. A
constrained syntax check is enough to keep a designation from being tagged
with an obviously malformed value (an empty string, stray whitespace, a tag
with an empty subtag from a doubled hyphen); validating every subtag against
the live registry would be a second, evolving source of truth this module has
no reason to take on, and no requirement here asks for it.

Written once so the backend's entry-time check and any future export/transform
caller can never diverge on what counts as well-formed (ADR-0001's "one shared
implementation" doctrine, applied here the same way ``sctid.py`` and ``text.py``
already apply it).
"""

from __future__ import annotations

import re
from typing import Final

#: language[-script][-region][-variant...], matching the common case actually
#: seen in this catalogue (e.g. ``en``, ``en-AU``, ``mi-NZ``, ``zh-Hans-CN``) -
#: not the full RFC 5646 ABNF grammar (extended language subtags, private-use
#: tags, grandfathered tags), none of which this catalogue has any use for.
#: Each subtag is 2-8 alphanumeric characters, hyphen-separated, with no empty
#: subtag permitted - the doubled-hyphen defect class PRD Appendix A.4 already
#: documents for a different column, and just as unrepresentable here.
#:
#: The primary subtag is deliberately constrained to 2-3 letters, not
#: RFC 5646's full 2-8 - this excludes the registered/reserved 4-8 letter
#: primary subtags the RFC also permits (e.g. a future ISO 639-3 code, or a
#: private-use primary subtag), none of which this catalogue has ever used
#: or has a requirement to accept; widen this constant, not a second
#: pattern, if that changes.
LANGUAGE_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

#: The catalogue's own default (PRD §6.3) - mirrored by ``Designation.language``'s
#: column ``server_default``, so this constant and that DDL string share one
#: source of truth in prose even though `test_sql_parameterisation.py` still
#: requires the DDL itself to be a plain literal.
DEFAULT_LANGUAGE: Final[str] = "en-AU"


def is_well_formed_language_tag(tag: str) -> bool:
    """True if ``tag`` has the shape of a BCP-47 language tag.

    Deliberately case-insensitive at the pattern level (`en-au` and `en-AU`
    both match) - BCP-47 recommends but does not require canonical casing,
    and rejecting a syntactically fine tag over casing alone would be a
    stricter check than any requirement here asks for. A caller wanting
    canonical form should apply ``canonicalize_language_tag`` separately.
    """
    return bool(LANGUAGE_TAG_PATTERN.fullmatch(tag))


def canonicalize_language_tag(tag: str) -> str:
    """Folds ``tag`` to BCP-47's canonical casing: the primary subtag
    lowercase, a two-letter region subtag uppercase, a four-letter script
    subtag title-cased, every other subtag lowercase.

    Callers must check ``is_well_formed_language_tag`` first - this makes
    no attempt to validate shape, only to normalise the casing of a tag
    already known to have one.

    Exists so every string-equality comparison this catalogue makes against
    a language tag (``DEFAULT_LANGUAGE``, ``designation``'s two partial
    unique indexes, ``ck_designation_no_en_au_preferred``) can rely on
    having already run this once, at the write boundary, rather than
    ``en-au`` and ``en-AU`` silently being treated as two different
    languages (issue #224 review finding 2).
    """
    subtags = tag.split("-")
    canonical = [subtags[0].lower()]
    for subtag in subtags[1:]:
        if len(subtag) == 2 and subtag.isalpha():
            canonical.append(subtag.upper())
        elif len(subtag) == 4 and subtag.isalpha():
            canonical.append(subtag.capitalize())
        else:
            canonical.append(subtag.lower())
    return "-".join(canonical)
