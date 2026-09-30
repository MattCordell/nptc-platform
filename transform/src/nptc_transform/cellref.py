"""``CellRef``: a structured, resolvable pointer to one workbook cell.

A leaf module that imports nothing local. ``workbook.py`` produces these (via
``Cell.reference``) and ``findings.py`` carries them (as ``Finding.location``);
if either owned the type, the other would need an import it does not want.
``workbook.py`` depends on openpyxl, which ``findings.py`` must not acquire
transitively just to hold a location. ``findings.py`` depends on ``bands.py``,
which nothing in ``workbook.py`` should need to know about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COLUMN_LETTER_RE = re.compile(r"^[A-Z]+$")


@dataclass(frozen=True)
class CellRef:
    """One cell's position: sheet name, A1-style column letters, 1-based row.

    ``order=True`` is deliberately not used: field-order comparison would sort
    ``AA2`` before ``B2``, because A1 column letters are not lexicographically
    ordered. ``sort_key`` does this correctly.

    There is deliberately no ``parse()``. A sheet named ``Sales!Q1`` makes
    ``Sales!Q1!B12`` unparseable, which is why this type exists instead of a plain
    string. Anything needing the parts holds the ``CellRef``; nothing re-splits
    the rendered string.
    """

    sheet: str
    column_letter: str
    row: int

    def __post_init__(self) -> None:
        # Runtime checks, not ``assert isinstance``: a value can type-check yet be
        # unresolvable against a real workbook (``CellRef("Sheet", "b", 0)``).
        # ``warn_unreachable`` is on, so these must be checks mypy cannot already
        # prove impossible.
        if not self.sheet:
            raise ValueError("CellRef.sheet must be non-empty")
        if not _COLUMN_LETTER_RE.fullmatch(self.column_letter):
            raise ValueError(f"CellRef.column_letter must match [A-Z]+, got {self.column_letter!r}")
        if self.row < 1:
            raise ValueError(f"CellRef.row must be >= 1, got {self.row}")

    def __str__(self) -> str:
        return f"{self.sheet}!{self.column_letter}{self.row}"

    def sort_key(self) -> tuple[str, int, str, int]:
        """A key that sorts columns numerically, not lexicographically.

        ``(len(column_letter), column_letter)`` is the numeric A1 column index for
        uppercase letters (``B`` < ``AA``: one letter sorts before two, and
        same-length letters compare correctly), with no openpyxl import.
        """
        return (self.sheet, len(self.column_letter), self.column_letter, self.row)
