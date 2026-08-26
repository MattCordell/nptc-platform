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

#: issue #48 (FR-06/FR-08/FR-82): SELECT+INSERT only at table level - same
#: posture as `GRANT_DESIGNATION_SQL` above.
GRANT_CODE_BINDING_SQL = "GRANT SELECT, INSERT ON TABLE code_binding TO nptc_app;"
#: Column-level UPDATE, conspicuously excluding `id`, `entry_id`, `system`
#: and `code` - rebinding to a different concept is a retire-and-replace,
#: never an in-place edit, which is what makes FR-82's provenance guarantee
#: a privilege-level invariant rather than an application convention (the
#: same trick `GRANT_CATALOGUE_ENTRY_UPDATE_SQL` plays for `business_key`).
#: `fsn`/`au_preferred_term` ARE included: the FR-45 validation sweep must
#: be able to refresh a drifted served label from the terminology server -
#: a refresh from the wire, never a re-derivation.
GRANT_CODE_BINDING_UPDATE_SQL = (
    "GRANT UPDATE (fsn, au_preferred_term, edition_hint, status, "
    "replaced_by_binding_id, retirement_reason, updated_at) "
    "ON TABLE code_binding TO nptc_app;"
)
#: No DELETE, no TRUNCATE, ever - a binding is retired via `status`, never
#: removed (FR-08: "the superseded binding is retained") - the same
#: privilege-level guarantee `REVOKE_DESIGNATION_DELETE_SQL` already makes
#: for `designation`.
REVOKE_CODE_BINDING_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE code_binding FROM nptc_app;"

#: issue #49 (FR-05): `term_key`/`preferred_term_key` are new columns added
#: in migration 0009, after 0006/0007 already granted their tables' other
#: columns - a **separate** statement, executed only by 0009, rather than
#: editing `GRANT_DESIGNATION_UPDATE_SQL`/`GRANT_CATALOGUE_ENTRY_UPDATE_SQL`
#: in place. Editing those in place would make migrations 0006/0007 grant a
#: column that does not exist yet on a from-scratch replay
#: (`test_db_round_trip.py`'s downgrade/upgrade fingerprint test).
GRANT_DESIGNATION_TERM_KEY_UPDATE_SQL = "GRANT UPDATE (term_key) ON TABLE designation TO nptc_app;"
GRANT_CATALOGUE_ENTRY_PREFERRED_TERM_KEY_UPDATE_SQL = (
    "GRANT UPDATE (preferred_term_key) ON TABLE catalogue_entry TO nptc_app;"
)

#: issue #49 (FR-05): SELECT+INSERT only - an acknowledgement is a record of
#: an editorial decision, never edited or removed (see the model's own
#: docstring for why withdrawal is out of scope).
GRANT_DESIGNATION_COLLISION_ACK_SQL = (
    "GRANT SELECT, INSERT ON TABLE designation_collision_acknowledgement TO nptc_app;"
)
REVOKE_DESIGNATION_COLLISION_ACK_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE designation_collision_acknowledgement FROM nptc_app;"
)

#: issue #51 (FR-09/FR-11/FR-12), per ADR-0012: SELECT+INSERT only at table
#: level - same posture as `GRANT_CATALOGUE_ENTRY_SQL` above.
GRANT_PROPERTY_DEFINITION_SQL = "GRANT SELECT, INSERT ON TABLE property_definition TO nptc_app;"
#: Column-level UPDATE excluding `key`, `id`, `index_seq`, `origin` and
#: `created_at` - never table-level `UPDATE`, which would supersede this
#: column list. This is what makes FR-12 ("INSERT may set key, UPDATE may
#: not touch it") a database invariant: an UPDATE reaching `key` fails with
#: `42501` regardless of what the ORM or a future contributor believes.
#: `row_version` MUST be included - SQLAlchemy's `version_id_col` machinery
#: issues `UPDATE ... SET row_version = ...` as part of every mapped
#: update, matching `GRANT_CATALOGUE_ENTRY_UPDATE_SQL`'s own precedent.
#:
#: **Frozen as migration 0010 first wrote it - do not edit for a later
#: column.** Migration 0010 calls `op.execute(GRANT_PROPERTY_DEFINITION_
#: UPDATE_SQL)` and replays that exact statement on every fresh migrate from
#: empty; widening this constant in place would silently rewrite 0010's own
#: history and grant a not-yet-existent column the moment a later migration
#: (whichever one adds it) runs *after* 0010 but *before* the column exists.
#: A later column's grant is a new, separate constant executed by the
#: migration that adds that column - see
#: `GRANT_PROPERTY_DEFINITION_LOCAL_CODE_SYSTEM_KEY_UPDATE_SQL` below for
#: issue #52's own case.
GRANT_PROPERTY_DEFINITION_UPDATE_SQL = (
    "GRANT UPDATE (label, datatype, cardinality, scope, required_for_submission, "
    "required_for_publication, binding_target, value_set_uri, strength, edition, "
    "filterable, status, display_order, constraints, deprecated_at, updated_at, "
    "row_version) ON TABLE property_definition TO nptc_app;"
)
#: No DELETE grant at all - FR-11's stronger, unconditional form (ADR-0012):
#: the PRD's conditional test ("has it appeared in a published export?") is
#: never asked, so it can never be got wrong.
REVOKE_PROPERTY_DEFINITION_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE property_definition FROM nptc_app;"
)
#: issue #52 (FR-10): the one column migration 0013 adds, granted on its
#: own rather than by widening `GRANT_PROPERTY_DEFINITION_UPDATE_SQL` in
#: place - see that constant's own comment for why 0010 must keep replaying
#: its original, narrower column list forever.
GRANT_PROPERTY_DEFINITION_LOCAL_CODE_SYSTEM_KEY_UPDATE_SQL = (
    "GRANT UPDATE (local_code_system_key) ON TABLE property_definition TO nptc_app;"
)

#: issue #51 (FR-09/FR-10), per ADR-0012: SELECT+INSERT+UPDATE+DELETE - a
#: value is ordinary editable content (removing a specimen from an entry is
#: normal editing, not the case FR-11 protects against), unlike
#: `property_definition` above.
GRANT_PROPERTY_VALUE_SQL = (
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE property_value TO nptc_app;"
)
REVOKE_PROPERTY_VALUE_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE property_value FROM nptc_app;"

#: issue #56 (FR-90): SELECT+INSERT only at table level - same posture as
#: `GRANT_CODE_BINDING_SQL` above.
GRANT_LOCAL_CODE_SYSTEM_SQL = "GRANT SELECT, INSERT ON TABLE local_code_system TO nptc_app;"
#: Column-level UPDATE, excluding `id`, `key` and `created_at` - `key` is
#: immutable for the same reason `catalogue_entry.business_key` is (see
#: `LocalCodeSystem._validate_key_immutable`).
GRANT_LOCAL_CODE_SYSTEM_UPDATE_SQL = (
    "GRANT UPDATE (uri, title, description, owner, status, updated_at) "
    "ON TABLE local_code_system TO nptc_app;"
)
#: No DELETE, no TRUNCATE, ever - a code system is deprecated via `status`,
#: never removed.
REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE local_code_system FROM nptc_app;"
)

#: issue #56 (FR-90/FR-92): SELECT+INSERT only - same posture as
#: `GRANT_CODE_BINDING_SQL` above.
GRANT_LOCAL_CODE_SQL = "GRANT SELECT, INSERT ON TABLE local_code TO nptc_app;"
#: Column-level UPDATE, excluding `id`, `system_id`, `code` and
#: `created_at` - a code is deprecated and replaced by a new row, never
#: reparented or rebound in place (the same trick `GRANT_CODE_BINDING_
#: UPDATE_SQL` plays for `entry_id`/`code`).
GRANT_LOCAL_CODE_UPDATE_SQL = (
    "GRANT UPDATE (display, definition, provisional, status, deprecated_at, "
    "deprecation_reason, display_order, updated_at) ON TABLE local_code TO nptc_app;"
)
#: No DELETE, no TRUNCATE, ever - a code is deprecated via `status`, never
#: removed.
REVOKE_LOCAL_CODE_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE local_code FROM nptc_app;"

#: issue #56 (FR-91): SELECT+INSERT only - an advisory map row is a
#: point-in-time editorial judgement, never edited or removed (see the
#: model's own "never edited, only replaced" docstring note).
GRANT_LOCAL_CODE_SNOMED_MAP_SQL = "GRANT SELECT, INSERT ON TABLE local_code_snomed_map TO nptc_app;"
REVOKE_LOCAL_CODE_SNOMED_MAP_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE local_code_snomed_map FROM nptc_app;"
)
