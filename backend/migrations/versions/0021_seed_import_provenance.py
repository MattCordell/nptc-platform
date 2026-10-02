"""seed_import and entry_seed_provenance

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-03

FR-76, ADR-0010, ADR-0042: the record of the baseline seeding run, and each seeded
entry's provenance, which ADR-0010 commits to preserving verbatim. See
`nptc.db.models.seed_import` and `nptc.db.models.entry_seed_provenance` for the reasoning.

Both grants are `SELECT, INSERT` only, so "written once by the loader, never edited" holds at the
privilege level. The downgrade drops both tables outright, provenance first because it holds the
foreign key to `seed_import`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seed_import",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("release_name", sa.Text(), nullable=False),
        sa.Column("release_note", sa.Text(), nullable=False),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("dataset_schema_version", sa.Integer(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton", name=op.f("ck_seed_import_singleton")),
        sa.CheckConstraint(
            "length(btrim(release_name)) > 0", name=op.f("ck_seed_import_release_name_not_blank")
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_seed_import_source_sha256")
        ),
        sa.CheckConstraint("entry_count > 0", name=op.f("ck_seed_import_entry_count")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_import")),
    )
    op.create_index("ix_seed_import_singleton", "seed_import", ["singleton"], unique=True)

    op.create_table(
        "entry_seed_provenance",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("seed_import_id", sa.UUID(), nullable=False),
        sa.Column("source_sheet", sa.Text(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("legacy_version", sa.Text(), nullable=True),
        sa.Column("legacy_history", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(source_sheet)) > 0",
            name=op.f("ck_entry_seed_provenance_source_sheet_not_blank"),
        ),
        sa.CheckConstraint("source_row >= 1", name=op.f("ck_entry_seed_provenance_source_row")),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_entry_seed_provenance_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["seed_import_id"],
            ["seed_import.id"],
            name=op.f("fk_entry_seed_provenance_seed_import_id_seed_import"),
        ),
        sa.PrimaryKeyConstraint("entry_id", name=op.f("pk_entry_seed_provenance")),
    )

    op.execute(roles.GRANT_SEED_IMPORT_SQL)
    op.execute(roles.REVOKE_SEED_IMPORT_WRITE_SQL)
    op.execute(roles.GRANT_ENTRY_SEED_PROVENANCE_SQL)
    op.execute(roles.REVOKE_ENTRY_SEED_PROVENANCE_WRITE_SQL)


def downgrade() -> None:
    op.drop_table("entry_seed_provenance")
    op.drop_index("ix_seed_import_singleton", table_name="seed_import")
    op.drop_table("seed_import")
