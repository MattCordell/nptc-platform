"""code_binding retired_at

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-07

Issue #140 (FR-17): a real retirement timestamp on `code_binding`, so
`nptc.catalogue.queries.get_entry_by_code` has an honest tie-break when the
same code was retired on more than one entry (a code is retired and
replaced, never rebound in place, so each retirement is a new row -
`ix_code_binding_one_active_entry_per_code` only rules out the *active*
case). See `docs/adr/0033-exact-code-lookup-routes.md` for the alternatives
considered (a real column vs. the `updated_at` proxy vs. a 409 refusing to
resolve an ambiguous retired code at all) and why this one was chosen.

`ck_code_binding_retired_at` mirrors `ck_code_binding_retirement_reason`
exactly: mandatory exactly when `status = 'retired'`, forbidden otherwise.

**Backfill before the CHECK, matching 0009/0013's own precedent.** Any
database that already holds retired bindings (this repo's own seed/test
fixtures, pre-alpha though it is) would otherwise fail this upgrade outright
- the backfill uses `updated_at` as the best available approximation for a
row that predates this column, which is exactly the proxy this column
exists to stop being *the* answer going forward.

The privilege grant is a new, separate statement
(`GRANT_CODE_BINDING_RETIRED_AT_UPDATE_SQL`), not a widened re-execution of
`GRANT_CODE_BINDING_UPDATE_SQL` - migration 0008's own statement would
otherwise try to grant a column that does not exist yet on a from-scratch
replay (`test_db_round_trip.py`'s downgrade/upgrade fingerprint test).

Downgrade drops the grant's column along with the CHECK and the column
itself - Postgres revokes a column-level privilege automatically when its
column is dropped, matching 0013's own downgrade (no explicit `REVOKE`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "code_binding", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Backfill before the CHECK below, which would otherwise fail this
    # upgrade against any database already holding a retired binding - see
    # the module docstring.
    op.execute(
        "UPDATE code_binding SET retired_at = updated_at "
        "WHERE status = 'retired' AND retired_at IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_code_binding_retired_at"),
        "code_binding",
        "(status = 'retired') = (retired_at IS NOT NULL)",
    )
    op.execute(roles.GRANT_CODE_BINDING_RETIRED_AT_UPDATE_SQL)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_code_binding_retired_at"), "code_binding", type_="check")
    op.drop_column("code_binding", "retired_at")
