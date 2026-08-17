"""audit_event hash chain

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

Adds ``prev_hash``/``entry_hash`` (NFR-10) to ``audit_event`` - see
``nptc.audit.hashing``/``nptc.audit.writer`` for the digest construction
and append sequence, and ``docs/architecture/data-model.md`` for the full
design writeup.

Both columns are ``TEXT NOT NULL`` with **no server default**: there is no
way to invent a hash for a pre-existing row, so `op.add_column` here is
only ever valid against an empty ``audit_event`` table (Postgres raises
``23502`` otherwise). Pre-alpha, no write path has ever run against this
table, so that is honest rather than awkward - see
docs/operations/upgrade.md for the explicit note to a future operator.

No new `GRANT` is needed: table-level `SELECT, INSERT` (0002_audit_event.py)
already covers columns added later.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("prev_hash", sa.Text(), nullable=False))
    op.add_column("audit_event", sa.Column("entry_hash", sa.Text(), nullable=False))
    op.create_check_constraint(
        op.f("ck_audit_event_prev_hash_hex"), "audit_event", "prev_hash ~ '^[0-9a-f]{64}$'"
    )
    op.create_check_constraint(
        op.f("ck_audit_event_entry_hash_hex"), "audit_event", "entry_hash ~ '^[0-9a-f]{64}$'"
    )
    op.create_unique_constraint(op.f("uq_audit_event_entry_hash"), "audit_event", ["entry_hash"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_audit_event_entry_hash"), "audit_event", type_="unique")
    op.drop_constraint(op.f("ck_audit_event_entry_hash_hex"), "audit_event", type_="check")
    op.drop_constraint(op.f("ck_audit_event_prev_hash_hex"), "audit_event", type_="check")
    op.drop_column("audit_event", "entry_hash")
    op.drop_column("audit_event", "prev_hash")
