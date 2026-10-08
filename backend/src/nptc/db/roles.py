"""The least-privilege application role and its grant/revoke SQL.

Imported by **both** the migration that creates the role and grants and the
tests that assert them, so the granted and asserted privilege sets cannot drift
apart.

**Grant model.** Most tables get a table-level ``SELECT, INSERT`` grant. Where
rows are edited, ``UPDATE`` is column-level and leaves out the immutable
columns (keys, parents, ``created_at``); ``user_identity`` and
``property_value`` are the exceptions, with table-level ``UPDATE``. Append-only
tables get no ``UPDATE``, and ``validation_finding`` gets ``SELECT`` only.
``DELETE`` is granted on ``user_identity``, ``user_role`` and
``property_value`` alone, and ``TRUNCATE`` is revoked everywhere. Each of these
is a privilege-level invariant, not an application convention: a violating
statement fails with ``42501`` whatever the ORM or a future contributor
believes. A constant's comment therefore names only what is special about that
table.

**A shipped constant is frozen.** A migration replays its grant constant on
every fresh migrate from empty, so widening a constant in place would grant a
column that does not exist yet when that migration runs. A later column gets
its own constant, executed by the migration that adds the column.

Every statement is a plain string literal, never built from an f-string,
``%``/``+`` concatenation or ``.format()`` (NFR-22, enforced by
``backend/tests/test_sql_parameterisation.py``). The role name is fixed at
deploy time.
"""

from __future__ import annotations

#: The app runtime role. NOLOGIN: nothing authenticates as it directly. A LOGIN
#: role is granted membership in it instead (``nptc_app_login`` in
#: ``backend/tests/conftest.py``; ``docs/operations/upgrade.md`` for a real
#: deployment).
APP_ROLE = "nptc_app"

#: Roles are cluster-wide, so a plain ``CREATE ROLE`` is not idempotent across
#: two databases in one cluster; hence the guard. This ``DO $$`` block runs once
#: in the migration, so it is not the stored logic PRD Section 14.1 bans
#: (ADR-0011).
CREATE_APP_ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nptc_app') THEN
    CREATE ROLE nptc_app NOLOGIN;
  END IF;
END $$;
"""

GRANT_SCHEMA_USAGE_SQL = "GRANT USAGE ON SCHEMA public TO nptc_app;"

#: Append-only (NFR-09). ``TRUNCATE`` is owner-only and not implied by
#: ``DELETE``, but ``GRANT ALL`` includes it, so the REVOKE is not a no-op: it is
#: the literal string rule 3 of ``test_sql_parameterisation.py`` greps for to
#: ensure nothing grants ALL on the audit table.
GRANT_AUDIT_EVENT_SQL = "GRANT SELECT, INSERT ON TABLE audit_event TO nptc_app;"
REVOKE_AUDIT_EVENT_WRITE_SQL = "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_event FROM nptc_app;"

#: NFR-17 needs ``UPDATE`` (to write the tombstone) but no ``DELETE``:
#: "pseudonymise, never delete". The column list leaves out ``id`` and
#: ``created_at``, so the retained UUID that audit attribution and the NFR-10
#: hash chain depend on is immutable even to the app role.
GRANT_APP_USER_SQL = "GRANT SELECT, INSERT ON TABLE app_user TO nptc_app;"
GRANT_APP_USER_UPDATE_SQL = (
    "GRANT UPDATE (username, display_name, organisation, status, closed_at, updated_at) "
    "ON TABLE app_user TO nptc_app;"
)
REVOKE_APP_USER_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE app_user FROM nptc_app;"

#: NFR-17 needs ``DELETE``: closing an account removes its linked identities
#: outright, because a link row has no tombstone shape. ``UPDATE`` is
#: table-level.
GRANT_USER_IDENTITY_SQL = "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE user_identity TO nptc_app;"
REVOKE_USER_IDENTITY_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE user_identity FROM nptc_app;"

#: FR-44, FR-01: a role grant is created or removed, never edited, so ``UPDATE``
#: is column-level on ``granted_at`` only. Postgres requires some ``UPDATE``
#: privilege on the table before it honours ``SELECT ... FOR UPDATE``, and
#: ``nptc.auth.grants.assert_not_last_administrator`` takes that row lock.
#: ``granted_at`` is never written after insert, so granting it satisfies
#: Postgres while ``user_id``, ``role`` and ``granted_by_user_id`` stay
#: immutable.
GRANT_USER_ROLE_SQL = "GRANT SELECT, INSERT, DELETE ON TABLE user_role TO nptc_app;"
GRANT_USER_ROLE_UPDATE_SQL = "GRANT UPDATE (granted_at) ON TABLE user_role TO nptc_app;"
REVOKE_USER_ROLE_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE user_role FROM nptc_app;"

#: FR-03, FR-38.
GRANT_CATALOGUE_ENTRY_SQL = "GRANT SELECT, INSERT ON TABLE catalogue_entry TO nptc_app;"
#: Excludes `id`, `business_key` and `created_at`, which makes FR-03's key
#: immutability a database invariant. `row_version` MUST be included:
#: `version_id_col` issues `UPDATE ... SET row_version = ... WHERE row_version =
#: ...` on every mapped update, and omitting it would turn every optimistic
#: write into a permission error.
#: Migration 0006's statement, replayed as written. Migration 0024 drops
#: `specimen_unconstrained`, and its grant goes with it.
GRANT_CATALOGUE_ENTRY_UPDATE_SQL = (
    "GRANT UPDATE (preferred_term, status, specimen_unconstrained, updated_at, row_version) "
    "ON TABLE catalogue_entry TO nptc_app;"
)
#: Re-grants the one column migration 0024's downgrade puts back.
GRANT_CATALOGUE_ENTRY_SPECIMEN_UNCONSTRAINED_UPDATE_SQL = (
    "GRANT UPDATE (specimen_unconstrained) ON TABLE catalogue_entry TO nptc_app;"
)
#: An entry is deprecated or withdrawn through `status`, never removed. With
#: `UNIQUE (business_key)` and a monotonic minting sequence, that is what
#: guarantees FR-03's "never reused".
REVOKE_CATALOGUE_ENTRY_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE catalogue_entry FROM nptc_app;"
)
#: `business_key` is minted by an explicit `nextval()` in
#: `nptc.catalogue.entries.allocate_business_key`, evaluated with the inserting
#: role's own privileges, unlike `audit_event.sequence` (an identity column). So
#: the role needs USAGE for `nextval` and UPDATE for `setval`, which
#: `advance_sequence_past` calls: Postgres requires sequence-level UPDATE, not
#: USAGE, for `setval`. No SELECT: nothing reads `last_value` or `currval`.
GRANT_CATALOGUE_BUSINESS_KEY_SEQ_SQL = (
    "GRANT USAGE, UPDATE ON SEQUENCE catalogue_entry_business_key_seq TO nptc_app;"
)

#: FR-04, FR-24, FR-37, FR-85.
GRANT_DESIGNATION_SQL = "GRANT SELECT, INSERT ON TABLE designation TO nptc_app;"
#: Excludes `id`, `entry_id` and `created_at`: a designation is retired and
#: re-created on another entry, never reparented
#: (`Designation._validate_entry_id_immutable` is the Python-level guard).
#: Migration 0007's statement, replayed as written. Migration 0026 drops `use` and `language`, and
#: their grants go with them.
GRANT_DESIGNATION_UPDATE_SQL = (
    "GRANT UPDATE (term, use, language, status, updated_at) ON TABLE designation TO nptc_app;"
)
#: Re-grants the two columns migration 0026's downgrade puts back.
GRANT_DESIGNATION_USE_LANGUAGE_UPDATE_SQL = (
    "GRANT UPDATE (use, language) ON TABLE designation TO nptc_app;"
)
#: A designation is retired through `status`, never removed: a retired
#: designation is retained.
REVOKE_DESIGNATION_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE designation FROM nptc_app;"

#: FR-06, FR-08, FR-82.
GRANT_CODE_BINDING_SQL = "GRANT SELECT, INSERT ON TABLE code_binding TO nptc_app;"
#: Excludes `id`, `entry_id`, `system` and `code`: rebinding to a different
#: concept is retire-and-replace, never an in-place edit, which makes FR-82's
#: provenance a privilege-level invariant. `fsn` and `au_preferred_term` ARE
#: included, because the FR-45 validation sweep refreshes a drifted served
#: label from the terminology server.
GRANT_CODE_BINDING_UPDATE_SQL = (
    "GRANT UPDATE (fsn, au_preferred_term, edition_hint, status, "
    "replaced_by_binding_id, retirement_reason, updated_at) "
    "ON TABLE code_binding TO nptc_app;"
)
#: A binding is retired through `status`, never removed (FR-08: "the superseded
#: binding is retained").
REVOKE_CODE_BINDING_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE code_binding FROM nptc_app;"

#: FR-17: a later column, so its own constant, executed by migration 0016. Set
#: with `status` and `retirement_reason` by
#: `nptc.catalogue.bindings.retire_binding`, never alone.
GRANT_CODE_BINDING_RETIRED_AT_UPDATE_SQL = (
    "GRANT UPDATE (retired_at) ON TABLE code_binding TO nptc_app;"
)

#: FR-05: `term_key` and `preferred_term_key` are later columns, so each has its
#: own constant, executed by migration 0009.
GRANT_DESIGNATION_TERM_KEY_UPDATE_SQL = "GRANT UPDATE (term_key) ON TABLE designation TO nptc_app;"

#: A later column, so its own constant, executed by migration 0020. Set by
#: `retire_designation`, cleared by `reinstate_designation`.
GRANT_DESIGNATION_RETIRED_AT_UPDATE_SQL = (
    "GRANT UPDATE (retired_at) ON TABLE designation TO nptc_app;"
)
GRANT_CATALOGUE_ENTRY_PREFERRED_TERM_KEY_UPDATE_SQL = (
    "GRANT UPDATE (preferred_term_key) ON TABLE catalogue_entry TO nptc_app;"
)

#: FR-05: an acknowledgement records an editorial decision, never edited or
#: removed.
GRANT_DESIGNATION_COLLISION_ACK_SQL = (
    "GRANT SELECT, INSERT ON TABLE designation_collision_acknowledgement TO nptc_app;"
)
REVOKE_DESIGNATION_COLLISION_ACK_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE designation_collision_acknowledgement FROM nptc_app;"
)

#: FR-09, FR-11, FR-12 (ADR-0012).
GRANT_PROPERTY_DEFINITION_SQL = "GRANT SELECT, INSERT ON TABLE property_definition TO nptc_app;"
#: Excludes `key`, `id`, `index_seq`, `origin` and `created_at`. Never
#: table-level `UPDATE`, which would supersede this column list. This makes
#: FR-12 ("INSERT may set key, UPDATE may not touch it") a database invariant.
#: `row_version` MUST be included, as in `GRANT_CATALOGUE_ENTRY_UPDATE_SQL`.
#: Frozen as migration 0010 wrote it (see the module docstring).
GRANT_PROPERTY_DEFINITION_UPDATE_SQL = (
    "GRANT UPDATE (label, datatype, cardinality, scope, required_for_submission, "
    "required_for_publication, binding_target, value_set_uri, strength, edition, "
    "filterable, status, display_order, constraints, deprecated_at, updated_at, "
    "row_version) ON TABLE property_definition TO nptc_app;"
)
#: No `DELETE` at all: FR-11's unconditional form (ADR-0012). The PRD's
#: conditional test ("has it appeared in a published export?") is never asked,
#: so it can never be got wrong.
REVOKE_PROPERTY_DEFINITION_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE property_definition FROM nptc_app;"
)
#: FR-10: the one column migration 0013 adds, so its own constant (see the
#: module docstring).
GRANT_PROPERTY_DEFINITION_LOCAL_CODE_SYSTEM_KEY_UPDATE_SQL = (
    "GRANT UPDATE (local_code_system_key) ON TABLE property_definition TO nptc_app;"
)

#: FR-09, FR-10 (ADR-0012). `DELETE` is granted because a value is ordinary
#: editable content: removing a specimen from an entry is normal editing, not
#: the case FR-11 protects. ``UPDATE`` is table-level.
GRANT_PROPERTY_VALUE_SQL = (
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE property_value TO nptc_app;"
)
REVOKE_PROPERTY_VALUE_TRUNCATE_SQL = "REVOKE TRUNCATE ON TABLE property_value FROM nptc_app;"

#: FR-13, ADR-0012: the index reconciler's login (`nptc_indexer`, see `nptc.db.provision_login`)
#: is a member of this NOLOGIN role, which owns `property_value` and nothing else. Postgres has no
#: grantable "create index" privilege: `CREATE INDEX` needs table ownership, and ownership is the
#: narrowest grant that works. The owner role also needs `SELECT` on `property_definition`, the
#: table the reconciler reads to learn which indexes should exist.
PROPERTY_INDEX_OWNER_ROLE = "nptc_property_index_owner"
CREATE_PROPERTY_INDEX_OWNER_ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nptc_property_index_owner') THEN
    CREATE ROLE nptc_property_index_owner NOLOGIN;
  END IF;
END $$;
"""
TRANSFER_PROPERTY_VALUE_OWNERSHIP_SQL = (
    "ALTER TABLE property_value OWNER TO nptc_property_index_owner;"
)
#: Run by the migration role, so `CURRENT_USER` is the role that created the table.
RESTORE_PROPERTY_VALUE_OWNERSHIP_SQL = "ALTER TABLE property_value OWNER TO CURRENT_USER;"
GRANT_PROPERTY_INDEX_OWNER_PROPERTY_DEFINITION_SQL = (
    "GRANT SELECT ON TABLE property_definition TO nptc_property_index_owner;"
)
REVOKE_PROPERTY_INDEX_OWNER_PROPERTY_DEFINITION_SQL = (
    "REVOKE SELECT ON TABLE property_definition FROM nptc_property_index_owner;"
)
#: Postgres checks `CREATE` on the schema for every new index, and PG15+ withholds it from PUBLIC.
#: It also lets the role create other objects in `public`; that is the cost of the check, not a
#: separate choice.
GRANT_PROPERTY_INDEX_OWNER_SCHEMA_CREATE_SQL = (
    "GRANT CREATE ON SCHEMA public TO nptc_property_index_owner;"
)
REVOKE_PROPERTY_INDEX_OWNER_SCHEMA_CREATE_SQL = (
    "REVOKE CREATE ON SCHEMA public FROM nptc_property_index_owner;"
)

#: FR-90.
GRANT_LOCAL_CODE_SYSTEM_SQL = "GRANT SELECT, INSERT ON TABLE local_code_system TO nptc_app;"
#: Excludes `id`, `key` and `created_at`. `key` is immutable for the same reason
#: as `catalogue_entry.business_key` (`LocalCodeSystem._validate_key_immutable`).
GRANT_LOCAL_CODE_SYSTEM_UPDATE_SQL = (
    "GRANT UPDATE (uri, title, description, owner, status, updated_at) "
    "ON TABLE local_code_system TO nptc_app;"
)
#: A code system is deprecated through `status`, never removed.
REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL = (
    "REVOKE DELETE, TRUNCATE ON TABLE local_code_system FROM nptc_app;"
)

#: FR-90, FR-92.
GRANT_LOCAL_CODE_SQL = "GRANT SELECT, INSERT ON TABLE local_code TO nptc_app;"
#: Excludes `id`, `system_id`, `code` and `created_at`: a code is deprecated and
#: replaced by a new row, never reparented or rebound.
GRANT_LOCAL_CODE_UPDATE_SQL = (
    "GRANT UPDATE (display, definition, provisional, status, deprecated_at, "
    "deprecation_reason, display_order, updated_at) ON TABLE local_code TO nptc_app;"
)
#: A code is deprecated through `status`, never removed.
REVOKE_LOCAL_CODE_DELETE_SQL = "REVOKE DELETE, TRUNCATE ON TABLE local_code FROM nptc_app;"

#: FR-91: an advisory map row is a point-in-time editorial judgement, never
#: edited or removed.
GRANT_LOCAL_CODE_SNOMED_MAP_SQL = "GRANT SELECT, INSERT ON TABLE local_code_snomed_map TO nptc_app;"
REVOKE_LOCAL_CODE_SNOMED_MAP_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE local_code_snomed_map FROM nptc_app;"
)

#: FR-18, FR-45, FR-55: **SELECT only**, the one table `nptc_app` cannot write.
#: Nothing in P1 inserts a finding through the interactive app role: FR-45's
#: sweep and FR-55's lifecycle are P3, and which account runs the sweep is P3's
#: decision. Test fixtures seed rows on the owner connection
#: (`Session(bind=db)`), as `test_audit_tamper_detection.py` does, never
#: `app_db`, which would need INSERT.
GRANT_VALIDATION_FINDING_SQL = "GRANT SELECT ON TABLE validation_finding TO nptc_app;"
REVOKE_VALIDATION_FINDING_WRITE_SQL = (
    "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE validation_finding FROM nptc_app;"
)

#: FR-76, ADR-0042: the seeding run and each seeded entry's provenance are written once by the
#: loader, which runs as the app role, and never edited or removed by any path (ADR-0010: "never an
#: editable field").
GRANT_SEED_IMPORT_SQL = "GRANT SELECT, INSERT ON TABLE seed_import TO nptc_app;"
REVOKE_SEED_IMPORT_WRITE_SQL = "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE seed_import FROM nptc_app;"
GRANT_ENTRY_SEED_PROVENANCE_SQL = "GRANT SELECT, INSERT ON TABLE entry_seed_provenance TO nptc_app;"
REVOKE_ENTRY_SEED_PROVENANCE_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE entry_seed_provenance FROM nptc_app;"
)

#: NFR-45, ADR-0043: an acceptance is a fact about a past moment, so it is never edited or removed.
#: The prior record of what a user accepted is kept even after they accept a later version.
GRANT_TERMS_ACCEPTANCE_SQL = "GRANT SELECT, INSERT ON TABLE terms_acceptance TO nptc_app;"
REVOKE_TERMS_ACCEPTANCE_WRITE_SQL = (
    "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE terms_acceptance FROM nptc_app;"
)
