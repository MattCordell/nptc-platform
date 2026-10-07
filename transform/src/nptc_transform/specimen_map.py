"""The terminologist-reviewed map from workbook specimen strings to SNOMED CT codes (ADR-0044).

``data/specimen_map.tsv`` is the one source of specimen vocabulary. A string resolves by trimmed,
case-insensitive equality and by nothing else: no prefix, substring or similarity match, because a
wrong code seeded silently is worse than a row that blocks (FR-88).

A row marked "no map" has no code. It names a test that needs no specimen, so it yields no value.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from importlib import resources
from typing import Final

from nptc_shared.sctid import has_valid_check_digit, has_valid_format
from nptc_shared.text import normalise_for_comparison

_SOURCE: Final = "Source display"
_CODE: Final = "Target code"
_DISPLAY: Final = "Target display"
_RELATIONSHIP: Final = "Relationship type code"
_NO_MAP: Final = "No map flag"
_STATUS: Final = "Status"
_REQUIRED_COLUMNS: Final = (_SOURCE, _CODE, _DISPLAY, _RELATIONSHIP, _NO_MAP, _STATUS)

_KNOWN_RELATIONSHIPS: Final = frozenset({"TARGET_EQUIVALENT", "TARGET_INEXACT", "TARGET_BROADER"})
_REVIEWED_STATUS: Final = "MAPPED"
_BOM: Final = chr(0xFEFF)

SPECIMEN_MAP_FILE: Final = "specimen_map.tsv"


class SpecimenMapError(ValueError):
    """The map file is malformed. ``problems`` lists every defect found, each naming its line."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(
            f"specimen map is not valid ({len(problems)} problem(s)): " + "; ".join(problems)
        )
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class SpecimenMapEntry:
    """One reviewed row. ``code`` is ``None`` exactly when the row is marked "no map".
    ``line`` is the 1-based line in the file, for a finding that points at the row."""

    source: str
    code: str | None
    display: str | None
    relationship: str | None
    line: int


def normalise_specimen_key(text: str) -> str:
    return normalise_for_comparison(text).casefold()


@dataclass(frozen=True)
class SpecimenMap:
    entries: tuple[SpecimenMapEntry, ...]
    #: The spreadsheet column letter of ``Target code``, for a finding that points at a row.
    code_column: str = "C"

    @cached_property
    def _by_key(self) -> dict[str, SpecimenMapEntry]:
        return {normalise_specimen_key(entry.source): entry for entry in self.entries}

    def resolve(self, text: str) -> SpecimenMapEntry | None:
        """The row ``text`` names, or ``None`` when the map does not cover it."""
        return self._by_key.get(normalise_specimen_key(text))

    @cached_property
    def codes(self) -> tuple[str, ...]:
        """Every distinct target code, sorted (FR-73)."""
        return tuple(sorted({entry.code for entry in self.entries if entry.code is not None}))


def _entry_from_row(row: dict[str, str], line: int, problems: list[str]) -> SpecimenMapEntry | None:
    def problem(message: str) -> None:
        problems.append(f"line {line}: {message}")

    source = row[_SOURCE].strip()
    code = row[_CODE].strip()
    display = row[_DISPLAY].strip()
    relationship = row[_RELATIONSHIP].strip()
    flag = row[_NO_MAP].strip().casefold()
    if not source:
        problem("the source string is blank")
    if row[_STATUS].strip() != _REVIEWED_STATUS:
        problem(f"status must be {_REVIEWED_STATUS!r}")
    if flag not in {"true", "false"}:
        problem("the no-map flag must be 'true' or 'false'")
        return None
    if flag == "true":
        if code or display or relationship:
            problem(f"{source!r} is marked no-map but carries a target")
        return SpecimenMapEntry(source, None, None, None, line)
    if not (has_valid_format(code) and has_valid_check_digit(code)):
        problem(f"{source!r} has target code {code!r}, which is not a valid SCTID")
    if not display:
        problem(f"{source!r} has no target display")
    if relationship not in _KNOWN_RELATIONSHIPS:
        problem(f"{source!r} has unknown relationship {relationship!r}")
    return SpecimenMapEntry(source, code, display, relationship, line)


def parse_specimen_map(text: str) -> SpecimenMap:
    """Parses and validates the TSV, raising one ``SpecimenMapError`` that lists every defect."""
    reader = csv.DictReader(
        io.StringIO(text.removeprefix(_BOM), newline=""), delimiter="\t", quoting=csv.QUOTE_NONE
    )
    missing = [column for column in _REQUIRED_COLUMNS if column not in (reader.fieldnames or ())]
    if missing:
        raise SpecimenMapError([f"missing column(s) {', '.join(missing)}"])

    problems: list[str] = []
    entries: list[SpecimenMapEntry] = []
    first_line: dict[str, int] = {}
    for row in reader:
        line = reader.line_num
        if None in row or None in row.values():
            problems.append(f"line {line}: the row does not have one cell per column")
            continue
        entry = _entry_from_row(row, line, problems)
        if entry is None:
            continue
        key = normalise_specimen_key(entry.source)
        if key in first_line:
            problems.append(
                f"line {line}: {entry.source!r} repeats the string on line {first_line[key]} "
                "(case and spacing are ignored)"
            )
            continue
        first_line[key] = line
        entries.append(entry)
    if not entries and not problems:
        problems.append("the map has no rows")
    if problems:
        raise SpecimenMapError(problems)
    column = (reader.fieldnames or []).index(_CODE)
    return SpecimenMap(tuple(entries), chr(ord("A") + column) if column < 26 else "A")


def _load_packaged() -> SpecimenMap:
    resource = resources.files("nptc_transform").joinpath("data", SPECIMEN_MAP_FILE)
    return parse_specimen_map(resource.read_text(encoding="utf-8-sig"))


SPECIMEN_MAP: Final = _load_packaged()
