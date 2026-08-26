"""property_definition_local_code_system_key

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-27

Issue #52 (FR-09, FR-10): names *which* governed `local_code_system` a
`binding_target = 'local_code_system'` property (Discipline, Subgroup) is
bound to - the gap #51/ADR-0012 left open. `#51`'s own docstring on
`property_definition.local_code_system_key` explains why this was a stopgap
rather than an oversight: `local_code_system` (issue #56) did not exist yet
when #51 landed, so the column could not be a real FK at that point. It has
since landed, so this migration adds a real `FOREIGN KEY`, not another
FK-less placeholder.

**Mirrors `value_set_uri_required`'s own shape.** A new `CHECK`,
`local_code_system_key_required`, makes "a `local_code_system` binding
always names its system" a schema invariant the same way
`value_set_uri_required` already does for the `value_set` case -
`IS DISTINCT FROM`, not `<>`, for the same NULL-safety reason that
constraint's own comment gives. `binding_fields_require_target` (the
CHECK closing the *other* direction - no stray binding data on a non-code
property) is dropped and recreated with `local_code_system_key` added to
its `AND`-chain, since Alembic has no "ALTER CHECK" - the old constraint is
dropped by name and the new one created under the same name, matching
`0008_code_binding.py`'s own precedent for widening a `CHECK`.

**The privilege grant is a new, separate statement, not a widened
re-execution of `GRANT_PROPERTY_DEFINITION_UPDATE_SQL`.** That constant is
migration 0010's own statement, replayed verbatim by 0010 on every fresh
migrate from empty - widening it in place would make 0010 try to grant
`local_code_system_key` before this migration has added the column, which
is exactly the failure a `uv run pytest` from a clean container caught.
`GRANT_PROPERTY_DEFINITION_LOCAL_CODE_SYSTEM_KEY_UPDATE_SQL` grants only
the one column this migration adds.

Downgrade drops the FK, the two CHECKs, restores `binding_fields_require_
target` to its pre-#52 form, and drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_BINDING_FIELDS_REQUIRE_TARGET_SQL = (
    "binding_target IS NOT NULL OR (value_set_uri IS NULL AND strength IS NULL AND edition IS NULL)"
)
_NEW_BINDING_FIELDS_REQUIRE_TARGET_SQL = (
    "binding_target IS NOT NULL OR (value_set_uri IS NULL AND strength IS NULL "
    "AND edition IS NULL AND local_code_system_key IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "property_definition", sa.Column("local_code_system_key", sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        op.f("local_code_system_key_local_code_system"),
        "property_definition",
        "local_code_system",
        ["local_code_system_key"],
        ["key"],
    )
    op.create_check_constraint(
        op.f("ck_property_definition_local_code_system_key_required"),
        "property_definition",
        "binding_target IS DISTINCT FROM 'local_code_system' OR local_code_system_key IS NOT NULL",
    )
    op.drop_constraint(
        op.f("ck_property_definition_binding_fields_require_target"),
        "property_definition",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_property_definition_binding_fields_require_target"),
        "property_definition",
        _NEW_BINDING_FIELDS_REQUIRE_TARGET_SQL,
    )
    op.execute(roles.GRANT_PROPERTY_DEFINITION_LOCAL_CODE_SYSTEM_KEY_UPDATE_SQL)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_property_definition_binding_fields_require_target"),
        "property_definition",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_property_definition_binding_fields_require_target"),
        "property_definition",
        _OLD_BINDING_FIELDS_REQUIRE_TARGET_SQL,
    )
    op.drop_constraint(
        op.f("ck_property_definition_local_code_system_key_required"),
        "property_definition",
        type_="check",
    )
    op.drop_constraint(
        op.f("local_code_system_key_local_code_system"),
        "property_definition",
        type_="foreignkey",
    )
    op.drop_column("property_definition", "local_code_system_key")
