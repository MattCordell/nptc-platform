"""designation retired_at

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-11

Issue #313: a real retirement timestamp on `designation`, mirroring
`code_binding.retired_at` (migration 0016, issue #140, FR-17) exactly. It
gives the coming `reinstate_designation` path an honest tie-break when two
retired rows share one `(entry_id, term_key, language)` - a term added,
retired, and re-added twice over - which the plan for #313 settled on as
"most-recently-retired first" rather than leaving the choice to
`updated_at` (which moves on any column update, not only a retirement).

`ck_designation_retired_at` mirrors `ck_code_binding_retired_at` exactly:
mandatory exactly when `status = 'retired'`, forbidden otherwise.

**Backfill before the CHECK, matching 0016's own precedent.** Any database
that already holds retired designations (this repo's own seed/test
fixtures, pre-alpha though it is) would otherwise fail this upgrade outright
- the backfill uses `updated_at` as the best available approximation for a
row that predates this column, exactly the proxy this column exists to
stop being *the* answer going forward.

The privilege grant is a new, separate statement
(`GRANT_DESIGNATION_RETIRED_AT_UPDATE_SQL`), not a widened re-execution of
`GRANT_DESIGNATION_UPDATE_SQL` - migration 0007's own statement would
otherwise try to grant a column that does not exist yet on a from-scratch
replay (`test_db_round_trip.py`'s downgrade/upgrade fingerprint test).

Downgrade drops the grant's column along with the CHECK and the column
itself - Postgres revokes a column-level privilege automatically when its
column is dropped, matching 0016's own downgrade (no explicit `REVOKE`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "designation", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Backfill before the CHECK below, which would otherwise fail this
    # upgrade against any database already holding a retired designation -
    # see the module docstring.
    op.execute(
        "UPDATE designation SET retired_at = updated_at "
        "WHERE status = 'retired' AND retired_at IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_designation_retired_at"),
        "designation",
        "(status = 'retired') = (retired_at IS NOT NULL)",
    )
    op.execute(roles.GRANT_DESIGNATION_RETIRED_AT_UPDATE_SQL)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_designation_retired_at"), "designation", type_="check")
    op.drop_column("designation", "retired_at")
