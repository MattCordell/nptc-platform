"""specimen_binding_includes_root

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-07

FR-88, FR-89, ADR-0044: `Any` is the specimen code `123038009`, which `<123038009` refuses
because it selects descendants only. The `specimen` property is now bound to `<<123038009`, and
its `forbidden_codes` constraint (the old FR-89 refusal of the literal `Any`) goes.

**Why a migration.** `seed_system_properties` inserts only the definitions that are missing, so a
database that already holds the `specimen` definition would keep the old binding. The update is
raw SQL, as migration 0013's backfill is: `row_version` has one write path, the ORM's, and the API
cannot amend a binding. No audit event is written, because the audit writer is application code.

**Guarded.** The `UPDATE` matches the system `specimen` definition only while it still holds the
old binding, so an administrator's own change is not overwritten. `forbidden_codes` is removed
only when it is exactly the old `["Any"]`.

Downgrade restores the old binding and constraint. It would not be valid for an entry that holds
the root as a specimen value, so a downgrade is for an empty catalogue or a rehearsal.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
_NEW_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009"
_OLD_REFUSAL = '{"forbidden_codes": ["Any"]}'


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE property_definition SET value_set_uri = :new_uri, "
            "constraints = CASE WHEN constraints -> 'forbidden_codes' = "
            "CAST(:old_refusal AS jsonb) -> 'forbidden_codes' "
            "THEN constraints - 'forbidden_codes' ELSE constraints END, "
            "updated_at = clock_timestamp() "
            "WHERE key = 'specimen' AND origin = 'system' AND value_set_uri = :old_uri"
        ),
        {"new_uri": _NEW_URI, "old_uri": _OLD_URI, "old_refusal": _OLD_REFUSAL},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE property_definition SET value_set_uri = :old_uri, "
            "constraints = constraints || CAST(:old_refusal AS jsonb), "
            "updated_at = clock_timestamp() "
            "WHERE key = 'specimen' AND origin = 'system' AND value_set_uri = :new_uri"
        ),
        {"new_uri": _NEW_URI, "old_uri": _OLD_URI, "old_refusal": _OLD_REFUSAL},
    )
