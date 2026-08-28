"""FR-44's strongest test: parses PRD Section 4.7's own markdown table out
of `docs/prd/NPTC-Catalogue-Platform-PRD.md` and asserts
`nptc.auth.permissions.ROLE_PERMISSIONS` reproduces it, cell by cell.

The PRD table is a genuinely independent representation of the matrix -
the maintainer's own words, "Authoritative. Where prose elsewhere in this
document disagrees with this table, the table wins" (PRD Section 4.7).
The only hand-written bridge is `_ROW_PERMISSIONS` below (row label ->
permission set), with exhaustiveness asserted in both directions: a PRD
row this bridge doesn't recognise fails loudly (not silently skipped), and
a `Permission` never referenced by any row fails too - both catch a matrix
edit on one side that the other side missed.

The parser itself raises rather than silently returning zero rows if the
section heading moves or the table shape changes (`_locate_matrix_section`,
the explicit row-count assertion) - a guard that cannot rot into an
always-pass, the same discipline `test_token_verification_guard.py`'s own
positive control applies to itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nptc.auth.permissions import ROLE_PERMISSIONS, Permission, Role

REPO_ROOT = Path(__file__).resolve().parents[2]
PRD_PATH = REPO_ROOT / "docs" / "prd" / "NPTC-Catalogue-Platform-PRD.md"

#: Column order exactly as PRD Section 4.7 states it.
_COLUMN_ROLES = [
    Role.ANON,
    Role.OBSERVER,
    Role.PROVISIONAL,
    Role.MEMBER,
    Role.REVIEWER,
    Role.ADMINISTRATOR,
]

#: The one row with a qualifier that changes *which* permission applies
#: (own vs any) rather than merely how much (the max-5/20-hr quota rows,
#: which map to the same permission regardless of the number - quotas are
#: `nptc.auth.permissions.QUOTAS`, not permissions, see that module's
#: docstring).
_WITHDRAW_ROW_LABEL = "Withdraw own submission before approval"

#: Row label (exactly as the PRD table cell reads, backtick-quoted terms
#: included) -> the permission(s) that row's "Y" grants. Every row in the
#: table except `_WITHDRAW_ROW_LABEL` (handled specially below) must
#: appear here - `test_every_prd_row_is_recognised` asserts that.
_ROW_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "Browse and search approved catalogue": frozenset({Permission.CATALOGUE_BROWSE}),
    "Retrieve published release artefacts": frozenset({Permission.RELEASE_RETRIEVE}),
    "View pending submissions and states": frozenset({Permission.SUBMISSION_VIEW}),
    "View interest counts": frozenset({Permission.INTEREST_VIEW_COUNTS}),
    "Create submissions": frozenset({Permission.SUBMISSION_CREATE}),
    "Propose amendments": frozenset({Permission.AMENDMENT_PROPOSE}),
    "Register interest": frozenset({Permission.INTEREST_REGISTER}),
    "View property registry (read-only)": frozenset({Permission.REGISTRY_READ}),
    "View submitter identities": frozenset({Permission.SUBMITTER_IDENTITY_VIEW}),
    "View who registered interest": frozenset({Permission.INTEREST_IDENTITIES_VIEW}),
    "Read and write internal comments": frozenset(
        {Permission.COMMENT_INTERNAL_READ, Permission.COMMENT_INTERNAL_WRITE}
    ),
    "Transition submissions up to `Ready for approval`": frozenset(
        {Permission.SUBMISSION_TRANSITION_REVIEW}
    ),
    "Run validation, acknowledge findings": frozenset(
        {Permission.VALIDATION_RUN, Permission.VALIDATION_ACKNOWLEDGE}
    ),
    "Promote Provisional to Member": frozenset({Permission.ROLE_GRANT_MEMBER}),
    "Transition to `Approved`": frozenset({Permission.SUBMISSION_TRANSITION_APPROVE}),
    "Edit published catalogue entries": frozenset({Permission.CATALOGUE_EDIT_PUBLISHED}),
    "Cut and publish releases": frozenset({Permission.RELEASE_PUBLISH}),
    "Manage property registry and local code systems": frozenset({Permission.REGISTRY_MANAGE}),
    "Manage export configuration": frozenset({Permission.EXPORT_CONFIG_MANAGE}),
    "Grant or revoke Observer, Reviewer, Administrator": frozenset({Permission.ROLE_GRANT_ANY}),
    "Suspend users, override rate limits": frozenset(
        {Permission.USER_SUSPEND, Permission.USER_RATE_LIMIT_OVERRIDE}
    ),
    "Read the audit log": frozenset({Permission.AUDIT_READ}),
}

_EXPECTED_ROW_COUNT = len(_ROW_PERMISSIONS) + 1  # + the withdraw row


def _locate_matrix_section(markdown: str) -> str:
    """Returns the markdown between the "4.7 Permission matrix" heading
    and the next "## " heading. Raises if the heading cannot be found -
    the PRD moving or renaming this section must fail this test loudly,
    never silently parse zero rows."""
    match = re.search(r"### 4\.7 Permission matrix\b(.*?)(?=\n## )", markdown, re.DOTALL)
    if match is None:
        raise AssertionError(
            "could not locate the '### 4.7 Permission matrix' section in the PRD - "
            "has the heading moved or been renamed?"
        )
    return match.group(1)


def _parse_table_rows(section: str) -> list[list[str]]:
    """Every markdown table row (lines starting with `|`), split into
    cells, excluding the header and the `|---|...` separator row."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue  # the |---|:--:|... separator row
        rows.append(cells)
    return rows


@pytest.fixture(scope="module")
def matrix_rows() -> list[list[str]]:
    markdown = PRD_PATH.read_text(encoding="utf-8")
    section = _locate_matrix_section(markdown)
    rows = _parse_table_rows(section)
    assert len(rows) >= 2, "expected at least a header row and one data row"
    header, *data_rows = rows
    assert header[0] == "Capability"
    assert header[1:] == ["Anon", "Observer", "Provisional", "Member", "Reviewer", "Admin"]
    return data_rows


def test_parser_finds_the_expected_number_of_rows(matrix_rows: list[list[str]]) -> None:
    """Guards the parser itself against silently matching nothing - see
    the module docstring."""
    assert len(matrix_rows) == _EXPECTED_ROW_COUNT


def test_every_prd_row_is_recognised(matrix_rows: list[list[str]]) -> None:
    """A PRD row this test's bridge doesn't know about must fail loudly,
    not be silently skipped - this is what keeps a future 4.7 edit honest."""
    unrecognised = [
        row[0]
        for row in matrix_rows
        if row[0] != _WITHDRAW_ROW_LABEL and row[0] not in _ROW_PERMISSIONS
    ]
    assert not unrecognised, f"PRD row(s) not recognised by _ROW_PERMISSIONS: {unrecognised}"


def test_every_permission_is_referenced_by_some_row() -> None:
    """The mirror image of the above: a `Permission` never granted by any
    matrix row is an orphan constant - dead code, or a row this bridge
    forgot to update."""
    referenced: set[Permission] = set()
    for perms in _ROW_PERMISSIONS.values():
        referenced |= perms
    referenced |= {Permission.SUBMISSION_WITHDRAW_OWN, Permission.SUBMISSION_WITHDRAW_ANY}
    assert referenced == set(Permission)


def _cell_permissions(row_label: str, cell: str) -> frozenset[Permission]:
    if row_label == _WITHDRAW_ROW_LABEL:
        if "own" in cell:
            return frozenset({Permission.SUBMISSION_WITHDRAW_OWN})
        if "any" in cell:
            return frozenset({Permission.SUBMISSION_WITHDRAW_ANY})
        assert cell == ".", f"unexpected withdraw-row cell {cell!r}"
        return frozenset()

    if cell == ".":
        return frozenset()
    assert cell.startswith("Y"), f"unexpected cell {cell!r} for row {row_label!r}"
    return _ROW_PERMISSIONS[row_label]


@pytest.mark.req("FR-44")
def test_role_permissions_matches_the_prd_matrix_cell_by_cell(matrix_rows: list[list[str]]) -> None:
    expected: dict[Role, set[Permission]] = {role: set() for role in _COLUMN_ROLES}
    for row in matrix_rows:
        label, *cells = row
        for role, cell in zip(_COLUMN_ROLES, cells, strict=True):
            expected[role] |= _cell_permissions(label, cell)

    for role in _COLUMN_ROLES:
        assert ROLE_PERMISSIONS[role] == frozenset(expected[role]), (
            f"{role.value}: code grants {sorted(ROLE_PERMISSIONS[role])}, "
            f"PRD Section 4.7 grants {sorted(expected[role])}"
        )
