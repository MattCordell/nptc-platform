"""FR-09's own acceptance test (issue #51, ADR-0012): "no migration, no
restart, no deployment" is the whole claim of the JSONB storage design,
so it is verified here against a running application, not inferred from
the schema being capable of it in principle.

`app_engine` is a single, already-migrated, session-scoped `Engine` -
every other test module in this suite shares it, and this test opens no
migration and calls nothing under `alembic`. Two independent connections
are drawn from that one already-running engine: the first defines a new
property (something no migration in this repository has ever named), the
second - a genuinely separate connection, standing in for a second
request handled by the same running process - immediately records and
reads back a value for it. Nothing in between restarts the process or
runs a migration; the property is usable the moment its `INSERT`
commits, because storing a new property is ordinary `property_definition`
/`property_value` row data, never a schema change.

Both connections issue real, committed `INSERT`s (unlike every other test
in this suite, which runs inside a `db`/`app_db` transaction rolled back
at teardown) - that is the point: a rolled-back write would not prove
anything usable by a genuinely separate later connection. A `finally`
block cleans both rows up via `owner_engine` (the owner role, since
`nptc_app` has no `DELETE` grant on `property_definition` at all - FR-11),
so this test leaves the shared container exactly as it found it for every
other test in the run, matching issue #190's no-cross-test-pollution rule.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_a_new_property_is_usable_with_no_migration_and_no_restart(
    app_engine: Engine, owner_engine: Engine
) -> None:
    try:
        with app_engine.connect() as defining_connection:
            defining_connection.execute(
                text(
                    "INSERT INTO property_definition "
                    "(key, label, datatype, cardinality, scope, required_for_submission, "
                    "required_for_publication, filterable, origin, display_order) "
                    "VALUES ('novel_property', 'Novel property', 'string', '0..1', 'both', "
                    "false, false, false, 'admin', 0)"
                )
            )
            defining_connection.commit()

        # A second, independent connection off the same already-running engine -
        # no migration ran and no process restarted between the two blocks.
        with app_engine.connect() as reading_connection:
            entry_id = reading_connection.execute(
                text(
                    "INSERT INTO catalogue_entry (business_key, preferred_term) "
                    "VALUES ('NPTC-600001', 'Sample entry') RETURNING id"
                )
            ).scalar_one()
            reading_connection.execute(
                text(
                    "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
                    "VALUES (:entry_id, 'novel_property', 0, '\"first value\"')"
                ),
                {"entry_id": entry_id},
            )
            row = reading_connection.execute(
                text(
                    "SELECT value FROM property_value "
                    "WHERE entry_id = :entry_id AND property_key = 'novel_property'"
                ),
                {"entry_id": entry_id},
            ).one()
            reading_connection.commit()

        assert row.value == "first value"
    finally:
        with owner_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                text("DELETE FROM property_value WHERE property_key = 'novel_property'")
            )
            cleanup_connection.execute(
                text("DELETE FROM catalogue_entry WHERE business_key = 'NPTC-600001'")
            )
            cleanup_connection.execute(
                text("DELETE FROM property_definition WHERE key = 'novel_property'")
            )
            cleanup_connection.commit()
