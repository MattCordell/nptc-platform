"""submission

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-10

FR-23, FR-27, FR-28, FR-29: a submission is its own record, not a catalogue entry with another
status. See `nptc.db.models.submission` for the reasoning.

The grants live in this migration with the table. Only `state`, `updated_at` and `row_version` are
updatable, and `DELETE` is revoked, so "what the submitter sent is never edited or removed" holds at
the privilege level. The downgrade drops the table, which discards every submission.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'Submitted'"), nullable=False),
        sa.Column("preferred_term", sa.Text(), nullable=False),
        sa.Column(
            "synonyms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("snomed_code", sa.Text(), nullable=True),
        sa.Column("snomed_fsn", sa.Text(), nullable=True),
        sa.Column(
            "property_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("submitter_id", sa.UUID(), nullable=False),
        sa.Column("organisation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("kind IN ('new_test','amendment')", name=op.f("ck_submission_kind")),
        sa.CheckConstraint("state IN ('Submitted')", name=op.f("ck_submission_state")),
        sa.CheckConstraint(
            "length(btrim(preferred_term)) > 0",
            name=op.f("ck_submission_preferred_term_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(synonyms) = 'array'", name=op.f("ck_submission_synonyms_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(property_values) = 'object'",
            name=op.f("ck_submission_property_values_is_object"),
        ),
        sa.CheckConstraint(
            "nptc_sctid_is_valid(snomed_code)", name=op.f("ck_submission_snomed_code")
        ),
        sa.CheckConstraint(
            "snomed_fsn IS NULL OR length(btrim(snomed_fsn)) > 0",
            name=op.f("ck_submission_snomed_fsn_not_blank"),
        ),
        sa.CheckConstraint(
            "(snomed_code IS NULL) = (snomed_fsn IS NULL)",
            name=op.f("ck_submission_snomed_code_with_fsn"),
        ),
        sa.CheckConstraint(
            "notes IS NULL OR length(btrim(notes)) > 0", name=op.f("ck_submission_notes_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["submitter_id"], ["app_user.id"], name=op.f("fk_submission_submitter_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission")),
    )
    op.create_index("ix_submission_submitter_id", "submission", ["submitter_id"])
    op.create_index("ix_submission_created_at", "submission", ["created_at"])

    op.execute(roles.GRANT_SUBMISSION_SQL)
    op.execute(roles.GRANT_SUBMISSION_UPDATE_SQL)
    op.execute(roles.REVOKE_SUBMISSION_DELETE_SQL)


def downgrade() -> None:
    op.drop_index("ix_submission_created_at", table_name="submission")
    op.drop_index("ix_submission_submitter_id", table_name="submission")
    op.drop_table("submission")
