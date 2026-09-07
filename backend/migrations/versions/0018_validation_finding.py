"""validation_finding

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-07

Issue #141 (FR-18, FR-45, FR-55): PRD SS6.1's `CatalogueEntry --<
ValidationFinding (open / acknowledged / resolved / superseded)`, landed
minimal and read-only ahead of the P3 sweep that will populate it - see
`nptc.db.models.validation_finding`'s own module docstring for the full
reasoning, and `nptc.db.roles.GRANT_VALIDATION_FINDING_SQL`'s comment for
why the grant is SELECT-only rather than this file's usual SELECT+INSERT.

Also adds `ix_audit_event_entity_type_entity_id_sequence` (PR #278 review):
`nptc.catalogue.history.load_history`'s public, unbounded read of
`audit_event` had no supporting index until this - see
`nptc.db.models.audit.AuditEvent.__table_args__`'s own comment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validation_finding",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=True),
        sa.Column("finding_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
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
            "finding_type IN ('code_not_found', 'code_inactive', 'fsn_drift', "
            "'preferred_term_drift', 'replacement_available', 'out_of_scope_hierarchy', "
            "'unexpected_semantic_tag', 'binding_violation', 'local_code_retired')",
            name=op.f("ck_validation_finding_finding_type"),
        ),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name=op.f("ck_validation_finding_severity"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'superseded')",
            name=op.f("ck_validation_finding_status"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_validation_finding_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["code_binding.id"],
            name=op.f("fk_validation_finding_binding_id_code_binding"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_finding")),
    )

    op.create_index(
        "ix_validation_finding_open_entry_id",
        "validation_finding",
        ["entry_id"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.execute(roles.GRANT_VALIDATION_FINDING_SQL)
    op.execute(roles.REVOKE_VALIDATION_FINDING_WRITE_SQL)

    op.create_index(
        "ix_audit_event_entity_type_entity_id_sequence",
        "audit_event",
        ["entity_type", "entity_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_entity_type_entity_id_sequence", table_name="audit_event")
    op.drop_table("validation_finding")
