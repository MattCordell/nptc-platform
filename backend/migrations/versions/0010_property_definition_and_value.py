"""property_definition_and_value

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-21

The privilege grants live in this same migration, not a later
"permissions" one - see 0002/.../0009's docstrings, the same
grants-live-with-the-table reasoning (ADR-0011) applies here.

Issue #51 (FR-09, FR-10, FR-11, FR-12): the property registry's storage
layer, per ADR-0012 - see that ADR for the full design record and why
each shape below was chosen over the alternatives it names.

`property_value.entry_id` references `catalogue_entry.id`: ADR-0012 flagged
this FK as unavailable when it was written (`catalogue_entry` had not yet
landed), but #46 has since merged, so the FK is added directly here rather
than deferred to a follow-on migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "property_definition",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "index_seq",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("datatype", sa.Text(), nullable=False),
        sa.Column("cardinality", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("required_for_submission", sa.Boolean(), nullable=False),
        sa.Column("required_for_publication", sa.Boolean(), nullable=False),
        sa.Column("binding_target", sa.Text(), nullable=True),
        sa.Column("value_set_uri", sa.Text(), nullable=True),
        sa.Column("strength", sa.Text(), nullable=True),
        sa.Column("edition", sa.Text(), nullable=True),
        sa.Column("filterable", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]{0,62}$'", name=op.f("ck_property_definition_key")
        ),
        sa.CheckConstraint(
            "cardinality IN ('0..1','1..1','0..*','1..*')",
            name=op.f("ck_property_definition_cardinality"),
        ),
        sa.CheckConstraint(
            "scope IN ('entry','designation')", name=op.f("ck_property_definition_scope")
        ),
        sa.CheckConstraint(
            "origin IN ('system','admin')", name=op.f("ck_property_definition_origin")
        ),
        sa.CheckConstraint(
            "status IN ('active','deprecated')", name=op.f("ck_property_definition_status")
        ),
        sa.CheckConstraint(
            "binding_target IN ('value_set','local_code_system')",
            name=op.f("ck_property_definition_binding_target"),
        ),
        sa.CheckConstraint(
            "strength IN ('required','extensible','example')",
            name=op.f("ck_property_definition_strength"),
        ),
        sa.CheckConstraint(
            "(datatype = 'code') = (binding_target IS NOT NULL)",
            name=op.f("ck_property_definition_binding_required_for_code"),
        ),
        sa.CheckConstraint(
            "binding_target IS DISTINCT FROM 'value_set' OR value_set_uri IS NOT NULL",
            name=op.f("ck_property_definition_value_set_uri_required"),
        ),
        sa.CheckConstraint(
            "(status = 'deprecated') = (deprecated_at IS NOT NULL)",
            name=op.f("ck_property_definition_deprecated_at_required"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_property_definition")),
        sa.UniqueConstraint("key", name=op.f("uq_property_definition_key")),
        sa.UniqueConstraint("index_seq", name=op.f("uq_property_definition_index_seq")),
    )

    op.create_table(
        "property_value",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("property_key", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_property_value_ordinal_non_negative")),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_property_value_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["property_key"],
            ["property_definition.key"],
            name=op.f("fk_property_value_property_key_property_definition"),
        ),
        sa.PrimaryKeyConstraint(
            "entry_id", "property_key", "ordinal", name=op.f("pk_property_value")
        ),
    )

    op.execute(roles.GRANT_PROPERTY_DEFINITION_SQL)
    op.execute(roles.GRANT_PROPERTY_DEFINITION_UPDATE_SQL)
    op.execute(roles.REVOKE_PROPERTY_DEFINITION_DELETE_SQL)
    op.execute(roles.GRANT_PROPERTY_VALUE_SQL)
    op.execute(roles.REVOKE_PROPERTY_VALUE_TRUNCATE_SQL)


def downgrade() -> None:
    op.drop_table("property_value")
    op.drop_table("property_definition")
