"""retire_specimen_unconstrained

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-07

FR-89, ADR-0044: `Any` is the specimen code `123038009` alone, so the `specimen_unconstrained`
flag that carried "accepts any specimen" has nothing left to say and is dropped.

**The flag converts before it goes.** Each entry flagged `true` that holds no specimen value gets
one: `{"system": "http://snomed.info/sct", "code": "123038009", "display": "Any"}`, the value a
fresh seed of the workbook's `Any` writes. An entry that holds named specimens and the flag (the
old FR-89 guard refused that pair, but seeded or direct-SQL rows could hold it) keeps its named
specimens, because the two cannot be merged. The upgrade logs a warning with their business keys
before the column goes, so an operator can review each one.

**Raw SQL, no audit event, `row_version` untouched.** As migrations 0009, 0013 and 0023 do: a
migration is not an application write path, `row_version` has one write path (the ORM's), and a
migration that imported the write path would break the first time a later revision changed it.
The entry's history therefore shows no event for this change. The migration runs before the
backend starts, so no editor holds a stale lock token against it.

The upgrade refuses, with a message, if an entry needs the conversion and the `specimen`
property definition is absent. The loader creates that definition before it writes any entry, so
a catalogue with entries has it.

Downgrade re-adds the column and puts back what the upgrade took: an entry whose only specimen
is the root becomes flagged again, and that value is removed, which restores the old rule that
an entry holds the flag or specimens, never both. The column's `UPDATE` grant for `nptc_app` is
re-granted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LISTED_KEYS = 50
_log = logging.getLogger("alembic.runtime.migration")

_ROOT_VALUE = json.dumps(
    {"system": "http://snomed.info/sct", "code": "123038009", "display": "Any"}
)

_COUNT_WAITING = (
    "SELECT count(*) FROM catalogue_entry e WHERE e.specimen_unconstrained "
    "AND NOT EXISTS (SELECT 1 FROM property_value pv "
    "WHERE pv.entry_id = e.id AND pv.property_key = 'specimen')"
)
_CONVERT = (
    "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
    "SELECT e.id, 'specimen', 0, CAST(:value AS jsonb) "
    "FROM catalogue_entry e WHERE e.specimen_unconstrained "
    "AND NOT EXISTS (SELECT 1 FROM property_value pv "
    "WHERE pv.entry_id = e.id AND pv.property_key = 'specimen')"
)
_LOSING_THE_FLAG = (
    "SELECT e.business_key FROM catalogue_entry e WHERE e.specimen_unconstrained "
    "AND EXISTS (SELECT 1 FROM property_value pv "
    "WHERE pv.entry_id = e.id AND pv.property_key = 'specimen') ORDER BY e.business_key"
)
_FLAG_ROOT_ONLY = (
    "UPDATE catalogue_entry SET specimen_unconstrained = true WHERE id IN ("
    "SELECT entry_id FROM property_value WHERE property_key = 'specimen' "
    "GROUP BY entry_id HAVING count(*) = 1 AND bool_and(value ->> 'code' = '123038009'))"
)
_DELETE_ROOT_ONLY = (
    "DELETE FROM property_value WHERE property_key = 'specimen' AND entry_id IN ("
    "SELECT entry_id FROM property_value WHERE property_key = 'specimen' "
    "GROUP BY entry_id HAVING count(*) = 1 AND bool_and(value ->> 'code' = '123038009'))"
)


def upgrade() -> None:
    connection = op.get_bind()
    losing: list[str] = list(connection.execute(sa.text(_LOSING_THE_FLAG)).scalars())
    if losing:
        _log.warning(
            "%d entries were marked as accepting any specimen and also hold named specimens. "
            "The mark is dropped and the named specimens are kept: %s",
            len(losing),
            ", ".join(losing[:_LISTED_KEYS]) + (", ..." if len(losing) > _LISTED_KEYS else ""),
        )
    waiting: int = connection.execute(sa.text(_COUNT_WAITING)).scalar_one()
    if waiting:
        defined: int = connection.execute(
            sa.text("SELECT count(*) FROM property_definition WHERE key = 'specimen'")
        ).scalar_one()
        if not defined:
            raise RuntimeError(
                f"{waiting} entries are marked as accepting any specimen, but the 'specimen' "
                "property definition does not exist, so their specimen cannot be recorded"
            )
        connection.execute(sa.text(_CONVERT), {"value": _ROOT_VALUE})
    op.drop_column("catalogue_entry", "specimen_unconstrained")


def downgrade() -> None:
    op.add_column(
        "catalogue_entry",
        sa.Column(
            "specimen_unconstrained",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(_FLAG_ROOT_ONLY)
    op.execute(_DELETE_ROOT_ONLY)
    op.execute(roles.GRANT_CATALOGUE_ENTRY_SPECIMEN_UNCONSTRAINED_UPDATE_SQL)
