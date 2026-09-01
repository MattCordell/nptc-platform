"""hybrid search indexes

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-02

Issue #138 (FR-14, FR-15): the index support behind the second half of
catalogue search. `0012_catalogue_search_indexes.py` covered two of FR-14's
five searchable fields with trigram similarity alone; this migration adds the
remaining three fields and the full-text half of the ranking. No table, no
column and no grant changes here - two functions and seven indexes.

**What changed since 0012, and why this is not a reversal of it.** 0012's
docstring argues "why trigram, not `tsvector`", and that argument still
holds on its own terms: a `tsvector` match is lexeme equality after stemming,
so `haemglobin` shares no lexeme with `haemoglobin` and scores zero. Nothing
here weakens that. What ADR-0029 settles is that the two mechanisms are
complementary rather than alternative - full-text is the half that matches an
inflected or pluralised form, which trigram scores as a near-miss, and
trigram is the half that survives a typo, which full-text cannot see at all.
`nptc.catalogue.search._SEARCH_SQL` scans both and keeps the better score per
entry, so the trigram indexes 0012 created are still load-bearing and are
left exactly as they are.

**Why the function pair must be created before the indexes.** Four of the
seven index expressions call `nptc_search_document`, which in turn calls
0012's `nptc_search_text`, so the order is create-functions then
create-indexes and the exact reverse on downgrade - the same dependency shape
0012 already has, and 0008 before it for `nptc_sctid_is_valid`.
`nptc_search_query` is created here too even though no index references it:
it is the query-side half of the same pair, and splitting the two across
migrations would let a deployment exist in which documents and queries are
lexed by configurations that were never introduced together.

**Why every `code_binding` index is partial and the entry-side ones are
not.** Identical to 0012's split. A retired binding is history rather than a
way into the catalogue, exactly as a retired designation is, so those five
indexes cover only the rows a result can come from.
`catalogue_entry.preferred_term`'s pair stays non-partial because #149's
maintenance UI searches drafts.

**`ix_code_binding_code` is a btree, not a trigram index.** The code is
matched by equality - see ADR-0029 on why fuzzy code matching was rejected.
The existing `ix_code_binding_one_active_entry_per_code` cannot serve this
lookup even though it indexes the same column: `code` is its second column
behind `system`, and the search box has no `system` to supply.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import functions

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every `code_binding` index this migration creates is scoped to active
#: bindings. A module constant rather than five copies of the same string,
#: so the model's `postgresql_where` and this migration cannot drift apart in
#: four places at once - and a plain literal, since
#: `test_sql_parameterisation.py`'s AST guard rejects an f-string reaching
#: `sa.text(...)` even when every interpolated value is a constant.
_ACTIVE_ONLY = "status = 'active'"


def upgrade() -> None:
    # Must precede every create_index below: four of them inline
    # `nptc_search_document`, which itself calls 0012's `nptc_search_text`.
    op.execute(functions.CREATE_SEARCH_DOCUMENT_FUNCTION_SQL)
    op.execute(functions.CREATE_SEARCH_QUERY_FUNCTION_SQL)

    op.create_index(
        "ix_catalogue_entry_preferred_term_fts",
        "catalogue_entry",
        [sa.text("nptc_search_document(preferred_term)")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_designation_term_fts",
        "designation",
        [sa.text("nptc_search_document(term)")],
        postgresql_using="gin",
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )

    op.create_index(
        "ix_code_binding_code",
        "code_binding",
        ["code"],
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )
    op.create_index(
        "ix_code_binding_fsn_trgm",
        "code_binding",
        [sa.text("nptc_search_text(fsn)")],
        postgresql_using="gin",
        postgresql_ops={"nptc_search_text(fsn)": "gin_trgm_ops"},
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )
    op.create_index(
        "ix_code_binding_fsn_fts",
        "code_binding",
        [sa.text("nptc_search_document(fsn)")],
        postgresql_using="gin",
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )
    op.create_index(
        "ix_code_binding_au_preferred_term_trgm",
        "code_binding",
        [sa.text("nptc_search_text(au_preferred_term)")],
        postgresql_using="gin",
        postgresql_ops={"nptc_search_text(au_preferred_term)": "gin_trgm_ops"},
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )
    op.create_index(
        "ix_code_binding_au_preferred_term_fts",
        "code_binding",
        [sa.text("nptc_search_document(au_preferred_term)")],
        postgresql_using="gin",
        postgresql_where=sa.text(_ACTIVE_ONLY),
    )


def downgrade() -> None:
    # Exact reverse of upgrade: every dependent index dropped before the
    # functions their expressions reference. PostgreSQL tracks the
    # dependency and would refuse the `DROP FUNCTION` otherwise.
    op.drop_index("ix_code_binding_au_preferred_term_fts", table_name="code_binding")
    op.drop_index("ix_code_binding_au_preferred_term_trgm", table_name="code_binding")
    op.drop_index("ix_code_binding_fsn_fts", table_name="code_binding")
    op.drop_index("ix_code_binding_fsn_trgm", table_name="code_binding")
    op.drop_index("ix_code_binding_code", table_name="code_binding")
    op.drop_index("ix_designation_term_fts", table_name="designation")
    op.drop_index("ix_catalogue_entry_preferred_term_fts", table_name="catalogue_entry")

    op.execute(functions.DROP_SEARCH_QUERY_FUNCTION_SQL)
    op.execute(functions.DROP_SEARCH_DOCUMENT_FUNCTION_SQL)
