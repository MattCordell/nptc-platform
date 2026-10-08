"""property_index_owner_role

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-08

FR-13, ADR-0012: moves ownership of `property_value` to a NOLOGIN role,
`nptc_property_index_owner`, so the index reconciler's login can `CREATE INDEX` and `DROP INDEX`
on that one table. Postgres has no narrower privilege than ownership for this. The role also
gets `SELECT` on `property_definition`, which the reconciler reads, and `CREATE` on schema
`public`, which Postgres checks for every new index.

**Who runs later migrations.** The migration role no longer owns `property_value`, so a later
migration that alters it must run as a role that can `SET ROLE nptc_property_index_owner`. A
superuser can, as in the compose stack. A non-superuser migration role needs
`GRANT nptc_property_index_owner TO <that role>`, once; the same grant is needed to run this
migration's `ALTER TABLE ... OWNER TO`. See `docs/operations/upgrade.md`.

**Run the downgrade as the role that created the tables.** It gives `property_value` to
`CURRENT_USER`, which is the original owner only when the original migration role runs it. A
different role, such as a superuser on a deployment with a narrower migration role, becomes the
owner instead. It does not drop the role, for the reason given in migration 0001: a role is
cluster-wide, not schema.

**A login that owns `property_value` can plant code that runs as the migration role.** A
migration that writes the table calls `nptc.db.migration_guards.refuse_foreign_code_on_property_value`
first. See `docs/operations/upgrade.md`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(roles.CREATE_PROPERTY_INDEX_OWNER_ROLE_SQL)
    op.execute(roles.TRANSFER_PROPERTY_VALUE_OWNERSHIP_SQL)
    op.execute(roles.GRANT_PROPERTY_INDEX_OWNER_PROPERTY_DEFINITION_SQL)
    op.execute(roles.GRANT_PROPERTY_INDEX_OWNER_SCHEMA_CREATE_SQL)


def downgrade() -> None:
    op.execute(roles.REVOKE_PROPERTY_INDEX_OWNER_SCHEMA_CREATE_SQL)
    op.execute(roles.REVOKE_PROPERTY_INDEX_OWNER_PROPERTY_DEFINITION_SQL)
    op.execute(roles.RESTORE_PROPERTY_VALUE_OWNERSHIP_SQL)
