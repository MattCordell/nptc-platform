"""Checks a migration runs before it writes a table another role owns (FR-13).

`nptc_property_index_owner` owns `property_value`, and `nptc_indexer` is a member, so that login
can create a trigger, a rule, or a function (for a trigger, or inside an index expression) that
runs as whoever next writes the table. A migration writes it as the migration role, which is a
superuser in the compose stack. A migration that writes `property_value` therefore calls
`refuse_foreign_code_on_property_value` first, which locks the table so nothing is planted after
the check, and `test_migration_guard_coverage.py` fails when one does not.
`docs/operations/upgrade.md` states the accepted risk.

Every statement is a plain string literal (NFR-22).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection

#: Held until the migration's transaction ends. It conflicts with `CREATE TRIGGER`, `CREATE RULE`,
#: `CREATE INDEX` and `ALTER TABLE`, so nothing can be planted between the check and the write, and
#: it blocks ordinary writes too, which is why the backend is stopped for a migration.
_LOCK_SQL = sa.text("LOCK TABLE public.property_value IN SHARE ROW EXCLUSIVE MODE")
_TRIGGERS_SQL = sa.text(
    "SELECT tgname FROM pg_trigger "
    "WHERE tgrelid = 'public.property_value'::regclass AND NOT tgisinternal"
)
_RULES_SQL = sa.text(
    "SELECT rulename FROM pg_rules WHERE schemaname = 'public' AND tablename = 'property_value'"
)
#: A function owned by the index owner role or one of its members was not made by a migration.
#: `to_regrole` is null before migration 0025, which makes the comparison false, not an error.
_FUNCTIONS_SQL = sa.text(
    "SELECT p.proname FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname = 'public' AND ("
    "p.proowner = to_regrole('nptc_property_index_owner') "
    "OR p.proowner IN (SELECT member FROM pg_auth_members "
    "WHERE roleid = to_regrole('nptc_property_index_owner')))"
)


class ForeignCodeOnPropertyValueError(RuntimeError):
    """`property_value` carries code no migration created."""


def refuse_foreign_code_on_property_value(connection: Connection) -> None:
    connection.execute(_LOCK_SQL)
    triggers = list(connection.execute(_TRIGGERS_SQL).scalars())
    rules = list(connection.execute(_RULES_SQL).scalars())
    functions = list(connection.execute(_FUNCTIONS_SQL).scalars())
    if not (triggers or rules or functions):
        return
    raise ForeignCodeOnPropertyValueError(
        "property_value carries code that no migration created, and this migration would run it "
        f"as the migration role. Triggers: {triggers or 'none'}. Rules: {rules or 'none'}. "
        f"Functions owned by the index owner role or its members: {functions or 'none'}. "
        "Inspect them, drop what should not be there, then run the migration again."
    )
