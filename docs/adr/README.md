# Architecture decision records

MADR-lite: enough structure to be useful, not enough to be a chore. Each ADR records a
decision, the alternatives considered, and the consequences — so the reasoning survives
staff turnover and a decision is superseded explicitly rather than silently relitigated.

## When to write one

When a design decision has a rejected alternative worth remembering — see the
"documentation impact" table in [CONTRIBUTING.md](../../CONTRIBUTING.md). Not every
decision needs one; a decision the PRD already argues in full only needs an ADR if this
codebase adds something the PRD does not already settle (see ADR-0001, which mostly
records and cross-references the PRD's own argument rather than repeating it).

## Format

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-MMMM
**Date:** YYYY-MM-DD

## Context
## Decision
## Rejected alternatives
## Consequences
```

Number sequentially. Never delete or renumber a past ADR.

**Reversing the decision itself** — choosing a different technology, rule, or approach —
gets a new ADR that supersedes the old one; update the superseded ADR's `Status` line, but
leave its `Context`/`Decision`/`Consequences` as the historical record of what was decided
and why at the time.

**Correcting a stated fact within a decision that has not changed** — e.g. a version
number in a table that was wrong or has since been narrowed, a broken link, a typo — may be
edited in place, with a dated `## Amendments` section appended (one line per correction:
what was stated, what it's now, why) so the correction itself is visible in `git blame`
without a whole new ADR for something that isn't a new decision. See ADR-0001's Amendments
section for the shape.

If it's unclear which of the two a change is, it's a reversal: supersede.

## Index

| ADR | Title |
|---|---|
| [0001](0001-technology-stack.md) | Technology stack |
| [0002](0002-requirement-evidence-without-a-test.md) | Requirement evidence without a pytest test |
| [0003](0003-terminology-client-in-shared.md) | Terminology client in shared/, with httpx as its first runtime dependency |
| [0004](0004-informational-band-and-code-level-band-assignment.md) | A fourth, non-blocking `INFORMATIONAL` band; band assigned from the finding code, never from content |
| [0005](0005-sweep-chunk-size-and-concurrency-defaults.md) | Batch sweep defaults — chunk size 300, delta concurrency 4, first pass sequential |
| [0006](0006-designation-reconciliation-strategy.md) | Designation reconciliation strategy — local-first classification, a monotone server probe, and a workbook-scoped index for the "wrong concept" outcome |
| [0007](0007-misspelling-detection-heuristics.md) | Misspelling detection heuristics — banded Levenshtein in `shared/`, no new dependency, thresholds as constants |
| [0008](0008-specimen-inspection-strategy.md) | Specimen inspection strategy — ECL set-membership over the `Has specimen` attribute, a hand-typed + server-augmented specimen table, and a coverage audit for what the table doesn't cover |
| [0009](0009-defect-report-structure-and-cell-references.md) | Grouped defect report structure and a structured `CellRef` location — Band → FindingCode grouping, no CSV, `report.json` schema 7 |
| [0010](0010-import-dataset-format.md) | Import dataset format and the transform's role in minting `business_key` |
| [0011](0011-database-migration-foundation.md) | Database migration foundation — Alembic config in `pyproject.toml`, the least-privilege `nptc_app` role proven by refusal tests, the testcontainers integration harness, and the reflection-fingerprint round-trip check |
| [0012](0012-property-registry-storage-and-validation.md) | Property registry storage and validation — `PropertyDefinition` as a conventional relational table, row-per-value `PropertyValue`, in-process JSON Schema derivation keyed on `(key, row_version)`, and the FR-13 partial-index naming scheme |
| [0013](0013-datatype-handler-registry.md) | Datatype handler registry design — an eleven-member `DatatypeHandler` Protocol, `ControlKind` naming form controls after the interaction rather than the datatype, and a pure-`ast` guard banning datatype dispatch outside `registry/datatypes/` |
| [0014](0014-keycloak-realm-as-code.md) | Keycloak realm as code, imported on compose up — hand-authored `nptc-realm.json`, no application roles in the realm, ephemeral storage, and a client-level audience mapper on `nptc-frontend` instead of a shared client scope |
| [0015](0015-internal-user-identity-and-account-closure.md) | Internal user identity and account closure — `app_user`/`user_identity` split, no `role` column, and pseudonymise-never-delete on closure |
| [0016](0016-server-side-jwt-verification.md) | Server-side JWT verification — `pyjwt[crypto]` plus a thin JWKS-outage/refresh-cooldown wrapper, OIDC discovery over string-concatenated endpoints, and the payload (not header) `typ` check |
| [0017](0017-audit-hash-chain.md) | Audit hash chain construction — a `pg_advisory_xact_lock`-serialised append path, `GENERATED ALWAYS AS IDENTITY` sequence excluded from the digest, and a write-time self-check re-read |
| [0018](0018-field-level-audit-diffing.md) | Field-level audit diffing — a declared allow/deny/ignore column policy per model, SQLAlchemy attribute-history diffing, and NFR-26 redaction by field name |
| [0019](0019-permission-framework.md) | Permission framework — permissions and role→permission mappings as code (never database rows), `user_role` grants as the only new table, own/any and quota matrix qualifiers split by mechanism, and an application-level, row-locked last-administrator guard |
| [0020](0020-frontend-router.md) | Frontend router — TanStack Router, a hand-written code-based route table over file-based routing, raw-string search serialisation bypassing `qss`'s numeric coercion (FR-06), and idempotent `validateSearch` |
| [0021](0021-browser-side-pkce-login.md) | Browser-side PKCE login — the SPA performs the code exchange (over a backend/BFF callback), tokens held in memory only with no refresh token, silent `prompt=none` renewal, and the realm's browser flow restructured into satisfiable LoA 1 and LoA 2 subflows |
| [0022](0022-designation-storage.md) | Designation storage — the three preferred-term-shaped strings and where each lives, `designation` as catalogue-side-only storage never mirroring a served label, and clean-the-normalisable/reject-the-ambiguous term hygiene at entry |
| [0023](0023-database-level-sctid-validation.md) | Database-level SCTID validation via a SQL function — an `IMMUTABLE` pure predicate referenced from `code_binding`'s own `CHECK`, with an exhaustive parity test against `nptc_shared.sctid` (rejected: a regex-only `CHECK`, an unrolled inline expression, a validating trigger) |
| [0024](0024-catalogue-search-and-pagination.md) | Catalogue search and pagination — `pg_trgm` similarity over an `IMMUTABLE`, schema-qualified `unaccent` wrapper (rejected: `tsvector`, which scores a typo at zero), and keyset pagination with a derived, non-opaque cursor throughout (rejected: `OFFSET`, which silently skips or repeats rows under concurrent writes) |
| [0025](0025-frontend-styling.md) | Frontend styling — Tailwind CSS v4 with CSS-first `@theme` configuration (rejected: plain CSS with custom properties, CSS Modules) |
| [0026](0026-form-primitives-without-a-form-library.md) | Form primitives without a form library — props-in/props-out components with the caller owning field state and validation (rejected: `react-hook-form` + `zod`, a form context that wires errors to fields implicitly, a custom `role="listbox"` select), a group's summary anchor on its first option's input, and `RadioGroup`'s own roving tabindex |
| [0027](0027-cast-safe-numeric-index-expression.md) | Cast-safe numeric index expression via a SQL function — indexed in this table by #149, which found the row missing while adding its own |
| [0028](0028-registry-read-permission.md) | A member-tier `registry.read` permission, distinct from `catalogue.browse` and `registry.manage` — closes issue #55/PR #223's round-2 finding that the registry `GET` routes had drifted to fully public |
| [0029](0029-domain-logic-at-the-browser-boundary.md) | Domain logic at the browser boundary — mirror the function in TypeScript and share the Python test fixtures, gated on three conditions (rejected: splitting server-side, dropping the paste affordance, generating the TypeScript, a PR review comment) |
