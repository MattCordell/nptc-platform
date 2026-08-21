"""collision_detection

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-21

The privilege grants live in this same migration, not a later
"permissions" one - see 0002/0003/0005/0006/0007/0008's docstrings, the
same grants-live-with-the-table reasoning (ADR-0011) applies here.

Issue #49 (FR-05, FR-08): the three severities of PRD SS6.3's collision
detection.

**Error and warning severity share one comparison key, backfilled here.**
`designation.term_key` and `catalogue_entry.preferred_term_key` are added
nullable, backfilled in Python from every existing row's `term`/
`preferred_term` via `nptc_shared.similarity.collision_key` - the same
function `nptc.catalogue.collisions` and the two models' own
`@validates` hooks call, so the backfilled value can never diverge from
what a fresh insert would compute (ADR-0001/FR-74's own principle: one
implementation, not a hand-written SQL approximation of it). Only once
every row has a value do the columns become `NOT NULL`. Both carry
`server_default = ''` even after that - not because an empty string is
ever a *correct* key, but so a raw INSERT that bypasses the ORM entirely
(every `backend/tests/test_db_*.py` constraint/privilege test, none of
which know this column exists) still satisfies `NOT NULL`; every write
through `Designation`/`CatalogueEntry` themselves always supplies the
real, computed value, which overrides the default.

`ix_designation_no_duplicate_active_term` is **replaced**, not
supplemented: issue #47's version keyed on `(entry_id, term, language)`,
which only caught a byte-for-byte duplicate. Re-created here on
`(entry_id, term_key, language)` so a case/punctuation variant of an
already-active synonym on the same entry is unrepresentable too, matching
`nptc.catalogue.designations.add_synonyms`'s own dedup-before-insert
behaviour. `ix_designation_term_key` and `ix_catalogue_entry_
preferred_term_key` are plain (non-partial) btree indexes: `nptc.catalogue.
collisions` filters by entry status in the query itself, so there is no
fixed `WHERE` clause a partial index could usefully pin.

**Both new `UNIQUE` indexes are strictly narrower than what they replace
or add, and can fail against pre-existing data.** The re-keyed
`ix_designation_no_duplicate_active_term` now also catches a case/
punctuation variant that the old `(entry_id, term, language)` version
never would have; `ix_code_binding_one_active_entry_per_code` is new and
catches exactly the legacy-workbook defect FR-08 exists to fix (the same
code active on two entries). `_check_for_index_violations` below runs
after the backfill but before either index is created, so a deployment
carrying such a row gets one clear, actionable error naming every
offending group - not an opaque `IntegrityError` mid-`CREATE INDEX` with
no indication of which rows to fix. Pre-alpha, no seed data has ever been
loaded, so this has never had a real violation to catch in practice.

**Warning severity's acknowledgement is a new, narrow table -
`designation_collision_acknowledgement` - not the PRD SS6.1
`ValidationFinding` lifecycle.** That entity is P3 (`nptc.validation` is
still a placeholder); FR-05's own acknowledgement requirement cannot wait
on it. See the model's own module docstring for the full reasoning and
scope `(entry_id, term_key, language)`.

**Blocking severity closes a gap `0008` left**: `ix_code_binding_
one_active_per_entry` only rules out one entry holding two active
bindings, not one code bound active on two different entries. `ix_
code_binding_one_active_entry_per_code`, partial on `status = 'active'`
exactly like its sibling, closes that.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles
from nptc_shared.language import LANGUAGE_TAG_PATTERN
from nptc_shared.similarity import collision_key

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class CollisionMigrationError(RuntimeError):
    """Raised by `_check_for_index_violations` when pre-existing data would
    violate one of the two new `UNIQUE` indexes this migration creates -
    see the module docstring's "can fail against pre-existing data" note.
    A deliberately loud, actionable failure before `CREATE INDEX` is ever
    attempted, rather than an opaque `IntegrityError` naming a constraint
    but not the offending rows."""


def _backfill_term_keys() -> None:
    """Computes `term_key`/`preferred_term_key` for every row that
    predates this migration, via the same `collision_key` a fresh insert
    would use - see the module docstring for why this must be the real
    function, not a hand-written SQL approximation of it. A from-scratch
    test database has no rows to backfill; this is exercised for real only
    against a pre-existing deployment.

    One `executemany`-shaped call per table (a list of parameter dicts
    passed to a single `bind.execute`), not one round trip per row - the
    row-by-row shape costs nothing against an empty or small table, but
    would matter for a real catalogue's full row count."""
    bind = op.get_bind()

    designation_rows = bind.execute(sa.text("SELECT id, term FROM designation")).all()
    if designation_rows:
        bind.execute(
            sa.text("UPDATE designation SET term_key = :term_key WHERE id = :id"),
            [{"term_key": collision_key(row.term), "id": row.id} for row in designation_rows],
        )

    entry_rows = bind.execute(sa.text("SELECT id, preferred_term FROM catalogue_entry")).all()
    if entry_rows:
        bind.execute(
            sa.text(
                "UPDATE catalogue_entry SET preferred_term_key = :preferred_term_key WHERE id = :id"
            ),
            [
                {"preferred_term_key": collision_key(row.preferred_term), "id": row.id}
                for row in entry_rows
            ],
        )


def _check_for_index_violations() -> None:
    """Raises `CollisionMigrationError` naming every group of rows that
    would violate `ix_designation_no_duplicate_active_term` (re-keyed) or
    `ix_code_binding_one_active_entry_per_code` (new) - see the module
    docstring. Run after the backfill (so `term_key` is populated) but
    before either index is created."""
    bind = op.get_bind()
    problems: list[str] = []

    duplicate_terms = bind.execute(
        sa.text(
            "SELECT entry_id, term_key, language, COUNT(*) AS n FROM designation "
            "WHERE status = 'active' GROUP BY entry_id, term_key, language HAVING COUNT(*) > 1"
        )
    ).all()
    for row in duplicate_terms:
        problems.append(
            f"designation: entry {row.entry_id} has {row.n} active synonyms folding to the "
            f"same comparison key {row.term_key!r} in language {row.language!r} - retire or "
            "reword all but one before upgrading"
        )

    duplicate_codes = bind.execute(
        sa.text(
            "SELECT system, code, COUNT(*) AS n FROM code_binding "
            "WHERE status = 'active' GROUP BY system, code HAVING COUNT(*) > 1"
        )
    ).all()
    for row in duplicate_codes:
        problems.append(
            f"code_binding: code {row.code!r} on {row.system!r} is actively bound to {row.n} "
            "entries - retire all but one binding before upgrading"
        )

    if problems:
        raise CollisionMigrationError(
            "cannot create issue #49's collision-detection indexes against existing data "
            "that would violate them:\n" + "\n".join(f"  - {p}" for p in problems)
        )


def upgrade() -> None:
    op.add_column(
        "designation",
        sa.Column("term_key", sa.Text(), server_default=sa.text("''"), nullable=True),
    )
    op.add_column(
        "catalogue_entry",
        sa.Column("preferred_term_key", sa.Text(), server_default=sa.text("''"), nullable=True),
    )

    _backfill_term_keys()
    _check_for_index_violations()

    op.alter_column("designation", "term_key", nullable=False)
    op.alter_column("catalogue_entry", "preferred_term_key", nullable=False)

    op.drop_index("ix_designation_no_duplicate_active_term", table_name="designation")
    op.create_index(
        "ix_designation_no_duplicate_active_term",
        "designation",
        ["entry_id", "term_key", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_designation_term_key", "designation", ["term_key"], unique=False)
    op.create_index(
        op.f("ix_catalogue_entry_preferred_term_key"),
        "catalogue_entry",
        ["preferred_term_key"],
        unique=False,
    )

    op.create_index(
        "ix_code_binding_one_active_entry_per_code",
        "code_binding",
        ["system", "code"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.execute(roles.GRANT_DESIGNATION_TERM_KEY_UPDATE_SQL)
    op.execute(roles.GRANT_CATALOGUE_ENTRY_PREFERRED_TERM_KEY_UPDATE_SQL)

    op.create_table(
        "designation_collision_acknowledgement",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("term_key", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), server_default=sa.text("'en-AU'"), nullable=False),
        sa.Column("acknowledged_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(term_key)) > 0",
            name=op.f("ck_designation_collision_acknowledgement_term_key_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name=op.f("ck_designation_collision_acknowledgement_reason_not_blank"),
        ),
        sa.CheckConstraint(
            f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'",
            name=op.f("ck_designation_collision_acknowledgement_language"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_designation_collision_acknowledgement_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_designation_collision_acknowledgement_acknowledged_by_user_id_app_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_designation_collision_acknowledgement")),
    )

    op.create_index(
        op.f("ix_designation_collision_acknowledgement_entry_id"),
        "designation_collision_acknowledgement",
        ["entry_id"],
        unique=False,
    )
    op.create_index(
        "ix_designation_collision_ack_entry_term_language",
        "designation_collision_acknowledgement",
        ["entry_id", "term_key", "language"],
        unique=True,
    )

    op.execute(roles.GRANT_DESIGNATION_COLLISION_ACK_SQL)
    op.execute(roles.REVOKE_DESIGNATION_COLLISION_ACK_WRITE_SQL)


def downgrade() -> None:
    op.drop_table("designation_collision_acknowledgement")

    op.drop_index("ix_code_binding_one_active_entry_per_code", table_name="code_binding")

    op.drop_index(op.f("ix_catalogue_entry_preferred_term_key"), table_name="catalogue_entry")
    op.drop_index("ix_designation_term_key", table_name="designation")
    op.drop_index("ix_designation_no_duplicate_active_term", table_name="designation")
    op.create_index(
        "ix_designation_no_duplicate_active_term",
        "designation",
        ["entry_id", "term", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.drop_column("catalogue_entry", "preferred_term_key")
    op.drop_column("designation", "term_key")
