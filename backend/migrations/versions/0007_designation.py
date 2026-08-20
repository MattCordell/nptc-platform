"""designation

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20

The privilege grants live in this same migration, not a later
"permissions" one - see 0002_audit_event.py's/0003's/0005's/0006's
docstrings; the same grants-live-with-the-table reasoning (ADR-0011)
applies here.

Issue #47 (FR-04, FR-24, FR-37, FR-85): catalogue-side designations
(synonyms and non-en-AU preferred-term variants), never the SNOMED
CT-served labels that live on `code_binding` (#48) - see
`nptc.db.models.designation`'s own module docstring for the full
three-strings reasoning.

Three invariants enforced at the database layer, not merely by
application convention:

- The catalogue's en-AU preferred term lives in exactly one place -
  `catalogue_entry.preferred_term` - never duplicated into a `designation`
  row: `ck_designation_no_en_au_preferred` forbids
  `use = 'preferred' AND language = 'en-AU'`.
- At most one active preferred designation per `(entry_id, language)`, and
  no duplicate active `(entry_id, term, language)` - both partial unique
  indexes, `postgresql_where`-scoped to `status = 'active'` so a retired
  row never blocks a fresh one from being added under the same term. The
  second exists because the same synonym attached twice to one entry -
  whether from a doubled delimiter or a whitespace variant, PRD Appendix
  A.4 - must be unrepresentable, not merely discouraged by convention.
- No `DELETE`/`TRUNCATE` grant at all - a designation is retired via
  `status`, never removed ("a retired designation is retained, not
  deleted"). The column-level `UPDATE` grant also excludes `entry_id` -
  matching `catalogue_entry.business_key`'s own immutability treatment
  (0006) - a designation is retired and re-created on a different entry,
  never reparented.

`length` has no column here at all (FR-85/FR-24) - it is a computed
Python `@property` on the model, never persisted, so there is nothing for
this migration to create. Giving it a column at all, even one nothing
ever writes to, would leave a seam a future migration could accidentally
populate - the absence is deliberate, not an oversight to backfill later.

`ck_designation_language`'s regex is imported from `nptc_shared.language.
LANGUAGE_TAG_PATTERN` rather than hand-copied, the same
import-the-shared-source-rather-than-duplicate-it precedent this
migration already follows for `roles.GRANT_DESIGNATION_SQL` and friends
below - a hand-copied literal here previously let the deployed `CHECK`
silently drift from the model's own `_LANGUAGE_CHECK_SQL` (both built from
the same pattern, but never actually compared against each other).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles
from nptc_shared.language import LANGUAGE_TAG_PATTERN

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "designation",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("use", sa.Text(), server_default=sa.text("'synonym'"), nullable=False),
        sa.Column("language", sa.Text(), server_default=sa.text("'en-AU'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        sa.CheckConstraint(
            "use IN ('preferred','synonym')",
            name=op.f("ck_designation_use"),
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')",
            name=op.f("ck_designation_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(term)) > 0",
            name=op.f("ck_designation_term_not_blank"),
        ),
        sa.CheckConstraint(
            f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'",
            name=op.f("ck_designation_language"),
        ),
        sa.CheckConstraint(
            "NOT (use = 'preferred' AND language = 'en-AU')",
            name=op.f("ck_designation_no_en_au_preferred"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_designation_entry_id_catalogue_entry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_designation")),
    )

    op.create_index(op.f("ix_designation_entry_id"), "designation", ["entry_id"], unique=False)
    op.create_index(
        "ix_designation_one_active_preferred_per_entry_language",
        "designation",
        ["entry_id", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND use = 'preferred'"),
    )
    op.create_index(
        "ix_designation_no_duplicate_active_term",
        "designation",
        ["entry_id", "term", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.execute(roles.GRANT_DESIGNATION_SQL)
    op.execute(roles.GRANT_DESIGNATION_UPDATE_SQL)
    op.execute(roles.REVOKE_DESIGNATION_DELETE_SQL)


def downgrade() -> None:
    op.drop_table("designation")
