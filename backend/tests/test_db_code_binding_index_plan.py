"""The `EXPLAIN` proof that `nptc.catalogue.queries.get_entry_by_code`
(issue #140, FR-17) actually reaches `ix_code_binding_system_code`, not
merely that the index exists (issue #140 review).

Every other index on `code_binding` covering `(system, code)` or `code`
alone is partial, `WHERE status = 'active'`
(`nptc.db.models.code_binding`'s own `__table_args__`) - `get_entry_by_code`
must see retired rows too (FR-08), so its `WHERE` clause carries no
`status` predicate a partial index's own predicate could be proven to
imply. `ix_code_binding_system_code` is the one non-partial index over
those columns, added for exactly this query.

**Why bulk fixtures on both sides, matching `test_db_search_index.py`'s own
reasoning.** With only a handful of rows on either side of the join, every
plan costs about the same and the planner is free to pick any of them -
which is exactly what two earlier versions of this test found in turn: the
first drove from `catalogue_entry` and never touched `code_binding`'s new
index at all; seeding thousands of *other* active entries fixed that half,
but with only one real `code_binding` row the planner then picked *some*
index scan over `code_binding` to stand in for the forbidden sequential
scan - any one would do at that row count, and it did not pick
`ix_code_binding_system_code` in particular. Only with thousands of *other*
`code_binding` rows carrying distinct codes does filtering by `(system,
code)` through that index become the one plan whose cost does not scale
with the noise - both `catalogue_entry` and `code_binding` need to be large
enough that scanning either in full is expensive, leaving "probe
`ix_code_binding_system_code` for the one matching row, then a cheap PK
join" as the only cheap plan.

**Why `enable_seqscan = off` as well.** Removes the residual cost race a
still-modest fixture would otherwise leave: an unindexable predicate cannot
become an `Index Cond` regardless of cost pressure, so disabling
sequential scans cannot manufacture a false pass.

Marked `integration`: a query plan is a database fact, with no unit-level
substitute (NFR-39).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ClauseElement, Executable

from nptc.catalogue.queries import _get_entry_by_code_statement
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM

#: Enough `active` noise entries, and enough *other* `code_binding` rows,
#: that scanning either table in full costs more than one probe of
#: `ix_code_binding_system_code` - see the module docstring. Matches
#: `test_db_search_index.py`'s own `_ROW_COUNT`/`_BINDING_COUNT`.
_NOISE_ENTRY_COUNT = 5_000
_NOISE_BINDING_COUNT = 5_000
#: Wide enough that filtering it for `nptc_sctid_is_valid` yields at least
#: `_NOISE_BINDING_COUNT` codes - roughly one candidate in ten passes the
#: Verhoeff check, matching `test_db_search_index.py`'s own
#: `_CODE_CANDIDATES` sizing.
_NOISE_CODE_CANDIDATES = 60_000

_BULK_NOISE_ENTRIES_SQL = text("""
INSERT INTO catalogue_entry (business_key, preferred_term, status)
SELECT 'NPTC-9' || lpad(g::text, 8, '0'), 'index plan noise ' || g, 'active'
FROM generate_series(1, :count) AS g
""")

#: Retired, not active: `ix_code_binding_one_active_per_entry` permits at
#: most one *active* binding per entry, but this fixture wants one binding
#: per noise entry regardless, and the query under test must see retired
#: rows too (FR-08) - a fixture built only from active rows would leave that
#: half of the predicate untested.
_BULK_NOISE_BINDINGS_SQL = text("""
INSERT INTO code_binding (entry_id, code, fsn, edition_hint, status, retirement_reason, retired_at)
SELECT
    e.id,
    c.code,
    'index plan noise ' || c.code || ' (procedure)',
    'int',
    'retired',
    'index plan noise fixture',
    now()
FROM (
    SELECT id, row_number() OVER (ORDER BY business_key) AS rn
    FROM catalogue_entry
    WHERE business_key LIKE 'NPTC-9%'
    LIMIT :bindings
) AS e
JOIN (
    SELECT g::text AS code, row_number() OVER (ORDER BY g) AS rn
    FROM generate_series(200000000, 200000000 + :candidates) AS g
    WHERE nptc_sctid_is_valid(g::text)
) AS c ON c.rn = e.rn
""")

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_BINDING = text(
    "INSERT INTO code_binding (entry_id, system, code, fsn, edition_hint, status) "
    "VALUES (:entry_id, :system, :code, :fsn, 'int', :status)"
)

#: A real, Verhoeff-valid SCTID (`391483001`, the same fixture
#: `public_catalogue_support.py`/`test_db_code_binding.py` already use) -
#: the `code` CHECK calls `nptc_sctid_is_valid`, so an invented number would
#: not insert at all. Below `200000000`, so it cannot collide with a noise
#: code generated above.
_CODE = "391483001"


class _Explain(Executable, ClauseElement):
    """`EXPLAIN <statement>`, through SQLAlchemy's normal pipeline - the
    same compiler hook `test_db_property_index_plan.py`/
    `test_db_search_index.py` each define for themselves, so a column's
    bind processor still runs rather than being bypassed the way
    `exec_driver_sql` would. `inherit_cache = False`: the wrapped
    statement's bound values differ by call."""

    inherit_cache = False

    def __init__(self, statement: ClauseElement) -> None:
        self.statement = statement


@compiles(_Explain)
def _compile_explain(element: _Explain, compiler: object, **kw: object) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)  # type: ignore[attr-defined]


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_get_entry_by_code_plans_against_the_system_code_index(db: Connection) -> None:
    """`EXPLAIN` on the exact statement `get_entry_by_code` runs (built via
    `_get_entry_by_code_statement`, not a hand-copied approximation of it -
    the same reasoning `test_db_search_index.py` gives for building through
    `search.build_search_statement`)."""
    db.execute(_BULK_NOISE_ENTRIES_SQL, {"count": _NOISE_ENTRY_COUNT})
    db.execute(
        _BULK_NOISE_BINDINGS_SQL,
        {"bindings": _NOISE_BINDING_COUNT, "candidates": _NOISE_CODE_CANDIDATES},
    )
    entry_id = db.execute(
        _INSERT_ENTRY, {"business_key": "NPTC-300001", "preferred_term": "Index plan fixture"}
    ).scalar_one()
    db.execute(
        _INSERT_BINDING,
        {
            "entry_id": entry_id,
            "system": SNOMED_CT_SYSTEM,
            "code": _CODE,
            "fsn": "x",
            "status": "active",
        },
    )
    # Statistics, or the planner works from defaults unrelated to the
    # fixture just inserted - matching `test_db_search_index.py`'s own note.
    db.execute(text("ANALYZE code_binding"))
    db.execute(text("ANALYZE catalogue_entry"))
    # LOCAL: reverted with this test's own transaction, so it cannot leak
    # into another test sharing the session-scoped container.
    db.execute(text("SET LOCAL enable_seqscan = off"))

    statement = _get_entry_by_code_statement(SNOMED_CT_SYSTEM, _CODE)
    plan = chr(10).join(db.execute(_Explain(statement)).scalars().all())

    assert (
        "Index Scan on ix_code_binding_system_code" in plan
        or "Index Scan using ix_code_binding_system_code" in plan
    ), (
        f"ix_code_binding_system_code is not in the plan - "
        f"get_entry_by_code is not index-supported\n{plan}"
    )
    assert "Index Cond: ((system = " in plan or "Index Cond: (system = " in plan, plan
