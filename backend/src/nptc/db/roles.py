"""The least-privilege application role (issue #33) and its grant/revoke SQL.

Imported by **both** the migration that creates the role/grants and the
tests that assert them, so the granted and asserted privilege sets cannot
drift apart - a test editing its own expectation instead of the migration's
actual grant would defeat the point of a refusal test.

Every statement here is a plain string literal, never built from an
f-string, ``%``/``+`` concatenation, or ``.format()`` - see
``backend/tests/test_sql_parameterisation.py`` (NFR-22), which enforces this
statically. There is no runtime data anywhere in these statements to make
that a hardship: the role name is fixed at deploy time, not supplied by a
caller.
"""

from __future__ import annotations

#: The app runtime role. NOLOGIN - nothing ever authenticates directly as
#: this role; a LOGIN role is granted membership in it instead (see
#: backend/tests/conftest.py's ``nptc_app_login``, and
#: docs/operations/upgrade.md for the equivalent out-of-band operator step
#: in a real deployment).
APP_ROLE = "nptc_app"

#: Roles are cluster-wide, so plain ``CREATE ROLE`` is not idempotent across
#: two databases sharing one cluster - hence the guard. This ``DO $$`` block
#: is an anonymous, one-shot statement executed once by the migration, not
#: stored server-side logic (PRD Section 14.1's ban is on triggers/functions
#: that run on every future write, which this is not - see ADR-0011).
CREATE_APP_ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nptc_app') THEN
    CREATE ROLE nptc_app NOLOGIN;
  END IF;
END $$;
"""

GRANT_SCHEMA_USAGE_SQL = "GRANT USAGE ON SCHEMA public TO nptc_app;"

#: SELECT+INSERT only. TRUNCATE is a distinct, owner-only privilege not
#: implied by DELETE - but it *is* included in ``GRANT ALL``, so the REVOKE
#: below is belt-and-braces, not a no-op: it is the literal string NFR-22's
#: guard greps for to enforce that nothing in this codebase ever grants ALL
#: on the audit table (rule 3 of test_sql_parameterisation.py).
GRANT_AUDIT_EVENT_SQL = "GRANT SELECT, INSERT ON TABLE audit_event TO nptc_app;"
REVOKE_AUDIT_EVENT_WRITE_SQL = "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_event FROM nptc_app;"
