"""catalogue search indexes

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-24

Issue #142 (FR-14, FR-15, FR-20): the index support behind the public
catalogue search. No table, no column and no grant changes here - purely the
normalisation function `nptc_search_text` (`nptc.db.functions`) and the two
GIN trigram indexes built over it. `pg_trgm` and `unaccent` are already
installed by `0001_extensions_and_app_role.py`; this migration would fail
loudly rather than silently degrade if either were absent.

**Why trigram, not `tsvector`.** FR-15 requires search to tolerate
typographical error, and a `tsvector` match is lexeme-equality after
stemming: `haemglobin` shares no lexeme with `haemoglobin` and scores zero.
Trigram similarity is the only mechanism in a stock PostgreSQL that ranks a
transposition or a dropped letter as a near-match at all. See
`docs/adr/0024-catalogue-search-and-pagination.md` for the alternatives
rejected (including Elasticsearch, already ruled out by ADR-0001).

**Why the function must be created before the indexes.** Both index
expressions call `nptc_search_text`, so the order here is create-function
then create-indexes, and the exact reverse on downgrade - the same
dependency shape `0008_code_binding.py` already has for
`nptc_sctid_is_valid` and its `CHECK`.

**Why the designation index is partial and the entry index is not.**
Search never matches a retired designation (a retired synonym is history,
not a way in), so `WHERE status = 'active'` keeps that index to the rows
the query can actually return. `catalogue_entry.preferred_term`'s index is
deliberately *not* partial on `status = 'active'` even though the public
API serves only active entries: the maintenance UI (#149) searches drafts
too, and a partial index would silently stop accelerating it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import functions

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Must precede both create_index calls: each index's expression calls
    # this function.
    op.execute(functions.CREATE_SEARCH_TEXT_FUNCTION_SQL)

    op.create_index(
        "ix_catalogue_entry_preferred_term_trgm",
        "catalogue_entry",
        [sa.text("nptc_search_text(preferred_term)")],
        postgresql_using="gin",
        postgresql_ops={"nptc_search_text(preferred_term)": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_designation_term_trgm",
        "designation",
        [sa.text("nptc_search_text(term)")],
        postgresql_using="gin",
        postgresql_ops={"nptc_search_text(term)": "gin_trgm_ops"},
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_designation_term_trgm", table_name="designation")
    op.drop_index("ix_catalogue_entry_preferred_term_trgm", table_name="catalogue_entry")
    op.execute(functions.DROP_SEARCH_TEXT_FUNCTION_SQL)
