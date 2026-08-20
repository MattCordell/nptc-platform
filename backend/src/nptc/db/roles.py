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

#: NFR-17 needs UPDATE on app_user (to write the tombstone) but
#: conspicuously **no DELETE** - refusing it at the privilege level makes
#: "pseudonymise, never delete" a database invariant, not an application
#: convention. Column-level UPDATE excludes `id` and `created_at`, so the
#: retained UUID (what audit attribution and the NFR-10 hash chain depend
#: on) is immutable even to the app role itself.
GRANT_APP_USER_SQL = "GRANT SELECT, INSERT ON TABLE app_user TO nptc_app;"
GRANT_APP_USER_UPDATE_SQL = (
    "GRANT UPDATE (username, display_name, organisation, status, closed_at, updated_at) "
    "ON TABLE app_user TO nptc_app;"
)
REVOKE_APP_USER_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE app_user FROM nptc_app;"

#: NFR-17 needs DELETE on user_identity - closing an account removes its
#: linked identities outright (there is no tombstone shape for a link row).
GRANT_USER_IDENTITY_SQL = "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE user_identity TO nptc_app;"
REVOKE_USER_IDENTITY_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE user_identity FROM nptc_app;"

#: issue #44 (FR-44/FR-01): a role grant is created or removed, never
#: edited. **Column-level `UPDATE (granted_at)` only** - not the blanket
#: no-UPDATE-at-all the module docstring first assumed, and not table-wide
#: `UPDATE`, either. Postgres requires *some* `UPDATE` privilege on the
#: table before it will honour `SELECT ... FOR UPDATE` at all - confirmed
#: against a real container, since this is exactly the kind of privilege
#: detail worth checking rather than assuming - and `nptc.auth.grants.
#: assert_not_last_administrator`'s row lock (FR-01) needs precisely that.
#: `granted_at` is the one column nothing in this codebase ever writes to
#: after insert (a server-defaulted creation timestamp, the same role
#: `app_user.created_at`/`id` play in being excluded from *their* table's
#: UPDATE grant) - granting `UPDATE` on it, and nothing else, satisfies
#: Postgres's row-locking requirement while `user_id`/`role`/
#: `granted_by_user_id` - the columns that would actually rewrite "who
#: granted this, and when" - remain immutable at the privilege level.
GRANT_USER_ROLE_SQL = "GRANT SELECT, INSERT, DELETE ON TABLE user_role TO nptc_app;"
GRANT_USER_ROLE_UPDATE_SQL = "GRANT UPDATE (granted_at) ON TABLE user_role TO nptc_app;"
REVOKE_USER_ROLE_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE user_role FROM nptc_app;"

#: issue #46 (FR-03/FR-38): SELECT+INSERT only at table level - every
#: further privilege below is deliberately narrower than "the whole row".
GRANT_CATALOGUE_ENTRY_SQL = "GRANT SELECT, INSERT ON TABLE catalogue_entry TO nptc_app;"
#: Column-level UPDATE, conspicuously excluding `id`, `business_key` and
#: `created_at` - the same trick `GRANT_APP_USER_UPDATE_SQL` plays for
#: `app_user.id`/`created_at`. This is what makes FR-03's business_key
#: immutability a database invariant rather than an application
#: convention. `row_version` MUST be included: SQLAlchemy's `version_id_col`
#: machinery issues `UPDATE ... SET row_version = ... WHERE row_version =
#: ...` as part of every mapped update, and omitting it here would turn
#: every optimistic write into a permission error rather than a version
#: check.
GRANT_CATALOGUE_ENTRY_UPDATE_SQL = (
    "GRANT UPDATE (preferred_term, status, specimen_unconstrained, updated_at, row_version) "
    "ON TABLE catalogue_entry TO nptc_app;"
)
#: No DELETE, no TRUNCATE, ever - an entry is deprecated or withdrawn via
#: `status`, never removed. Combined with the UNIQUE constraint on
#: `business_key` and a monotonic, never-rolled-back minting sequence, this
#: is what guarantees FR-03's "never reused" rather than merely intending it.
REVOKE_CATALOGUE_ENTRY_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE catalogue_entry FROM nptc_app;"
)
#: Unlike `audit_event.sequence` (`GENERATED ALWAYS AS IDENTITY`, not
#: ACL-checked - see that model's own comment), `business_key` is minted by
#: an explicit `nextval()` call in `nptc.catalogue.entries.
#: allocate_business_key`, evaluated with the *inserting* role's own
#: privileges - so this needs USAGE (covers `nextval`) and UPDATE
#: (Postgres requires sequence-level `UPDATE`, not `USAGE`, to run
#: `setval` - `advance_sequence_past` calls it as one atomic
#: `setval(seq, GREATEST(nextval(seq) - 1, :value), true)` - proven
#: against a real container, not assumed) granted explicitly, unlike an
#: identity column's backing sequence. No `SELECT`: nothing here reads
#: the sequence's `last_value`/`currval` directly.
GRANT_CATALOGUE_BUSINESS_KEY_SEQ_SQL = (
    "GRANT USAGE, UPDATE ON SEQUENCE catalogue_entry_business_key_seq TO nptc_app;"
)

#: issue #47 (FR-04/FR-24/FR-37/FR-85): SELECT+INSERT only at table level -
#: same posture as `GRANT_CATALOGUE_ENTRY_SQL` above.
GRANT_DESIGNATION_SQL = "GRANT SELECT, INSERT ON TABLE designation TO nptc_app;"
#: Column-level UPDATE, excluding `id`, `entry_id` and `created_at` - a
#: designation is retired and re-created on a different entry, never
#: reparented (see `Designation._validate_entry_id_immutable`'s Python-level
#: guard for the same invariant), matching `business_key`'s own treatment
#: on `catalogue_entry`.
GRANT_DESIGNATION_UPDATE_SQL = (
    "GRANT UPDATE (term, use, language, status, updated_at) ON TABLE designation TO nptc_app;"
)
#: No DELETE, no TRUNCATE, ever - a designation is retired via `status`,
#: never removed ("a retired designation is retained, not deleted", #47's
#: own acceptance criterion) - the same privilege-level guarantee
#: `REVOKE_CATALOGUE_ENTRY_DELETE_SQL` already makes for `catalogue_entry`.
REVOKE_DESIGNATION_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE designation FROM nptc_app;"
