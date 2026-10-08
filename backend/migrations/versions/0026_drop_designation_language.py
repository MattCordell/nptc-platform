"""drop_designation_language

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-09

FR-04, ADR-0022: a designation is a synonym of one entry and nothing else. The catalogue is
Australian English only, so `designation.language` always held `en-AU`, and `designation.use` could
only be `preferred` for a non-en-AU variant that cannot exist. Both columns go, with their CHECKs
and the one-active-preferred index. `designation_collision_acknowledgement.language` goes for the
same reason.

**The upgrade refuses rather than deletes.** If any designation is not `en-AU` and `synonym`, or any
acknowledgement is not `en-AU`, dropping the column would lose what the row says. The upgrade
raises `DesignationLanguageInUseError` naming each such row, and changes nothing. Re-tag the row
as a synonym in `en-AU`, or retire and remove it, then run the migration again.

**The no-duplicate index narrows.** `ix_designation_no_duplicate_active_term` goes from
`(entry_id, term_key, language)` to `(entry_id, term_key)`. With every row `en-AU`, no group can
newly collide, so the upgrade needs no duplicate check. The acknowledgement index narrows the same
way.

Downgrade restores both `language` columns (default `en-AU`), `use` (default `synonym`), the CHECKs,
the indexes and the `UPDATE` grant on `use` and `language`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LISTED_ROWS = 50

_DESIGNATIONS_TO_REFUSE = (
    "SELECT e.business_key, d.term, d.use, d.language FROM designation d "
    "JOIN catalogue_entry e ON e.id = d.entry_id "
    "WHERE d.language <> 'en-AU' OR d.use <> 'synonym' ORDER BY e.business_key, d.term"
)
_ACKNOWLEDGEMENTS_TO_REFUSE = (
    "SELECT e.business_key, a.term_key, a.language FROM designation_collision_acknowledgement a "
    "JOIN catalogue_entry e ON e.id = a.entry_id "
    "WHERE a.language <> 'en-AU' ORDER BY e.business_key, a.term_key"
)


class DesignationLanguageInUseError(RuntimeError):
    """A designation or acknowledgement carries a language or use the upgrade would lose."""


def _refuse_rows_that_would_lose_information() -> None:
    connection = op.get_bind()
    problems: list[str] = []
    for row in connection.execute(sa.text(_DESIGNATIONS_TO_REFUSE)).all():
        problems.append(
            f"designation on {row.business_key}: term {row.term!r}, use {row.use!r}, "
            f"language {row.language!r}"
        )
    for row in connection.execute(sa.text(_ACKNOWLEDGEMENTS_TO_REFUSE)).all():
        problems.append(
            f"collision acknowledgement on {row.business_key}: term key {row.term_key!r}, "
            f"language {row.language!r}"
        )
    if problems:
        listed = problems[:_LISTED_ROWS]
        more = len(problems) - len(listed)
        raise DesignationLanguageInUseError(
            f"{len(problems)} rows are not an en-AU synonym, so dropping 'use' and 'language' "
            "would lose what they say. Re-tag each as an en-AU synonym, or retire it, then run "
            "the migration again:\n"
            + "\n".join(f"  - {p}" for p in listed)
            + (f"\n  ... and {more} more" if more else "")
        )


def upgrade() -> None:
    _refuse_rows_that_would_lose_information()

    op.drop_constraint(op.f("ck_designation_no_en_au_preferred"), "designation", type_="check")
    op.drop_constraint(op.f("ck_designation_language"), "designation", type_="check")
    op.drop_constraint(op.f("ck_designation_use"), "designation", type_="check")
    op.drop_index(
        "ix_designation_one_active_preferred_per_entry_language", table_name="designation"
    )
    op.drop_index("ix_designation_no_duplicate_active_term", table_name="designation")
    op.drop_column("designation", "language")
    op.drop_column("designation", "use")
    op.create_index(
        "ix_designation_no_duplicate_active_term",
        "designation",
        ["entry_id", "term_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.drop_constraint(
        op.f("ck_designation_collision_acknowledgement_language"),
        "designation_collision_acknowledgement",
        type_="check",
    )
    op.drop_index(
        "ix_designation_collision_ack_entry_term_language",
        table_name="designation_collision_acknowledgement",
    )
    op.drop_column("designation_collision_acknowledgement", "language")
    op.create_index(
        "ix_designation_collision_ack_entry_term",
        "designation_collision_acknowledgement",
        ["entry_id", "term_key"],
        unique=True,
    )


def downgrade() -> None:
    op.add_column(
        "designation_collision_acknowledgement",
        sa.Column("language", sa.Text(), server_default=sa.text("'en-AU'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_designation_collision_acknowledgement_language"),
        "designation_collision_acknowledgement",
        "language ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'",
    )
    op.drop_index(
        "ix_designation_collision_ack_entry_term",
        table_name="designation_collision_acknowledgement",
    )
    op.create_index(
        "ix_designation_collision_ack_entry_term_language",
        "designation_collision_acknowledgement",
        ["entry_id", "term_key", "language"],
        unique=True,
    )

    op.add_column(
        "designation",
        sa.Column("use", sa.Text(), server_default=sa.text("'synonym'"), nullable=False),
    )
    op.add_column(
        "designation",
        sa.Column("language", sa.Text(), server_default=sa.text("'en-AU'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_designation_use"), "designation", "use IN ('preferred','synonym')"
    )
    op.create_check_constraint(
        op.f("ck_designation_language"),
        "designation",
        "language ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'",
    )
    op.create_check_constraint(
        op.f("ck_designation_no_en_au_preferred"),
        "designation",
        "NOT (use = 'preferred' AND language = 'en-AU')",
    )
    op.drop_index("ix_designation_no_duplicate_active_term", table_name="designation")
    op.create_index(
        "ix_designation_no_duplicate_active_term",
        "designation",
        ["entry_id", "term_key", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_designation_one_active_preferred_per_entry_language",
        "designation",
        ["entry_id", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND use = 'preferred'"),
    )
    op.execute(roles.GRANT_DESIGNATION_USE_LANGUAGE_UPDATE_SQL)
