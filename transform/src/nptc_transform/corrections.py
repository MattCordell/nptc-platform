"""FR-71's three auto-correctable repairs, as functions returning corrected
values.

Detection of *whether* a repair applies stays in ``cell_defects.py``. A cell that
reaches ``dataset.py`` has no blocking finding, so it needs at most these three
repairs. The character classes come from ``nptc_shared.text``, as in
``cell_defects.py``, not re-derived here.
"""

from __future__ import annotations

from nptc_shared.text import is_normalisable_space


def correct_invisible_characters(text: str) -> str:
    """Collapses every normalisable-space invisible character in ``text`` to
    an ordinary space (``INVISIBLE_CHARACTER``, FR-71).

    Applied wherever the character occurs, not only at the edges. It mirrors
    ``nptc_shared.text.normalise_for_comparison``'s collapse but writes the
    result into the *stored* value. Never called for
    ``INVISIBLE_CHARACTER_AMBIGUOUS``: that band blocks emission first.
    """
    return "".join(" " if is_normalisable_space(ch) else ch for ch in text)


def correct_surrounding_whitespace(text: str) -> str:
    """Strips leading and trailing whitespace (``SURROUNDING_WHITESPACE``, FR-71).

    Never called on a whitespace-only cell: ``WHITESPACE_ONLY_CELL`` is a
    ``requires-human-decision`` finding that blocks emission first, because
    stripping it would decide on RCPA-QAP's behalf that the cell means "empty".
    """
    return text.strip()


def correct_code_cell(cell_text: str) -> str:
    """The code cell's text, as a plain string (``CODE_CELL_NOT_TEXT``, FR-06).

    ``Cell.text`` already renders a ``NUMBER``-typed cell's digits exactly
    (``workbook._render_text`` never routes an int through float). This function
    gives a caller one explicit name for the repair FR-71 promises; the text needs
    no transformation. Never called on a cell that has lost precision
    (``NUMERIC_PRECISION_RISK``, blocking), where nothing is left to recover.
    """
    return cell_text


def apply_corrections(text: str) -> str:
    """Every FR-71 auto-correctable text repair, composed in the order that makes
    them work together: collapsing an edge non-breaking space to an ordinary space
    *before* stripping is what lets the strip remove it (see
    ``nptc_shared.text.has_surrounding_whitespace``).
    """
    return correct_surrounding_whitespace(correct_invisible_characters(text))
