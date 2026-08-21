"""The database's Verhoeff check-digit function for SNOMED CT identifiers
(FR-06, issue #48).

PRD 14.1 bans business logic living in database triggers/functions because it
is invisible to the test suite and to code review. This function is a
deliberate, narrow exception, not a silent precedent - see
``docs/adr/0023-database-level-sctid-validation.md`` for the full argument.
The short version: FR-06 requires the *column itself* to carry a check
constraint enforcing format **and** Verhoeff, and a Postgres ``CHECK``
constraint permits no subquery or CTE - so the Verhoeff fold (a table lookup
per digit, against the Verhoeff D5 dihedral-group tables) cannot be spelled
as a practical inline boolean expression the way ``ck_designation_language``
manages for a plain regex. A ``CHECK`` may reference a function, so the fold
has to live in one.

Three things keep this from being the kind of stored logic PRD 14.1 actually
warns against - invisible, untested, undiscoverable:

- It is a pure predicate with no side effects (``LANGUAGE sql IMMUTABLE
  STRICT``), not a trigger, and it decides validity, never behaviour.
- It is defined here, in a versioned ``backend/src`` module and a migration,
  not typed once into a psql session - discoverable exactly the way
  ``nptc.db.roles``'s grant SQL already is.
- ``backend/tests/test_db_sctid_function.py`` proves it agrees with
  ``nptc_shared.sctid.has_valid_format``/``has_valid_check_digit`` over an
  exhaustive corpus, so a future edit to either side that lets them diverge
  fails CI rather than shipping unnoticed.

The ``_D``/``_P`` tables below are the same Verhoeff D5 multiplication and
position-permutation tables as ``nptc_shared.sctid._D``/``_P``, transliterated
to a Postgres two-dimensional integer array literal (1-based indexing, hence
the ``+ 1`` throughout the recursive fold below) - never hand-recomputed, so
the two can never silently diverge in the *values*, only (provably, via the
parity test) in behaviour.

Both statements here are plain string literals, matching
``nptc.db.roles``'s own precedent (``backend/tests/
test_sql_parameterisation.py`` rule 1 accepts a ``Name``/``Attribute``
reference to a module-level constant like this one; there is no runtime data
anywhere in either statement to make that a hardship - the function name and
argument name are fixed at deploy time).
"""

from __future__ import annotations

#: `LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE` - IMMUTABLE is required for
#: use inside a `CHECK` constraint; STRICT means a NULL `code` returns NULL
#: (which a `CHECK` treats as satisfied), harmless since `code` is `NOT
#: NULL` on `code_binding`.
CREATE_SCTID_VALIDATION_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION nptc_sctid_is_valid(code text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
WITH RECURSIVE
  tables AS (
    SELECT
      ARRAY[
        [0,1,2,3,4,5,6,7,8,9],
        [1,2,3,4,0,6,7,8,9,5],
        [2,3,4,0,1,7,8,9,5,6],
        [3,4,0,1,2,8,9,5,6,7],
        [4,0,1,2,3,9,5,6,7,8],
        [5,9,8,7,6,0,4,3,2,1],
        [6,5,9,8,7,1,0,4,3,2],
        [7,6,5,9,8,2,1,0,4,3],
        [8,7,6,5,9,3,2,1,0,4],
        [9,8,7,6,5,4,3,2,1,0]
      ]::int[] AS d,
      ARRAY[
        [0,1,2,3,4,5,6,7,8,9],
        [1,5,7,6,2,8,3,0,9,4],
        [5,8,0,3,7,9,6,1,4,2],
        [8,9,1,6,0,4,3,5,2,7],
        [9,4,5,3,1,2,6,8,7,0],
        [4,2,8,6,5,7,3,9,0,1],
        [2,7,9,3,8,0,6,4,1,5],
        [7,0,4,6,9,1,3,2,5,8]
      ]::int[] AS p
  ),
  -- A malformed candidate (wrong length, non-digit) folds over a harmless
  -- placeholder rather than failing the `::int` cast partway through the
  -- fold - the final `code ~ ...` conjunct rejects it regardless, mirroring
  -- `has_valid_check_digit`'s own "total over any str" contract.
  src AS (
    SELECT CASE WHEN code ~ '^[0-9]{6,18}$' THEN code ELSE '0' END AS v
  ),
  fold(position, checksum) AS (
    SELECT 0, 0
    UNION ALL
    SELECT
      f.position + 1,
      t.d[f.checksum + 1][
        t.p[(f.position % 8) + 1][
          substr(s.v, length(s.v) - f.position, 1)::int + 1
        ] + 1
      ]
    FROM fold f, tables t, src s
    WHERE f.position < length(s.v)
  )
SELECT
  code ~ '^[0-9]{6,18}$'
  AND (SELECT checksum FROM fold ORDER BY position DESC LIMIT 1) = 0
$$;
"""

DROP_SCTID_VALIDATION_FUNCTION_SQL = "DROP FUNCTION IF EXISTS nptc_sctid_is_valid(text);"
