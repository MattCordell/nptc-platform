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

    Category ``Zs`` includes the ordinary ASCII space, which is never a defect.
    Only its other members, such as the non-breaking space (U+00A0) and narrow
    no-break space (U+202F) named in PRD Appendix A.1, are.

    Deliberately universal, because the backend's entry-time prohibition
    (FR-74) must match the transform's definition. A line break (U+000A,
    U+000D) is a control character and stays flagged; a caller that knows a
    break is legitimate formatting (a multi-line free-text cell) filters it out
    itself.
    """
    category = unicodedata.category(ch)
    if category in INVISIBLE_CATEGORIES:
        return True
    return category == "Zs" and ch != " "


def is_normalisable_space(ch: str) -> bool:
    """True if ``ch`` is an invisible character with a single deterministic repair.

    Only a non-ASCII ``Zs`` space collapses to an ordinary space with no loss of
    meaning, which is what makes it auto-correctable (FR-71). Every other
    invisible category (``Cc`` control, ``Cf`` format such as a zero-width space
    or bidi override, ``Zl``/``Zp`` separators) has no single correct repair and
    needs a human decision.
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
    Appendix A.1 declines to quote them, and NFR-38 test 2 prohibits them in any
    generated output.
    """
    return "".join(f"<U+{ord(ch):04X}>" if is_invisible(ch) else ch for ch in text)


def has_surrounding_whitespace(text: str) -> bool:
    """True if ``text`` has leading or trailing whitespace (PRD Appendix A.3).

    ``str.strip()`` also strips non-breaking spaces, so a trailing U+00A0
    triggers this alongside ``find_invisible_characters``. They are different
    defect classes with different remedies, and both findings are intended.
    """
    return bool(text) and text != text.strip()


def normalise_for_comparison(text: str) -> str:
    """``text`` in Unicode Normalization Form C, with every normalisable space
    collapsed to an ordinary space and edge whitespace removed.

    FR-82 compares stored designations with the server "byte for byte after
    Unicode normalisation"; FR-97's reconciliation is the first caller.

    NFC only, never NFKC. NFKC folds compatibility characters (U+00B5 MICRO
    SIGN to U+03BC GREEK SMALL LETTER MU, ligatures, superscripts), all of which
    occur in pathology designations. Folding them would make two different
    strings compare equal, turning a real designation defect into a false
    match, the one direction with no report. NFC only reorders combining marks.

    No casefolding either: a case difference between a published label and a
    served designation is a real editorial difference.

    Every normalisable space collapses to an ordinary space wherever it
    occurs, not only at the edges, because ``str.strip()`` leaves an interior
    one (Appendix A.1's sample data has these) to defeat the comparison. This
    is FR-71's deterministic repair applied for comparison only, so an
    auto-correctable ``INVISIBLE_CHARACTER`` does not exempt a row from FR-97's
    reconciliation as if it were the blocking ``INVISIBLE_CHARACTER_AMBIGUOUS``.

    The final ``.strip()`` covers the case ``has_surrounding_whitespace``
    reports separately. This function is for comparison only: a finding must
    quote the original, unnormalised text so an operator sees what is in the cell.
    """
    composed = unicodedata.normalize("NFC", text)
    collapsed = "".join(" " if is_normalisable_space(ch) else ch for ch in composed)
    return collapsed.strip()
