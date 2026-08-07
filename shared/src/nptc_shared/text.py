"""Unicode text hygiene shared by the backend and the P0 transform.

Written once here (PRD Appendix A.1, FR-63, FR-74) so the transform's defect
detection and the backend's entry-time prohibition can never diverge on what
counts as an invisible character.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def is_invisible(ch: str) -> bool:
    """True if ``ch`` is a control, format, line/paragraph separator, or non-ASCII space.

    Category ``Zs`` (space separator) includes the ordinary ASCII space, which is
    never itself a defect - only the *other* members of ``Zs``, such as the
    non-breaking space (U+00A0) and narrow no-break space (U+202F) named in PRD
    Appendix A.1, are invisible-character defects.

    Deliberately universal: this is shared with the backend's entry-time
    prohibition (FR-74), which must never diverge from the transform's
    definition of an Appendix A.1 defect. A line break (U+000A/U+000D) is a
    genuine control character and stays flagged here - a caller with
    column-specific knowledge that a break is legitimate formatting (a
    multi-line free-text cell) is responsible for filtering that case out
    itself, not this function.
    """
    category = unicodedata.category(ch)
    if category in INVISIBLE_CATEGORIES:
        return True
    return category == "Zs" and ch != " "


def is_normalisable_space(ch: str) -> bool:
    """True if ``ch`` is an invisible character with a single deterministic repair.

    Only category ``Zs`` (a non-ASCII space, such as the non-breaking space
    U+00A0 or narrow no-break space U+202F) collapses unambiguously to an
    ordinary space with no loss of meaning - that is what makes it
    auto-correctable (PRD FR-71). Every other invisible category (``Cc``
    control, ``Cf`` format such as a zero-width space or bidi override,
    ``Zl``/``Zp`` line/paragraph separators) has no single correct repair, so
    a cell containing one cannot be corrected without a human decision.
    """
    return unicodedata.category(ch) == "Zs" and ch != " "


@dataclass(frozen=True)
class InvisibleCharacter:
    """One invisible character found in a string, by position."""

    offset: int
    codepoint: str
    name: str
    normalisable: bool


def find_invisible_characters(text: str) -> tuple[InvisibleCharacter, ...]:
    """Returns every invisible character in ``text``, in offset order."""
    found = []
    for offset, ch in enumerate(text):
        if is_invisible(ch):
            codepoint = f"U+{ord(ch):04X}"
            name = unicodedata.name(ch, "<unnamed>")
            found.append(
                InvisibleCharacter(
                    offset=offset,
                    codepoint=codepoint,
                    name=name,
                    normalisable=is_normalisable_space(ch),
                )
            )
    return tuple(found)


def escape_invisible(text: str) -> str:
    """Replaces every invisible character in ``text`` with its ``<U+XXXX>`` codepoint.

    Never write raw invisible characters into a report or log message: PRD
    Appendix A.1 explicitly declines to quote them verbatim, since doing so
    would place them in the document, and NFR-38 test 2 prohibits them in any
    generated output.
    """
    return "".join(f"<U+{ord(ch):04X}>" if is_invisible(ch) else ch for ch in text)


def has_surrounding_whitespace(text: str) -> bool:
    """True if ``text`` has leading or trailing whitespace (PRD Appendix A.3).

    ``str.strip()`` also strips non-breaking spaces, so a cell with a trailing
    U+00A0 correctly triggers this alongside ``find_invisible_characters`` -
    they are different defect classes with different remedies (normalise the
    character vs. strip the edge), and both findings are intended.
    """
    return bool(text) and text != text.strip()


def normalise_for_comparison(text: str) -> str:
    """``text`` in Unicode Normalization Form C, with every normalisable space
    collapsed to an ordinary space and edge whitespace removed.

    FR-82 requires stored designations to compare "byte for byte after Unicode
    normalisation" against the server, and FR-97's seeding-time reconciliation
    is the first caller. NFC only, deliberately never NFKC: NFKC is
    *compatibility* folding, which collapses distinctions the transform must
    preserve rather than paper over - it maps U+00B5 MICRO SIGN to U+03BC GREEK
    SMALL LETTER MU and folds ligatures and superscripts, all of which occur in
    pathology designations. Folding them would make two genuinely different
    strings compare equal, silently turning a real designation defect into a
    false "matches" - the one direction with no report at all. NFC only
    reorders combining marks into one canonical composed/decomposed form; it
    never changes what a human reading the string would say it means.

    No casefolding either: a case difference between a published label and a
    served designation is a real editorial difference worth surfacing, not
    noise to discard.

    Every character ``is_normalisable_space`` reports - a non-breaking space,
    a narrow no-break space, any other non-ASCII ``Zs`` character - collapses
    to an ordinary space **wherever it occurs**, not only at the edges.
    ``str.strip()`` alone only removes such a character at the start or end,
    so an *interior* one (PRD Appendix A.1's own sample data has these)
    otherwise survives into the comparison and defeats it: a caller that
    skips reconciliation whenever an invisible character remains after this
    normalisation must not have to treat a merely auto-correctable one
    (``INVISIBLE_CHARACTER``, non-blocking) as if it were the genuinely
    ambiguous kind (``INVISIBLE_CHARACTER_AMBIGUOUS``, blocking) - this
    mirrors the same deterministic repair FR-71 already specifies for that
    defect class, applied here for comparison only, so a defect band that
    does not block import cannot also silently exempt a row from FR-97's own
    designation reconciliation.

    ``.strip()`` (after the collapse above) removes the edge case
    ``has_surrounding_whitespace`` already reports as its own, separately
    auto-correctable defect - this function exists only for the
    *comparison*, never for what gets written into a message: a caller
    quoting a value in a finding must always quote the original, unnormalised
    text, so an operator sees what is actually in the cell.
    """
    composed = unicodedata.normalize("NFC", text)
    collapsed = "".join(" " if is_normalisable_space(ch) else ch for ch in composed)
    return collapsed.strip()
