"""The repository's versioned database functions.

Five functions, with different justifications. PRD 14.1 bans business logic in
database functions because it is invisible to tests and review, and none of
these is a licence for the next.

- ``nptc_sctid_is_valid`` (FR-06) is a deliberate, narrow exception, argued in
  ADR-0023. FR-06 needs the column's own ``CHECK`` to enforce Verhoeff, and a
  ``CHECK`` cannot hold a subquery or CTE, so the fold has to live in a
  function. It is a pure predicate (``LANGUAGE sql IMMUTABLE STRICT``), it is
  versioned here and in a migration, and
  ``backend/tests/test_db_sctid_function.py`` proves it agrees with
  ``nptc_shared.sctid`` over an exhaustive corpus.
- ``nptc_search_text``, ``nptc_search_document`` and ``nptc_search_query``
  (FR-14, FR-15, FR-20) are search normalisation with no catalogue rule in
  them. The first two are index expressions, which must be ``IMMUTABLE``, so
  each has to be a function. ``nptc_search_query`` is in no index; it exists so
  the text search configuration is named once for both index and query
  (ADR-0024, ADR-0029).
- ``nptc_numeric_or_null`` (FR-13) is the cast-safe numeric index expression
  (ADR-0027).

The Verhoeff tables in ``nptc_sctid_is_valid`` are ``nptc_shared.sctid._D`` and
``_P`` as a Postgres array literal, never hand-recomputed. Postgres arrays are
1-based, hence the ``+ 1`` throughout the fold.

Every statement is a plain string literal, which rule 1 of
``backend/tests/test_sql_parameterisation.py`` accepts.

**Every ``public.`` qualification is load-bearing.** PostgreSQL evaluates an
inlined index expression under a secure ``search_path`` of ``pg_catalog,
pg_temp``, so an unqualified reference to a ``public`` object makes ``CREATE
INDEX`` fail. A ``SET search_path`` clause on the function would stop it
inlining, so the objects are qualified instead (ADR-0024).

**The search functions are ``IMMUTABLE`` only for a fixed dictionary or
configuration.** The one-argument ``unaccent`` and ``to_tsvector`` read a
setting and are only ``STABLE``, so these functions use the two-argument forms
with a constant. If
the ``unaccent`` rules or the ``english`` configuration change under a running
database, the indexes must be ``REINDEX``ed (``docs/operations/upgrade.md``).
"""

from __future__ import annotations

#: `IMMUTABLE` is required for use inside a `CHECK`. `STRICT` returns NULL for a
#: NULL `code`, which a `CHECK` treats as satisfied; harmless because `code` is
#: `NOT NULL` on `code_binding`.
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

#: FR-14/FR-15/FR-20: the normalisation both trigram indexes are built over
#: (migration ``0012``), and that every search predicate applies to its own
#: input so index and query agree. It lowercases and strips diacritics and
#: encodes no catalogue rule (ADR-0024). ``STRICT``, so a NULL input stays NULL
#: instead of folding to an empty string that would trigram-match every short
#: query.
CREATE_SEARCH_TEXT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION nptc_search_text(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
SELECT lower(public.unaccent('public.unaccent'::regdictionary, value))
$$;
"""

DROP_SEARCH_TEXT_FUNCTION_SQL = "DROP FUNCTION IF EXISTS nptc_search_text(text);"

#: FR-14/FR-15: the full-text half of the hybrid, beside ``nptc_search_text``
#: (ADR-0029). The four ``tsvector`` GIN indexes in migration ``0015`` are built
#: over it, and every FTS predicate in ``nptc.catalogue.search`` matches them
#: against ``nptc_search_query`` of the user's input. The text search
#: configuration is therefore named once and cannot disagree between index and
#: query. It is ``english``, not ``simple``, because stemming and stopwords let
#: it match a plural against a singular, which trigram scores as a near-miss.
#: ``STRICT``, so a NULL term yields NULL rather than an empty ``tsvector``.
CREATE_SEARCH_DOCUMENT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.nptc_search_document(value text)
RETURNS tsvector
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
SELECT to_tsvector('pg_catalog.english'::regconfig, public.nptc_search_text(value))
$$;
"""

DROP_SEARCH_DOCUMENT_FUNCTION_SQL = "DROP FUNCTION IF EXISTS public.nptc_search_document(text);"

#: The query-side half of the pair above. ``websearch_to_tsquery``, not
#: ``to_tsquery``, because ``q`` is free text from a URL and ``to_tsquery``
#: raises on input it cannot parse (ADR-0029). An empty ``tsquery`` matches
#: nothing, so the FTS branches add no rows and the trigram branches still
#: answer.
#:
#: A query that absence alone satisfies (``-glucose``) matches every row by
#: sequential scan. This function does not repair that, because rewriting the
#: user's query would make the returned ``tsquery`` disagree with what they
#: typed. ``nptc.catalogue.search`` guards each FTS branch instead (ADR-0029).
#:
#: It raises a ``NOTICE`` on stopword-only input, once per use in the search
#: statement. That is left alone deliberately; read ADR-0029 before attaching a
#: notice handler.
#:
#: ``STRICT`` keeps the pair symmetric, so a NULL can never become a
#: match-everything query.
CREATE_SEARCH_QUERY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.nptc_search_query(value text)
RETURNS tsquery
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
SELECT websearch_to_tsquery('pg_catalog.english'::regconfig, public.nptc_search_text(value))
$$;
"""

DROP_SEARCH_QUERY_FUNCTION_SQL = "DROP FUNCTION IF EXISTS public.nptc_search_query(text);"

#: FR-13: the cast-safe numeric index expression (ADR-0027). A narrowing
#: amendment can leave a value that does not cast to `numeric`, which would
#: fail a bare `CREATE INDEX`; this returns `NULL` for it instead.
#:
#: `IMMUTABLE` although the `pg_input_is_valid` it wraps is `STABLE`. That is
#: safe only because the target type is the fixed literal `'numeric'`; ADR-0027
#: has the argument and the `plpgsql` fallback.
#:
#: `public.` on both the `CREATE` and the `DROP`, because
#: `nptc.db.property_indexes.create_statement` calls this from the indexer
#: role's connection (`NPTC_INDEXER_DATABASE_URL`), whose `search_path` may
#: differ from the migration role's. A mismatch is a permanent failure that the
#: reconciler would retry forever (ADR-0012).
CREATE_NUMERIC_OR_NULL_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.nptc_numeric_or_null(v text)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
SELECT CASE WHEN pg_input_is_valid(v, 'numeric') THEN v::numeric END
$$;
"""

DROP_NUMERIC_OR_NULL_FUNCTION_SQL = "DROP FUNCTION IF EXISTS public.nptc_numeric_or_null(text);"
