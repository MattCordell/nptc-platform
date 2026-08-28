"""numeric_or_null_function

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-27

Issue #54 (FR-13): the cast-safe numeric expression ADR-0012's third index
shape needs. No table, no column and no grant changes here - purely the
`nptc_numeric_or_null` function (`nptc.db.functions`), which
`nptc.db.property_indexes.create_statement` uses to build the expression
index a filterable `decimal`/`positiveInt` property gets. See
`docs/adr/0027-cast-safe-numeric-index-expression.md` for why a plain
`((value #>> '{}')::numeric)` expression index is unsafe (a retained
non-conforming value makes `CREATE INDEX` fail outright) and why this
function exists in the database at all rather than in application code -
an expression index's expression must itself be `IMMUTABLE`.

No index is created by this migration. The reconciler (`nptc.db.
property_reconciler`) creates a property's index at runtime, once it is
flagged filterable - this migration only makes the function available for
it to reference.

**Downgrade caveat, documented rather than guarded against here**: Postgres
tracks a dependency from a generated expression index to this function, so
`DROP FUNCTION` fails if a reconciler-built numeric-shaped index still
exists at downgrade time. Unlike `0012_catalogue_search_indexes.py`'s
`nptc_search_text` pairing, the dependent indexes here are not created by
a migration, so this one cannot drop them itself before dropping the
function - an operator downgrading past this revision must first reconcile
every numeric-shaped filterable property back to `filterable = false` (see
`docs/operations/upgrade.md`).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from nptc.db import functions

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(functions.CREATE_NUMERIC_OR_NULL_FUNCTION_SQL)


def downgrade() -> None:
    op.execute(functions.DROP_NUMERIC_OR_NULL_FUNCTION_SQL)
