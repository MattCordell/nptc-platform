"""The repository's versioned database functions.

Three, with quite different justifications: ``nptc_sctid_is_valid`` (FR-06,
issue #48) below, ``nptc_search_text`` (FR-14/FR-20, issue #142), and
``nptc_numeric_or_null`` (FR-13, issue #54) at the foot of this module - each
carries its own argument for why it exists in the database at all, and none
is a licence for the next one.

**``nptc_sctid_is_valid`` - the Verhoeff check-digit function for SNOMED CT
identifiers (FR-06, issue #48).**

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

#: FR-14/FR-15/FR-20 (issue #142): the search normalisation primitive the two
#: trigram indexes in migration ``0012`` are built over, and the one every
#: search predicate must apply to its own input so index and query agree.
#:
#: **Not the stored logic PRD 14.1 warns against.** Unlike
#: ``nptc_sctid_is_valid`` above - a validity *decision*, argued for
#: individually in ADR-0023 - this encodes no catalogue rule at all: it
#: lowercases and strips diacritics, nothing more. Nothing reads it but the
#: expression indexes and the matching predicate in
#: ``nptc.catalogue.search``, and ``docs/adr/0024-catalogue-search-and-
#: pagination.md`` records the decision. It has to exist as a function
#: because an expression index's expression must be ``IMMUTABLE``.
#:
#: **Why the dictionary is pinned.** ``unaccent(text)``, the one-argument
#: form, is only ``STABLE``: it resolves the dictionary through the current
#: ``search_path``, so its result depends on session state and Postgres
#: rightly refuses it in an index expression. The two-argument form takes an
#: explicit ``regdictionary`` and, with the dictionary named as a constant
#: here, the composition genuinely is immutable for a fixed dictionary
#: definition. That "for a fixed definition" caveat is the whole cost of the
#: marking: if the ``unaccent`` dictionary's rule file is ever changed
#: underneath a running database, both indexes must be ``REINDEX``ed - see
#: ``docs/operations/upgrade.md``. ``STRICT`` so a NULL input is NULL rather
#: than folding to the empty string, which would make every NULL-termed row
#: a trigram match for every short query.
#:
#: **Both the function and the dictionary are schema-qualified ``public.``,
#: and that is load-bearing, not tidiness.** While building or maintaining
#: an expression index, PostgreSQL evaluates the expression with a secure
#: ``search_path`` of ``pg_catalog, pg_temp`` - an attack-surface measure
#: that also means an unqualified ``unaccent(...)`` or
#: ``'unaccent'::regdictionary`` inside an inlined index expression resolves
#: against neither ``public`` nor the caller's path, and
#: ``CREATE INDEX`` fails outright with "text search dictionary
#: \"unaccent\" does not exist". ``0001_extensions_and_app_role.py`` creates
#: the extension with no ``SCHEMA`` clause, so both objects are in
#: ``public``; qualifying them here is what makes the function usable from
#: an index expression at all. Qualification is preferred over a
#: ``SET search_path`` clause on the function, which would make it
#: non-inlinable and so unusable as an indexed expression for the planner.
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

#: FR-14/FR-15 (issue #138): the full-text half of the hybrid search, and the
#: companion to ``nptc_search_text`` above. Four ``tsvector`` GIN indexes in
#: migration ``0015`` are built over ``nptc_search_document``; every FTS
#: predicate in ``nptc.catalogue.search`` matches them against
#: ``nptc_search_query`` applied to the user's own input, so index and query
#: are normalised by the same code rather than by two spellings that agree
#: today.
#:
#: **Why a function pair and not two inline expressions.** The text search
#: configuration has to be identical on both sides - a document lexed as
#: ``english`` and a query lexed as ``simple`` share no stems and match
#: nothing - and a configuration named in nine separate places in
#: ``_SEARCH_SQL`` is a configuration that will eventually disagree with
#: itself. Naming it once here is the only way the agreement is structural.
#:
#: **Why the two-argument ``to_tsvector``/``websearch_to_tsquery``, with the
#: configuration as a constant.** Exactly ``nptc_search_text``'s own reason
#: for the two-argument ``unaccent``: the one-argument forms resolve the
#: configuration through ``default_text_search_config``, a GUC, so they are
#: only ``STABLE`` and PostgreSQL refuses them in an index expression. The
#: two-argument forms with a literal ``regconfig`` are ``IMMUTABLE``. The
#: same "immutable for a fixed definition" caveat applies and has the same
#: remedy: if the ``english`` configuration, its stemmer or its stopword list
#: is ever changed underneath a running database, the four FTS indexes must
#: be ``REINDEX``ed (``docs/operations/upgrade.md``).
#:
#: **Why every object is schema-qualified.** ``public.nptc_search_text`` is
#: qualified for the reason ``nptc_numeric_or_null`` is: this function is
#: inlined into an index expression, and PostgreSQL evaluates an inlined body
#: under a secure ``search_path`` of ``pg_catalog, pg_temp``, where an
#: unqualified reference to a ``public`` function resolves against nothing
#: and ``CREATE INDEX`` fails outright. ``pg_catalog.english`` is qualified
#: for the same reason - a bare ``'english'::regconfig`` is resolved against
#: that same secure path, and while ``pg_catalog`` happens to be on it, being
#: explicit costs nothing and removes the dependence on that happening to
#: stay true.
#:
#: **Why ``english`` and not ``simple``.** ``simple`` lexes to lowercased
#: words with no stemming and no stopword list, which would make the FTS half
#: a strictly worse trigram - it would find nothing trigram does not already
#: find, and the index footprint would buy nothing. ``english`` is what makes
#: this pair earn its place: it is the half that matches a plural against a
#: singular and a query word against an inflected term, which trigram
#: similarity scores as a near-miss rather than a match.
#:
#: ``STRICT``, so a NULL ``au_preferred_term`` yields NULL rather than an
#: empty ``tsvector`` - the same reason ``nptc_search_text`` is ``STRICT``.
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

#: The query-side half of the pair above. ``websearch_to_tsquery`` rather
#: than ``to_tsquery`` or ``plainto_tsquery``, and the choice is about
#: failure behaviour rather than syntax sugar: ``to_tsquery`` raises a syntax
#: error on input it cannot parse, and ``q`` is a free-text field a user
#: types, so every stray ``&``, ``!`` or unbalanced quote would be a 500.
#: ``websearch_to_tsquery`` never raises - it accepts anything and returns a
#: query, possibly an empty one - which is the only acceptable contract for a
#: value arriving from a URL. It also gives users quoted-phrase and ``OR``
#: syntax for free, which ``plainto_tsquery`` does not.
#:
#: An empty ``tsquery`` (``q`` was entirely stopwords, say) matches nothing
#: rather than everything, so the FTS branches simply contribute no rows and
#: the trigram branches still answer the query. That is the correct
#: degradation and it needs no special case in ``_SEARCH_SQL``.
#:
#: Not ``STRICT``-dependent in practice - ``nptc.catalogue.search`` refuses a
#: blank ``q`` before any SQL runs - but marked ``STRICT`` anyway so the pair
#: is symmetric and a NULL can never become a match-everything query.
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

#: FR-13 (issue #54): the cast-safe numeric expression ADR-0012's third
#: index shape needs. See ``docs/adr/0027-cast-safe-numeric-index-
#: expression.md`` for the full argument - the short version: a `decimal`/
#: `positiveInt` property can retain a value that is not castable to
#: `numeric` (validation is not retroactive after a narrowing amendment),
#: and `CREATE INDEX` on a bare `((value #>> '{}')::numeric)` fails outright
#: the moment one such value is on record. This function turns that failure
#: into a `NULL` - a row that cannot be interpreted numerically is simply
#: unfindable by a range filter, which is correct, rather than blocking the
#: index for every other row.
#:
#: **Declared `IMMUTABLE`, even though `pg_input_is_valid` (the function
#: this wraps) is itself `STABLE`** - verified directly against the
#: `postgres:18.6` this repository pins (`pg_proc.provolatile = 's'`). Only
#: safe because the target type here is the single fixed literal
#: `'numeric'`, never a caller-supplied value: `pg_input_is_valid`'s
#: `STABLE` marking is a blanket one covering every possible target type
#: (some of which, e.g. `enum`/`timestamptz`-adjacent types, are genuinely
#: session- or catalog-state-sensitive), not evidence that numeric-literal
#: validity itself varies - numeric parsing has no locale, `search_path` or
#: timezone dependency. This is exactly `nptc_search_text`'s own precedent
#: above: the one-argument `unaccent(text)` is `STABLE` because it resolves
#: its dictionary through `search_path`, but the two-argument form with an
#: explicit, fixed dictionary is genuinely immutable. Should this ever need
#: revisiting, the documented fallback is a `LANGUAGE plpgsql` function with
#: an `EXCEPTION WHEN others THEN RETURN NULL` block - rejected here only
#: because it opens a subtransaction per row on every index build and every
#: insert, a cost this `pg_input_is_valid`-based version avoids entirely.
#: `public.nptc_numeric_or_null`, schema-qualified on both the `CREATE` and
#: the `DROP` (issue #54 review, third pass) - unlike `nptc_sctid_is_valid`/
#: `nptc_search_text` above, which are only ever referenced by other
#: migration-created objects under the migration role's own `search_path`
#: and so are self-consistent either way, this function is also referenced
#: from `nptc.db.property_indexes.create_statement`'s `CREATE INDEX`, which
#: runs on a *different* role's connection (`NPTC_INDEXER_DATABASE_URL`).
#: Leaving this bare would let the migration role's `search_path` decide
#: which schema the function actually lands in, independently of whatever
#: `create_statement` assumes - and unlike the table-reference case, a
#: mismatch here is not just silently wrong, it is a permanent failure
#: (`function public.nptc_numeric_or_null(text) does not exist`) that the
#: reconciler would retry forever. Qualifying the schema on both sides removes
#: the assumption instead of narrowing it.
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
