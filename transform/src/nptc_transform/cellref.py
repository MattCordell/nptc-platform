"""``CellRef``: a structured, resolvable pointer to one workbook cell.

Kept as its own leaf module, importing nothing local: ``workbook.py`` produces
these (via ``Cell.reference``) and ``findings.py`` carries them (as
``Finding.location``), and either module owning this type would force an
import direction the other side doesn't need. ``workbook.py`` depends on
openpyxl; ``findings.py`` must not acquire that dependency transitively just
to hold a location, and ``findings.py`` depends on ``bands.py``; nothing in
``workbook.py`` should have to know that exists either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COLUMN_LETTER_RE = re.compile(r"^[A-Z]+$")


@dataclass(frozen=True)
class CellRef:
    """One cell's position: sheet name, A1-style column letters, 1-based row.

    ``order=True`` is deliberately not used. Field-order comparison would sort
    ``AA2`` before ``B2``, because A1 column letters are not lexicographically
    ordered strings - ``sort_key`` exists precisely to do this correctly.

    There is deliberately no ``parse()``. A sheet named ``Sales!Q1`` makes
    ``Sales!Q1!B12`` unparseable - that ambiguity is the whole reason this
    type exists instead of a plain string. Anything needing the parts holds
    the ``CellRef`` itself; nothing ever re-splits the rendered string.
    """

    sheet: str
    column_letter: str
    row: int

    def __post_init__(self) -> None:
        # Runtime-real checks, not ``assert isinstance`` - a value here can
        # type-check while still being unresolvable against a real workbook
        # (``CellRef("Sheet", "b", 0)``), which is exactly what a resolvable
        # reference must rule out. ``warn_unreachable`` is on, so these must
        # be checks mypy cannot already prove are impossible.
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

        ``(len(column_letter), column_letter)`` is exactly the numeric A1
        column index for uppercase letters (``B`` < ``AA`` because 1 letter
        sorts before 2, and same-length letters already compare correctly
        lexicographically) - and needs no openpyxl import to compute.
        """
        return (self.sheet, len(self.column_letter), self.column_letter, self.row)
