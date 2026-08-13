"""FR-71's three auto-correctable repairs, as functions returning corrected
values (issue #31, P0-9).

Detection of *whether* a repair applies stays in ``cell_defects.py`` - a cell
that reaches ``dataset.py`` has already been proven, by the absence of a
blocking finding, to need at most these three repairs. Reuses
``nptc_shared.text`` throughout rather than re-deriving the character classes
FR-71 already defines there, the same discipline ``cell_defects.py`` follows.
"""

from __future__ import annotations

from nptc_shared.text import is_normalisable_space


def correct_invisible_characters(text: str) -> str:
    """Collapses every normalisable-space invisible character in ``text`` to
    an ordinary space (``INVISIBLE_CHARACTER``, FR-71).

    Applied wherever the character occurs, not only at the edges - mirrors
    ``nptc_shared.text.normalise_for_comparison``'s own collapse, but writes
    the result into the *stored* value rather than using it for comparison
    only. Never called for ``INVISIBLE_CHARACTER_AMBIGUOUS``: that band
    blocks emission before this function would ever run on such a cell.
    """
    return "".join(" " if is_normalisable_space(ch) else ch for ch in text)


def correct_surrounding_whitespace(text: str) -> str:
    """Strips leading and trailing whitespace (``SURROUNDING_WHITESPACE``, FR-71).

    Never called on a whitespace-only cell: that is ``WHITESPACE_ONLY_CELL``,
    a ``requires-human-decision`` finding that blocks emission before this
    function would run on it, since stripping it would silently decide the
    cell means "empty" on RCPA-QAP's behalf.
    """
    return text.strip()


def correct_code_cell(cell_text: str) -> str:
    """The code cell's text, as a plain string (``CODE_CELL_NOT_TEXT``, FR-06).

    ``Cell.text`` already renders a ``NUMBER``-typed cell's digits exactly
    (``workbook._render_text``'s own int branch never routes an int through
    float) - this function exists so a caller has one explicit name for the
    repair FR-71 promises, not because the text itself needs transforming
    here. Never called on a cell already found to have lost precision
    (``NUMERIC_PRECISION_RISK``, blocking): there is nothing left to recover.
    """
    return cell_text


def apply_corrections(text: str) -> str:
    """Every FR-71 auto-correctable text repair, composed in the order that
    makes them work together: collapsing an edge non-breaking space to an
    ordinary space *before* stripping is what lets the strip remove it -
    exactly as ``nptc_shared.text.has_surrounding_whitespace``'s own
    docstring already documents for that interaction.
    """
    return correct_surrounding_whitespace(correct_invisible_characters(text))
