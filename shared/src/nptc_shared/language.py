"""BCP-47 (RFC 5646) language tag well-formedness, shared by the backend's
designation storage (FR-04), export (FHIR ``designation.language``) and the P0
transform.

This is a **syntactic** check only: "does this string have the shape of a
language tag". It never consults IANA's subtag registry, which would be a second,
evolving source of truth that no requirement asks for. The shape check is enough
to keep an empty string, stray whitespace or a doubled hyphen out of a
designation's language.

Written once so the backend's entry-time check and any export or transform
caller cannot diverge on what is well-formed (ADR-0001's "one shared
implementation" rule, as ``sctid.py`` and ``text.py`` apply it).
"""

from __future__ import annotations

import re
from typing import Final

#: language[-script][-region][-variant...], matching the tags seen in this
#: catalogue (``en``, ``en-AU``, ``mi-NZ``, ``zh-Hans-CN``), not the full RFC
#: 5646 ABNF (extended language subtags, private-use and grandfathered tags).
#: Each later subtag is 2-8 alphanumeric characters, hyphen-separated, with no
#: empty subtag: the doubled-hyphen defect PRD Appendix A.4 documents for
#: another column.
#:
#: The primary subtag is 2-3 letters, not RFC 5646's 2-8, which excludes the
#: registered and private-use 4-8 letter primaries the catalogue never uses.
#: Widen this constant, not a second pattern, if that changes.
LANGUAGE_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

#: The catalogue's default (PRD §6.3), mirrored by ``Designation.language``'s
#: column ``server_default``. The DDL must stay a plain literal
#: (``test_sql_parameterisation.py``), so the two agree by convention.
DEFAULT_LANGUAGE: Final[str] = "en-AU"


def is_well_formed_language_tag(tag: str) -> bool:
    """True if ``tag`` has the shape of a BCP-47 language tag.

    Case-insensitive (``en-au`` and ``en-AU`` both match): BCP-47 recommends but
    does not require canonical casing, so rejecting on casing alone would be
    stricter than any requirement. Apply ``canonicalize_language_tag`` for
    canonical form.
    """
    return bool(LANGUAGE_TAG_PATTERN.fullmatch(tag))


def canonicalize_language_tag(tag: str) -> str:
    """Folds ``tag`` to BCP-47's canonical casing: the primary subtag
    lowercase, a two-letter region subtag uppercase, a four-letter script
    subtag title-cased, every other subtag lowercase.

    Call ``is_well_formed_language_tag`` first: this normalises casing only.

    Run once at the write boundary so every string comparison against a
    language tag (``DEFAULT_LANGUAGE``, the designation table's two partial
    unique indexes, ``ck_designation_no_en_au_preferred``) can assume it, and
    ``en-au`` and ``en-AU`` are never treated as two languages.
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
