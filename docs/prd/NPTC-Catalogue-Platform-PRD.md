# National Pathology Test Catalogue: Catalogue Maintenance Platform

## Product Requirements Document

| | |
|---|---|
| **Document** | NPTC Catalogue Maintenance Platform PRD |
| **Version** | 0.2 (Open issues resolved; terminology claims verified against Ontoserver) |
| **Date** | 4 August 2026 |
| **Author** | Matt Cordell, AEHRC-HDSI, CSIRO |
| **Status** | Draft. Eleven of the fifteen open issues in v0.1 are now closed. Four remain, listed in Section 15. |
| **Changes in v0.2** | Open issues OI-2 to OI-5 and OI-8 to OI-14 closed by decision. All SNOMED claims verified against SNOMED CT-AU via Ontoserver. Reviewer and Observer roles added, with an authoritative permission matrix. Speculative LOINC and panel hooks removed. `Discipline` and `Subgroup` specified as governed local code systems. NCTS handover simplified to file delivery with no integration. Designations now stored **exactly as served**, with tag stripping confined to the spreadsheet renderer. New requirement that every code binding be subsumed by `71388002` \|Procedure\|, verified against the sample. |
| **Governance** | Informs the NPTC Technical and Standards Working Group (TSWG), reporting to the NPTC Steering Committee |

### Reading guide for the development team

Requirements are identified as `FR-nn` (functional) and `NFR-nn` (non-functional). Each carries a priority:

- **MUST**: required for the proof of concept to be considered complete.
- **SHOULD**: required before any production adoption, may be deferred within the PoC.
- **MAY**: desirable, explicitly out of the PoC budget.

**These bands set scheduling priority only.** The keywords MUST, MUST NOT, SHOULD and MAY appearing *inside* a requirement's text carry their ordinary RFC 2119 force and describe how the requirement behaves once it is in scope. A SHOULD-priority requirement containing "MUST NOT" means: this may be deferred, but if it is built, that constraint is absolute. The two are independent, and a SHOULD-band requirement is not weakened by deferral to a later phase.

Requirement numbers are stable identifiers and are not sequential within sections. Where a requirement was added after first drafting, it appears in the section it belongs to and keeps the number it was assigned.

Section 15 lists open issues. Several are genuine design decisions that have not been made yet, and are marked as such rather than papered over. Do not infer a decision from silence.

---

## 1. Executive summary

The Royal College of Pathologists of Australasia Quality Assurance Programs (RCPA-QAP) maintains the Standardised Pathology Informatics in Australia (SPIA) Requesting terminology: a curated set of standardised pathology test names bound to SNOMED CT-AU concepts. It is published approximately bi-monthly as a Microsoft Excel workbook, which the National Clinical Terminology Service (NCTS) transforms into a SNOMED CT reference set and a FHIR ValueSet.

The spreadsheet is a hand-crafted artefact of a human editorial process. It was never designed for machine consumption, and after more than a decade of manual curation it carries an accumulated defect load that is visible in its own revision history: release notes recording "Fixed random SCT code errors (Excel truncation/extra digits)", "Rounding errors for some SCT codes fixed", "Removal of duplicates" and "Corrections to eight SNOMED CT ID errors", and per-entry history notes such as "Incorrect SCT Code fixed". A 50-row sample supplied for this analysis contains nine invisible-character defects, five compound free-text values in a field that should be coded, one synonym that collides with another entry's preferred term, and six SNOMED identifiers that Excel is structurally incapable of storing as numbers without corrupting them. Section 16 catalogues these in full.

Separately, the process for adding tests to the set runs through working groups whose deliberations are opaque to the wider pathology community. Implementers cannot see what has been proposed, what is under consideration, or why something was rejected.

This document specifies a web platform that makes the catalogue the authoritative machine-readable source of truth, replaces the spreadsheet with a generated export, opens the submission pipeline to the community under moderated governance, and continuously validates the catalogue's terminology bindings against the live SNOMED CT-AU and International editions.

The platform is scoped as a proof of concept, but is specified to a standard that permits adoption as-is if the community sees value in it. That constraint drives several decisions in this document, most notably on identity and audit, where retrofitting is materially more expensive than building correctly the first time.

---

## 2. Problem statement

### 2.1 The spreadsheet is not a data structure

The current workbook conflates three distinct things: the catalogue content, the editorial history, and the publication artefact. Because it is edited by hand in Excel, the tool itself introduces defects.

The most instructive example is SNOMED identifier corruption. Excel carries numeric values to a maximum of **15 significant decimal digits**, and silently zeroes everything beyond. (The underlying IEEE 754 double holds about 15.95 decimal digits, so 15 is the guaranteed-safe ceiling; Excel truncates to it rather than rounding at the binary boundary.)

Australian extension SCTIDs are **not a fixed length**. The sample contains extension identifiers of 15, 16 and 18 digits across two Australian namespaces, for example `873871000168106` (15), `1393151000168101` (16) and `933434771000036107` (18). **Any SCTID of 16 digits or more entered into a numeric cell is silently corrupted.** Six of the fifty sample codes are of that length, and all six survive only because they happen to be stored as text.

This matters for the build, not just the diagnosis: a validation rule written against "18-digit codes" would pass a corrupted 16-digit identifier. The workbook's own revision history records this failure mode twice in production, in February 2025 ("Rounding errors for some SCT codes fixed") and March 2026 ("Fixed random SCT code errors (Excel truncation/extra digits)").

This is not a discipline problem that better editorial care would solve. It is a property of the tool. The mitigation is to stop using a spreadsheet as the master.

### 2.2 There is no validation loop

SNOMED CT concepts are inactivated at each release. The catalogue's bindings can therefore silently rot between publications, and nothing in the current process detects this except a human noticing. The revision history shows corrections being applied reactively, sometimes years after the underlying change.

The problem is compounded by the release cadence mismatch: a concept inactivated in the International Edition published on 1 May does not become visible in SNOMED CT-AU until the AU release on 31 May. The current process has no mechanism to act on that foreknowledge.

### 2.3 The pipeline is opaque

Test submissions are handled inside working groups. A laboratory that proposes a test has no visibility into its progress, and the wider community cannot see what is already under consideration. This produces duplicate proposals and erodes confidence in the process. Three separate releases in the revision history record duplicate removal.

### 2.4 Derived data is maintained by hand

The `Version` and `History` columns record when and why each row changed. Both are typed by hand and both have drifted. In the sample, the `Version` column contains three different data types across 50 rows: 23 integers, two floats, and 25 dates, because the convention migrated from a semantic version to a release date without anyone migrating the existing values.

The `Length` column behaves differently and is worth separating out. Every cell is a live `=LEN()` formula, so it has not drifted at all. It is instead faithfully computing the wrong thing: it counts the trailing non-breaking spaces catalogued in Appendix A.1, so it currently measures the defect along with the term. Correcting the whitespace will change the published `Length` for roughly one entry in five.

All three values are derivable, and none of them should be a stored, editable field.

---

## 3. Goals and non-goals

### 3.1 Goals

| ID | Goal | How it is measured |
|---|---|---|
| G1 | Eliminate the class of defects caused by spreadsheet editing | Zero identifier corruption, whitespace, and delimiter defects in generated exports. Verified by automated test. |
| G2 | Make the catalogue authoritatively machine-readable | Every published release is generated deterministically from the database and is reproducible byte-for-byte from a release ID and export configuration version. |
| G3 | Detect terminology drift before it reaches publication | No release can be published containing a code that is inactive, non-existent, or whose FSN differs from the stored value, in either target edition. |
| G4 | Make the submission pipeline visible to the community | Any authenticated user can see the state and history of any submission, including their own and others'. |
| G5 | Preserve a defensible record of every editorial decision | Every content change is attributable to an identified person, carries a stated reason, and cannot be altered or removed after the fact. |
| G6 | Remain adaptable without rebuilds | New captured properties can be added by an administrator through the UI, without a code change or schema migration. |

### 3.2 Non-goals

The following are explicitly out of scope. They are recorded here so that the development team does not build toward them speculatively.

- **Becoming a terminology server.** The platform consumes FHIR terminology services. It does not implement them. All SNOMED CT lookup, expansion and subsumption is delegated to Ontoserver.
- **Authoring SNOMED CT content.** Where a concept does not exist, the platform records and tracks a request. It does not create concepts.
- **LOINC reporting code management.** Noted as a future extension. No LOINC-specific structure is to be built now.
- **Associating request terms with LOINC reporting codes (panel definition).** Deferred. The requirements are not understood well enough to specify, and the TSWG's own use of the term "panel" is not yet settled.
- **Replacing the NCTS transformation pipeline.** The platform produces the export artefacts; RCPA-QAP delivers them to NCTS through the existing submission process; NCTS publishes reference sets, ValueSets and release archives through its existing pipeline unchanged. **There is no system-to-system integration with NCTS in this build** (decision closing OI-5). The consequence for the development team is that no NCTS interface specification, endpoint, credential or availability dependency is required anywhere.
- **Generality for the SPIA Reporting (LOINC) set.** The data model is optimised for requesting and carries no speculative accommodation for reporting content (decision closing OI-13). If a Reporting catalogue is wanted later, the sanctioned answer is **a second deployment of the same codebase with different seed content and different code system bindings**, not a generalised single instance. That answer should be given when the question arises, so that it is a plan rather than a surprise.
- **Clinical decision support.** Named in the TSWG Terms of Reference as a future value-add. Nothing in this build.

### 3.3 A note on panels

The SPIA Requesting set is a flat list of orderable items. Some of those orderables are, clinically speaking, panels (a request for a group of analytes). They are represented as single flat entries and require no compositional modelling. **No parent-child or composition structure is to be built.** The compositional question the TSWG is circling, associating request terms with the LOINC reporting codes that constitute them, is a distinct future problem and is out of scope per Section 3.2.

---

## 4. Users and roles

Six roles, ordered by privilege. The Reviewer and Observer roles were added in v0.2, closing OI-4.

### 4.1 Anonymous visitor

An unauthenticated member of the public. Typically an LIS vendor developer, a laboratory scientist checking a test name, or a clinician.

Can browse, search, filter and view approved catalogue entries and their codings. Can retrieve published release artefacts. Cannot see submissions, interest records, user identities, or anything not yet approved.

**Rationale for public read:** the catalogue's value depends on being citable and linkable from vendor documentation and implementation guides. Placing approved content behind authentication would undermine the purpose of a national standard.

### 4.2 Observer

An authenticated, named, **entirely read-only** account. Sees the submission pipeline and its states but cannot write anything at all: no submissions, no interest records, no comments.

**Why this role exists.** Without it, giving a jurisdictional representative or a vendor visibility into the pipeline means making them a Member, which also grants them the ability to submit and to influence prioritisation. Observer separates *seeing* from *participating*. It is the correct role for anyone whose position makes contribution inappropriate, and for anyone who simply needs accountable, audited visibility.

**FR-80 (MUST):** The Observer role MUST have no write capability of any kind. This MUST be enforced as an absence of permissions, not as UI suppression, and MUST be covered by a negative authorisation test for every write endpoint.

### 4.3 Provisional user

A newly registered, authenticated user who has not yet been vouched for.

Adds to Observer: create submissions, capped at five in total. **Cannot register interest in a submission.**

The cap is a spam and abuse control, not a quality judgement. It exists so that a compromised or throwaway account cannot flood the pipeline.

### 4.4 Member

A provisional user promoted by an administrator or a reviewer.

Adds: unlimited submissions subject to a rate limit of 20 per rolling hour (adjustable per user), and the ability to record interest in a submission (see FR-32 on why this is not called voting).

### 4.5 Reviewer

A working group member with editorial authority over the submission pipeline but **not** over the platform or the published catalogue.

Adds to Member:

- Move submissions through every state up to and including `Ready for approval`, and to `Rejected` and `Awaiting terminology`
- See submitter identities and the organisational breakdown of interest records
- Read and write internal comments (FR-95)
- Promote a Provisional user to Member
- Run on-demand terminology validation and acknowledge findings

**Explicitly withheld from Reviewer**, because these are the powers that change what the community receives or who holds authority:

- Transition a submission to `Approved`
- Cut or publish a release
- Edit a published catalogue entry
- Grant or revoke any role other than Provisional-to-Member
- Change the property registry, local code systems, or export configuration
- Suspend a user or override a rate limit

**FR-81 (MUST):** The boundary between Reviewer and Administrator MUST be enforced server-side per permission, and each withheld capability above MUST have a negative authorisation test asserting that a Reviewer is refused.

### 4.6 Administrator

RCPA-QAP staff and their delegates.

Adds everything: full catalogue editing, approval and release publication, user management including suspension and rate limit override, property registry and local code system management, export configuration, and access to the audit log.

**FR-01 (MUST):** An administrator may grant and revoke any role. The system MUST prevent removal of the last remaining administrator.

**FR-02 (SHOULD):** Administrator actions that are irreversible or high-impact (publishing a release, deprecating a property, demoting another administrator) require an explicit confirmation step that restates what will happen.

### 4.7 Permission matrix

Authoritative. Where prose elsewhere in this document disagrees with this table, the table wins.

| Capability | Anon | Observer | Provisional | Member | Reviewer | Admin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Browse and search approved catalogue | Y | Y | Y | Y | Y | Y |
| Retrieve published release artefacts | Y | Y | Y | Y | Y | Y |
| View pending submissions and states | . | Y | Y | Y | Y | Y |
| View interest counts | . | Y | Y | Y | Y | Y |
| Create submissions | . | . | Y (max 5) | Y (20/hr) | Y | Y |
| Propose amendments | . | . | Y (max 5) | Y (20/hr) | Y | Y |
| Register interest | . | . | . | Y | Y | Y |
| View property registry (read-only) | . | . | . | Y | Y | Y |
| Withdraw own submission before approval | . | . | Y (own) | Y (own) | Y (own) | Y (any) |
| View submitter identities | . | . | . | . | Y | Y |
| View who registered interest | . | . | . | . | Y | Y |
| Read and write internal comments | . | . | . | . | Y | Y |
| Transition submissions up to `Ready for approval` | . | . | . | . | Y | Y |
| Run validation, acknowledge findings | . | . | . | . | Y | Y |
| Promote Provisional to Member | . | . | . | . | Y | Y |
| Transition to `Approved` | . | . | . | . | . | Y |
| Edit published catalogue entries | . | . | . | . | . | Y |
| Cut and publish releases | . | . | . | . | . | Y |
| Manage property registry and local code systems | . | . | . | . | . | Y |
| Manage export configuration | . | . | . | . | . | Y |
| Grant or revoke Observer, Reviewer, Administrator | . | . | . | . | . | Y |
| Suspend users, override rate limits | . | . | . | . | . | Y |
| Read the audit log | . | . | . | . | . | Y |

## 5. Scope and phasing

Six phases. Each is independently demonstrable, which matters for a project seeking community buy-in.

| Phase | Content | Why it is ordered here |
|---|---|---|
| **P0** | Seeding transform, anomaly report and designation change report (FR-70 to FR-76, plus FR-79 and FR-97) | Standalone deliverable with immediate value. Run against the real spreadsheet, it tells RCPA-QAP what needs fixing before anything is built. It also de-risks the data model, because the transform will find structures the model does not yet handle. |
| **P1** | Core catalogue, browse and search, identity, audit log, property registry | The property registry is deliberately early. If it arrives late, the built-in fields get hard-coded and the system ends up with two parallel mechanisms for the same concept. |
| **P2** | Submissions, workflow states, interest signals, internal comments, user administration and the full role model | The community-facing value. The Reviewer and Observer roles land here because the permission matrix at 4.7 is what the workflow states are enforced against. |
| **P3** | Terminology validation against Ontoserver | Depends on P1 content existing. Independent of P2. |
| **P4** | Releases, exports, export configuration versioning | Depends on P1 and P3. Cannot publish what has not been validated. |
| **P5** | Hardening: accessibility audit, security review, performance, operational documentation | Not optional if adoption is a realistic outcome. |

P0 can be built in parallel with P1 by a different developer.

### 5.1 Planning scale

One set of figures, used consistently by every sizing and performance requirement in this document. Where a requirement needs a number, it uses these.

| Measure | Figure | Basis |
|---|---|---|
| Catalogue entries at seeding | ~5,000 | Order of magnitude of the current published SPIA Requesting set. **To be confirmed against the complete workbook during P0**; the supplied sample is 50 rows and cannot establish it. |
| Design ceiling for performance requirements | 20,000 | Four times the seeding figure. Pure headroom: it is not a growth forecast, and it is **not** an allowance for the Reporting set, which OI-13 places in a separate deployment. All NFR performance budgets are stated against this number so that a budget is never the thing that fails first. |
| Code validations per full sweep | 40,000 | Design ceiling multiplied by two editions. |
| Registered users | Low hundreds | A community of practice, not a consumer service. Anonymous read traffic is the larger volume and is cacheable. |

The gap between 5,000 and 20,000 is deliberate headroom. It is not an expectation of growth, and it MUST NOT be used to justify architectural complexity that 5,000 rows does not need.

---

## 6. Domain model

### 6.1 Core entities

```
User ──< UserIdentity                  (internal UUID never leaves the platform)
  │  └──< TermsAcceptance              (which version, when)
  │
  ├──< Submission ──< InterestRecord
  │         │
  │         ├──< SubmissionStateTransition   (actor + mandatory reason)
  │         └──< Comment                     (internal: Admin + Reviewer only)
  │
  └──< AuditEvent                      (append-only, hash-chained)

CatalogueEntry ──< PropertyValue >── PropertyDefinition
  │                                        │
  │                                        └── binding ──> ValueSet (SNOMED)
  │                                                    or > LocalCodeSystem
  ├──< Designation   (preferred term, synonyms)
  ├──< CodeBinding   (fsn + au_preferred_term, validated separately)
  └──< ValidationFinding  (open / acknowledged / resolved / superseded)

LocalCodeSystem ──< LocalCode ──< SnomedMap   (advisory, may be absent)
      (Discipline, Subgroup: RCPA-governed, versioned with releases)

Release ──< ReleaseMember >── CatalogueEntry   (immutable snapshot)
  │
  └──< ExportArtefact >── ExportConfigVersion
```

### 6.2 CatalogueEntry

The central entity. Deliberately split into **core columns** (first-class, indexed, constrained) and **registry properties** (uniform handling, extensible).

**Core columns.** These are structural. They are not administrator-definable, because integrity constraints, search indexes and export identity depend on them.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Internal. Never exposed in exports. |
| `business_key` | text, unique, immutable | Stable public identifier for the entry, independent of code or term. See FR-03. |
| `preferred_term` | text, not null | The RCPA preferred term. |
| `status` | enum | `draft`, `active`, `deprecated`, `withdrawn` |
| `specimen_unconstrained` | boolean, not null, default false | True where the test accepts any specimen. A **core column, not a registry property**, because `boolean` is deliberately not an available property datatype (FR-77) and this flag must be queryable and constrained. See FR-89. |
| `created_at`, `updated_at` | timestamptz (UTC) | |
| `row_version` | integer | Optimistic concurrency. See FR-38. |

**FR-03 (MUST):** Every catalogue entry MUST carry an immutable `business_key` assigned at creation and never reused. Neither the SNOMED code nor the preferred term is a stable identifier: the sample shows preferred terms changing repeatedly ("Preferred term changed from 'Acid fast bacilli culture'") and codes being corrected. Downstream consumers and the audit log need something that does not move. Format: `NPTC-` followed by a zero-padded sequence, for example `NPTC-000247`.

**Rationale worth stating plainly:** the absence of a stable row identifier is why the current spreadsheet's `History` column has to be free text. Once each entry has a key, history becomes a queryable relation.

**Removed in v0.2:** a `test_kind` discriminator (`single_analyte` / `panel` / `profile`) on this entity, and a `binding_role` discriminator (`primary_request_code`, present only to anticipate future LOINC bindings) on CodeBinding. Both were speculative, and the decision closing OI-13 was to optimise purely for requesting. The SPIA set is a flat list of orderables in which some items happen to be panels, and nothing in this build needs to distinguish them. Re-adding it later is a nullable column and a backfill, which is cheap. Building it now and never using it is not.

### 6.3 Designation

A term attached to an entry, replacing the semicolon-delimited `RCPA Synonyms` cell.

| Field | Notes |
|---|---|
| `entry_id` | FK |
| `term` | text, not null |
| `use` | enum: `preferred`, `synonym` |
| `language` | BCP-47, default `en-AU` |
| `status` | `active`, `retired` |

**FR-04 (MUST):** Synonyms MUST be modelled as separate rows, not as a delimited string. The sample demonstrates why: one entry uses a comma delimiter where every other entry uses a semicolon (`'ADA RBC, ADA red cells'`), one contains an empty synonym produced by a doubled delimiter (`'Zovirax;;Cyclir'`), and several have inconsistent whitespace after the delimiter. These defects are unrepresentable once the delimiter is gone.

**FR-05 (MUST):** On save, the system MUST detect collisions between a designation and a designation on a different active entry. Two classes, with **different enforcement**, stated explicitly here because FR-71 and the acceptance tests depend on it:

- **Severity: error. The save is rejected.** A synonym that exactly matches another entry's *preferred term*. This is an ordering-safety hazard: a requester searching that string sees two results with no way to distinguish them. The 50-row sample contains one instance (`'Adrenal Ab'` is the preferred term of one entry and a synonym of `'21-Hydroxylase Ab'`). Extrapolated across the full set, this is unlikely to be isolated.
- **Severity: warning.** The same synonym attached to multiple distinct entries. The sample contains nine, including `'ADA2'` attached to three separate adenosine deaminase entries differing only by specimen. Sometimes this is legitimate (a genuinely ambiguous abbreviation that the specimen disambiguates), so it warns rather than blocks, but it MUST be visible and MUST be resolvable to an acknowledged state so the same warning does not recur every save.

Collision detection MUST normalise case, Unicode whitespace and punctuation before comparison, otherwise the non-breaking-space defects will cause collisions to be missed.

**A consequence that must be planned for, not discovered.** The existing published catalogue already contains at least one error-class collision. Because FR-71 bands this as a data defect that aborts import, **the seeded baseline cannot be created until RCPA-QAP resolves those collisions editorially**. This is intended: seeding a national catalogue with a known ordering-ambiguity hazard would defeat the purpose. But it puts an editorial decision on the critical path for P0, and the schedule must reflect that. The P0 transform's `--report-only` mode exists precisely so the list can be produced and worked before the import is attempted.

### 6.4 CodeBinding

| Field | Notes |
|---|---|
| `entry_id` | FK |
| `system` | URI. `http://snomed.info/sct` |
| `code` | **text, always.** Never an integer type anywhere in the stack. |
| `fsn` | The Fully Specified Name **exactly as served, semantic tag intact**. See FR-82. |
| `au_preferred_term` | The SNOMED CT-AU preferred term, exactly as served. Stored and validated separately from `fsn`. |
| `edition_hint` | `au`, `int`, or `unknown`. Which edition the code is expected to resolve in. |
| `status` | `active`, `retired`. A binding is never deleted once published. |
| `replaced_by_binding_id` | Nullable FK to the binding that superseded this one. Populated on retirement. |
| `retirement_reason` | Free text, mandatory when status becomes `retired`. |

**FR-06 (MUST):** SNOMED CT identifiers MUST be stored, transported and exported as strings at every layer: database column, API payload, JSON serialisation, CSV cell and spreadsheet cell. The database column MUST carry a check constraint enforcing `^\d{6,18}$` plus Verhoeff check-digit validation.

**FR-07 (MUST):** The spreadsheet export MUST write the code cell with an explicit text format. An automated test MUST assert that SCTIDs of **16, 17 and 18 digits** each survive a write-then-read round trip through the generated `.xlsx` unchanged. Sixteen digits is the boundary case, because Excel's limit is 15 significant digits, and a test written only against 18-digit identifiers would not detect a 16-digit regression.

**FR-08 (SHOULD):** An entry SHOULD permit at most one active code binding. Where a code is being replaced following inactivation, the superseded binding is retained with `status = retired`, a `replaced_by_binding_id`, and a mandatory `retirement_reason`, preserving the audit trail.

**FR-82 (MUST):** The platform MUST store `fsn` and `au_preferred_term` **exactly as the terminology server returns them**, with the semantic tag intact on the FSN. No transformation at rest.

Validation is then plain string equality against the server, per designation:

| Check | Compares | Finding | Severity |
|---|---|---|---|
| FSN currency | Stored `fsn` against the served FSN, byte for byte after Unicode normalisation | `fsn_drift` | Error |
| Preferred term currency | Stored `au_preferred_term` against the preferred term for language reference set `en-x-sctlang-32570271-00003610-6` | `preferred_term_drift` | Warning |

**Why store as served rather than pre-stripped.** A stored value that has been transformed cannot be distinguished from one that has not. That ambiguity is the entire source of the tag-stripping hazard: apply the strip twice to `Microscopy (acid fast bacilli) (procedure)` and you silently get `Microscopy`. Storing as served makes the state of every value unambiguous by construction, makes validation a single equality test with no normalisation of tags, and makes the export the only place a transformation happens.

**Severity rationale.** `fsn_drift` is an error because a changed FSN can indicate the concept's meaning was revised. `preferred_term_drift` is a warning because it is usually AU editorial improving a label: 9 of 50 sample rows are already in that state, and erroring on all of them would create a standing backlog that trains reviewers to dismiss the serious findings alongside it.

**FR-83 (MUST):** Semantic tag removal happens **only in the export renderer**, never in storage or in validation. The rule is: remove the final parenthesised group from the FSN, exactly once. Because the input is always a server-served FSN, it always carries exactly one tag, and the rule needs no list of known tags.

**The double-strip failure is prevented structurally, not by inspection.** The rule has exactly one call site, in the export renderer, and its input is always read directly from the `fsn` column, which by FR-82 always holds a served FSN. There is no code path that can feed it an already-stripped value. That is the whole reason for storing as served, and it is a stronger guarantee than any output check.

Two defensive assertions on top, because this runs unattended on every release:

- The input MUST end with a parenthesised group. If it does not, the value is not a served FSN and the export MUST fail loudly rather than publish it.
- The result MUST be non-empty.

`391483001` is the regression fixture: FSN `Microscopy (acid fast bacilli) (procedure)` MUST render as `Microscopy (acid fast bacilli)`, which correctly retains a parenthesised phrase. The accompanying test MUST assert that a second application of the rule to that output is never reached on any code path, rather than asserting that a second application would be harmless, because it would not be.

**FR-84 (MUST):** Every code binding MUST be subsumed by `71388002` \|Procedure (procedure)\|. A binding that is not is an **error** that blocks publication and requires a human to resolve, either by rebinding to a procedure concept or by explicitly justifying the exception.

**This is the primary hierarchy check and it replaces the vaguer "procedure or observable" scope test in v0.1.** It is structural rather than textual, so it catches an observable entity or a clinical finding accidentally bound as a request code, which is the failure mode that matters.

**Implementation MUST be a single batch call, not one `$subsumes` per code.** The ECL idiom returns exactly the violations and nothing else:

```
(<code1> OR <code2> OR ... OR <codeN>) MINUS <<71388002
```

An empty expansion means every code complies. This was used to verify the sample: all 50 codes returned zero violations (Appendix A.10).

**FR-98 (MUST):** Every artefact that publishes a SNOMED label MUST state explicitly which designation it carries and whether the tag is stripped. Defaults:

| Artefact | Field | Tag |
|---|---|---|
| SPIA spreadsheet | `fsn` | Stripped (FR-83), preserving the current published appearance exactly |
| Simple CSV | `fsn` | **Intact.** It is a new machine-facing artefact and the tag is meaningful to a consumer resolving the concept. |
| FHIR CodeSystem supplement | Designations carried natively | Not applicable |
| Search index | `fsn`, `au_preferred_term`, RCPA preferred term, and all synonyms | Both forms indexed, so a user who searches any label ever published reaches the entry |

Both tag settings are export-configuration values (FR-66), so either default can be changed without a code change. The AU preferred term is available as an addable column in the spreadsheet and CSV.

**FR-99 (SHOULD):** Where a binding is subsumed by `<<71388002` but its semantic tag is **not** `(procedure)`, raise a **warning**, not an error.

The two checks are not equivalent, which is worth stating because it is easy to assume they are. `71388002` subsumes `243120004` \|Regime/therapy (regime/therapy)\|, verified, so a concept can be a structurally valid procedure and still carry a different tag. Such a binding is legitimate SNOMED but odd for a pathology test request, so it warrants a look rather than a block.


### 6.5 The property registry

This is the highest-risk requirement in the brief and warrants an explicit design position.

**The risk.** "Administrators can add new properties with arbitrary datatypes" is, implemented naively, either runtime DDL (an administrator's UI action alters the schema, which breaks migrations, backups and any ORM) or classic Entity-Attribute-Value (which destroys query performance, defeats constraints, and makes every export a special case). Both are well-documented ways to produce a system that becomes progressively harder to change, which is the precise opposite of the stated intent.

**The recommended design.** A property *registry* plus typed JSONB storage plus per-property JSON Schema validation.

`PropertyDefinition`:

| Field | Notes |
|---|---|
| `key` | Immutable machine name, `snake_case`. Never changes. |
| `label` | Human-facing. Changeable. |
| `datatype` | `code`, `string`, `decimal`, `positiveInt`, `url` |
| `cardinality` | `0..1`, `1..1`, `0..*`, `1..*` |
| `scope` | `submission`, `maintenance`, or both |
| `required_for_submission` | boolean |
| `required_for_publication` | boolean |
| `binding` | Present only when `datatype = code`. See FR-10. |
| `filterable` | boolean. Drives index generation. |
| `origin` | `system` or `admin_defined` |
| `status` | `active`, `deprecated`. **No delete.** See FR-11. |
| `display_order` | integer |

`PropertyValue` stores `(entry_id, property_key, value JSONB, ordinal)`.

**FR-09 (MUST):** Adding, deprecating or amending a property definition MUST NOT require a schema migration, application restart, or code deployment.

**FR-10 (MUST):** A property of datatype `code` MUST carry a terminology binding, not merely a datatype. The brief lists `Code` alongside `String` and `decimal` as though it were a primitive of the same kind. It is not. A code without a value set is an uncontrolled string with extra steps. The binding MUST specify:

- `binding_target`: one of `value_set` or `local_code_system`. Local code systems (FR-90) are validated internally against the platform's own `LocalCode` table, because Ontoserver does not hold them.
- `value_set_uri`: required when `binding_target = value_set`. Either an explicit ValueSet URL or a SNOMED implicit ValueSet using ECL, for example `http://snomed.info/sct?fhir_vs=ecl/%3C123038009` (URL-encoded `<123038009`, descendants of Specimen excluding the grouper itself; use `%3C%3C` for `<<` where the grouper is a valid value)
- `strength`: `required` (validation fails if outside the set), `extensible` (permitted outside with a recorded justification), or `example`
- `edition`: which SNOMED edition the value set resolves against

Values whose target is a SNOMED value set are validated at save time via Ontoserver `$validate-code` and re-validated during the periodic sweep. Values whose target is a local code system are validated internally, and the sweep raises `local_code_retired` where a value now points at a deprecated local code.

**FR-11 (MUST):** A property definition MUST NOT be deletable once any value has been recorded against it or once it has appeared in any published export. It can only be deprecated. Deprecated properties are hidden from data entry forms, retained on existing entries, and remain available to export configurations for historical releases. **Rationale:** deleting a property silently changes the meaning of every past release that contained it, and breaks any downstream consumer parsing that column.

**FR-12 (MUST):** A property `key` MUST be immutable. `label` may change freely. Exports address properties by key; the label is presentation only. Without this rule, renaming a property in the UI silently renames a column in the published CSV.

**FR-77 (SHOULD):** The design MUST accommodate the addition of new *datatypes* to the registry, not only new properties, without a rebuild. The source notes call this out explicitly as a flexibility requirement.

The recommended design already supports it cheaply, and the requirement is to preserve that rather than to build a datatype editor now. Concretely: datatype handling MUST be implemented as a registry of handlers, each supplying a JSON Schema fragment, a validation routine, a form control, and a serialiser for each export format. Adding `boolean`, `dateTime` or `Coding` then means registering one handler, not modifying the storage layer, the property registry, the export engine or the search layer.

**What this rules out:** `switch` statements on datatype scattered through the codebase. If adding a datatype requires edits in more than the handler module and its tests, the requirement has not been met. A single test that registers a synthetic datatype and exercises it end to end through save, validate, filter and export is the check.

Adding a datatype remains a code change, deliberately. Allowing administrators to define arbitrary datatypes at runtime would mean user-authored validation logic, which is a different and much larger problem.

**FR-13 (SHOULD):** Properties marked `filterable` SHOULD have a supporting index created automatically (a Postgres expression index on the JSONB path, or a GIN index for multi-valued properties). Non-filterable properties are excluded from faceted search UI.

**Which fields go in the registry.** The current spreadsheet's `Discipline`, `Subgroup`, `Specimen` and `Usage guidance` are defined as `origin = system` properties in the registry, seeded at install. **`Length` is not a registry property**, because registry properties are stored and `Length` must never be stored (FR-85). It is computed in the export and presentation layers only. They are handled by exactly the same code path as administrator-defined properties. This is the dogfooding that keeps the extensibility mechanism honest: if it is good enough for the built-in fields, it is good enough for new ones. Core columns (Section 6.2) stay out of the registry because constraints and indexes depend on them.

**On `Length`.** Confirmed with RCPA-QAP: it is simply the character count of the RCPA preferred term. Its original intent was to monitor and discourage terms too long for some laboratory information systems to hold, **but no maximum has ever been specified**. It is therefore currently a number that measures something nobody acts on.

**FR-85 (MUST):** `Length` MUST be computed from the preferred term and MUST NOT be storable or editable (FR-24). It continues to be published, for continuity.

**FR-86 (SHOULD):** The platform SHOULD support a configurable maximum preferred-term length. It MUST be **unset by default**, and when set it MUST **warn rather than block**, because an existing catalogue will contain terms that exceed any threshold RCPA subsequently chooses, and a hard block would make those entries uneditable.

**FR-87 (SHOULD):** The platform SHOULD report the **distribution** of preferred-term lengths across the catalogue: a histogram, the maximum, and the count of entries that would be affected at each candidate threshold. Providing this turns "what limit should we set" from a guess into a decision made against the actual data, and it is a handful of lines of code.

**Migration consequence to communicate.** Because the current `=LEN()` formulas count the trailing non-breaking spaces documented in Appendix A.1, cleaning that whitespace will reduce the published `Length` for roughly one entry in five. Consumers should be told in the cutover release notes.

**Remaining decision.** RCPA-QAP nominates the maximum, informed by FR-87's distribution report and by whatever LIS constraint originally motivated the column. This is the narrowed form of OI-1.

### 6.6 Discipline, Subgroup and Specimen

The source notes proposed binding `Discipline` to `<394595002` and `Specimen` to `<123038009`. Both were verified against SNOMED CT-AU using Ontoserver in preparing v0.2 of this document. The two produced opposite results.

#### Specimen: bind to SNOMED, with one modelling addition

`<123038009` covers the sample's specimen vocabulary better than expected. Values that were flagged as doubtful in v0.1 all resolve:

| Sample value | SNOMED CT-AU | Verified |
|---|---|---|
| `24 hr urine` | `276833005` \|24 hour urine specimen\| | Yes. Timed collections are codable; the v0.1 concern was unfounded. |
| `Platelet poor plasma` | `119362004` \|Platelet poor plasma specimen\| | Yes |
| `Swabs` | `257261003` \|Swab\|, with a large subtype hierarchy | Yes |

**FR-88 (MUST):** `Specimen` MUST be a coded property with cardinality `0..*`, bound to a value set rooted at `123038009`, replacing the semicolon-delimited string. The multi-valued case is the norm, not the exception: one sample entry carries seven specimens.

**FR-89 (MUST):** The value `'Any'` MUST NOT be represented as a specimen code. It is the absence of a specimen constraint, not a specimen. It MUST be modelled as **zero specimen values plus the `specimen_unconstrained` core column** defined in Section 6.2 (decision closing OI-2). It is a core column rather than a registry property because `boolean` is not an available property datatype and the flag needs a not-null constraint and an index.

The boolean is what makes the model honest. Without it, an entry with no specimens is ambiguous between "this test accepts any specimen" and "nobody has filled this in yet", and those are different facts that a consumer must be able to tell apart. Exports render the flag as `Any` for continuity with the current published format.

One value remains unresolved, and it is editorial rather than technical: `Fluids` is vague enough that a terminologist should choose the intended concept or split it. It should be resolved during the P0 designation pass. `Any` is fully determined by FR-89 and needs no editorial input.

#### Discipline and Subgroup: governed RCPA local code systems

**The verification result is decisive, and stronger than the argument made in v0.1.** Expanding `<394595002` against SNOMED CT-AU returns exactly 17 concepts. Against the six `Discipline` values in the sample:

| RCPA discipline | SNOMED CT-AU candidate | Verdict |
|---|---|---|
| Chemical pathology | `394596001` \|Chemical pathology\| | Exact match |
| Haematology | `394916005` \|Haematology (specialty)\| | Exact match |
| Immunopathology | `394598000` \|Immunopathology\| | Exact match |
| Microbiology | `408454008` \|Clinical microbiology\| **or** `394820005` \|Medical microbiology\| | **Ambiguous.** Two candidates, neither named plainly "Microbiology". |
| Molecular | No concept named for it. `1236877003` \|Genetic pathology\| **is** a direct child of `394595002`, and `708179009` \|Molecular pathology service\| exists outside the hierarchy. See the note below on why neither is a match. | **No match.** |
| Serology | No **specialty** concept exists. The nearest is `708188000` \|Serology service\|, a healthcare service. SNOMED does contain serology *procedures* (`86810002` \|Qualitative serology procedure\|, `252332007` \|Autoantibody serology\|) and serology *findings*, but a procedure is not a discipline and cannot serve as a Discipline value. | **No match.** |

A subsumption test confirms that both service candidates sit outside the hierarchy: `check_subsumption(394595002, 708179009)` and `check_subsumption(394595002, 708188000)` both return **not-subsumed**. They are healthcare *service* concepts.

**On `1236877003` \|Genetic pathology\|, which is a genuine member of the hierarchy.** It is not a match for RCPA's `Molecular`, and mapping it would be a mistake rather than an approximation. RCPA treats genetic pathology as a **separate discipline** from molecular: the workbook's May 2025 revision note records "Addition of new Genetic patho[logy]" content alongside, not instead of, molecular content. Molecular in RCPA usage is a method-based grouping covering nucleic acid testing across microbiology, virology and haematology, most of which is not genetic testing at all. Binding `Molecular` to `Genetic pathology` would silently reclassify a large body of infectious-disease testing as genetic testing. That is worse than leaving it unmapped, which is why FR-91 requires the gap to stay visible.

The consequence is that **the six RCPA disciplines cannot be expressed within any single coherent SNOMED value set.** Covering them would require a union of pathology specialties and healthcare services, which is semantically incoherent, and Serology would still have no genuine match.

Two further facts already established in Appendix A reinforce this. The classification is actively being recut: nucleic acid tests moved from Serology to Microbiology in December 2024 and are classified as Molecular by June 2026, and the two categories with no SNOMED match are precisely the two involved in that churn. And five of fifty sample entries hold compound values such as `'Chemical pathology or Haematology'`, which are two values expressed as prose because the cell holds one string.

**FR-90 (MUST):** `Discipline` and `Subgroup` MUST each be implemented as a **governed local code system owned by RCPA-QAP** (decision closing OI-3 and OI-11), with:

- A stable code, a display term, a definition, and a status of `active` or `deprecated`
- Version history tied to catalogue releases, so that a past release's classification is reproducible
- Cardinality `0..*` on the catalogue entry, which resolves the compound `'X or Y'` values as multiplicity rather than requiring a concept that does not and should not exist
- Administrator-only management, using the same audit and changelog discipline as every other content change

**FR-91 (SHOULD):** A published **non-authoritative map** from the local `Discipline` code system to SNOMED CT concepts, for consumers who want a SNOMED view. The map MUST be explicit that it is advisory, MUST record a match strength per row, and MUST leave `Molecular` and `Serology` **unmapped rather than approximately mapped**. An honest gap is more useful to an implementer than a plausible-looking wrong mapping, and mapping `Molecular` to a healthcare service concept would be exactly that.

**FR-92 (SHOULD):** `Subgroup` migration MUST reconcile two problems visible in the sample before the code system is populated: it mixes classification axes (`Coagulation` and `Drug measurement` classify by analyte or clinical domain, while `Microbial Culture`, `Mycobacteria culture` and `Mycobacterial microscopy` classify by method), and it is inconsistently pluralised. The transform reports the distinct values; RCPA-QAP decides the vocabulary. Until then, migrate the existing strings verbatim as provisional codes rather than guessing at a structure.

**On `Usage guidance`:** retained as free text (decision closing OI-12). It is empty throughout the sample but is an intended editorial field, not a dead one, and there is no cost to carrying it.

---

## 7. Functional requirements: catalogue consumption

### 7.1 Browse and search

**FR-14 (MUST):** Anonymous and authenticated users can search the approved catalogue across RCPA preferred terms, synonyms, the stored `fsn`, the stored `au_preferred_term`, and the SNOMED code, in a single query field (see FR-98). A user typing `49466006`, `ACTH`, `Adrenocorticotropic hormone` or `Corticotropin` MUST reach the same entry.

**FR-15 (MUST):** Search MUST tolerate typographical error and word-order variation. At the planning scale (Section 5.1), PostgreSQL full-text search combined with `pg_trgm` trigram similarity is sufficient and appropriate. **Do not introduce Elasticsearch or a vector store.** The operational cost of a second data store is not justified by a dataset this size, and it creates a consistency problem between the search index and the audit-critical database.

**FR-16 (MUST):** Results can be filtered by any property marked `filterable`, presented as faceted filters with result counts. At minimum: discipline, subgroup, specimen, and entry status.

**FR-17 (MUST):** Exact-code lookup MUST be unambiguous and MUST be reachable by direct URL: `/catalogue/{business_key}` and `/catalogue/code/{system_token}/{code}`, where `system_token` is a short registered alias (`sct` for `http://snomed.info/sct`) rather than the URI itself, which cannot occupy a path segment unencoded. A query form `/catalogue/lookup?system={uri}&code={code}` MUST also be accepted for callers holding the full URI. Every entry has a stable, linkable, citable URL. This is what allows vendors to reference catalogue entries from their own documentation.

**FR-18 (SHOULD):** Search results indicate whether an entry's terminology binding has an open validation finding, without exposing internal validation detail to anonymous users. A user should not unknowingly implement a code that RCPA already knows is inactive.

**FR-19 (SHOULD):** An entry's detail page shows its full change history: every published release in which it appeared, what changed at each, and the changelog note the administrator supplied. This is the `History` column, made structured and queryable.

### 7.2 Read API

**FR-20 (MUST):** A documented, versioned, read-only JSON API over the approved catalogue, available without authentication, with the same content as the public UI. OpenAPI 3.1 specification published and served.

**Rationale:** LIS and PMS vendors are a primary audience. Requiring them to scrape HTML or download a spreadsheet to consume a national standard is the problem this platform exists to solve. The API costs almost nothing given the application already needs one.

**FR-21 (SHOULD):** Published FHIR artefacts for each release are served as static files at stable, versioned URLs. The platform serves these as files. It MUST NOT implement FHIR terminology operations; that is Ontoserver's role and duplicating it would be both wasteful and a source of divergence.

**FR-22 (SHOULD):** Anonymous API access is rate-limited by IP. Limits generous enough not to obstruct legitimate bulk retrieval, and the response MUST direct heavy consumers to the bulk release artefacts instead.

---

## 8. Functional requirements: contribution and workflow

### 8.1 Submission

**FR-23 (MUST):** Provisional users, Members, Reviewers and Administrators can submit a proposed new test. **Observers cannot** (FR-80). "Authenticated" is not the test; the permission is, per the matrix at 4.7. The form is generated from the property registry: every property with `scope` including `submission` appears, and those with `required_for_submission` are enforced.

**FR-24 (MUST):** Computed fields MUST NOT appear as editable inputs anywhere, for any role. `Length` is computed from the preferred term. `Version` and `History` are derived from release membership and audit history. `Version` and `History` are hand-typed today and have drifted accordingly (Section 2.4); `Length` is a spreadsheet formula and has not drifted, but it is still a derived value that belongs in the export layer rather than the data model.

**FR-25 (MUST):** Before submission is accepted, the system MUST run duplicate detection against existing catalogue entries and open submissions, using trigram similarity over preferred terms and synonyms plus exact match on any supplied code. Near-matches are shown to the submitter with a prompt to confirm the submission is genuinely distinct, and the confirmation is recorded on the submission.

**Rationale:** duplicate removal appears in three separate releases of the workbook's revision history, worded differently each time: "Removal of duplicates" (December 2024), "Removed duplicates" (June 2025) and "Removed SNOMED-CT duplicate listing" (October 2025). Detecting duplicates at the point of entry is dramatically cheaper than finding them at publication.

**FR-26 (SHOULD):** Where the submitter supplies a SNOMED code, the platform validates it live against Ontoserver during form completion and displays the resolved FSN and active status. The submitter cannot save a code that does not resolve, though they may submit with no code at all (see FR-30).

**FR-27 (SHOULD):** Submitters can attach free-text clinical justification and supporting references. In a moderated pipeline, "why does this test need a national standard name" is the question reviewers actually need answered, and it is not captured today.

### 8.2 Workflow states

**FR-28 (MUST):** Submissions progress through the following states.

```
                          ┌──────────────────────────────────┐
                          │                                  │
   [Submitted] ──────▶ [Triage] ──────▶ [Rejected] ──(appeal)─┘
                          │                 │
                          │                 └──▶ (terminal after appeal window)
                          ▼
                    [Under review]
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
   [Awaiting terminology]        [Ready for approval]
              │                        │
              └────(code issued)──────▶│
                                       ▼
                                  [Approved]
                                       │
                              (next release cut)
                                       ▼
                            [Published in release]
                                       │
                                       ▼
                                 [Withdrawn]
```

State rules:

- `Withdrawn` MUST be reachable from `Approved` and `Published`, and also by the original submitter from `Submitted` or `Triage`. The brief presents it as a terminal state at the end of a linear chain; in practice withdrawal is something that happens to content already in circulation, and that is the case that matters.
- `Rejected` MUST be reopenable. Rejections are sometimes wrong, and circumstances change. A rejected submission that is reopened retains its original history rather than being resubmitted as a new record.
- Every transition MUST record actor, timestamp, and a mandatory reason note. No silent transitions.
- Transitions MUST be validated server-side against the permitted state graph. A client that requests an illegal transition receives an error, not a best-effort interpretation.

**FR-29 (MUST):** `Approved` and `Published` are distinct states. Approval is an editorial decision; publication happens when a release is cut. Conflating them makes it impossible to answer "what was approved but not yet published", which is exactly the question an implementer planning their next LIS update needs answered.

**FR-30 (MUST):** The `Awaiting terminology` state exists for submissions with no suitable existing SNOMED concept. In this state the record holds:

| Field | Purpose |
|---|---|
| `proposed_fsn` | The FSN being requested |
| `target_edition` | `au` or `int` |
| `justification` | Why an existing concept is not adequate |
| `external_reference` | Free text or URL identifying the request in whatever system handles it |
| `raised_date`, `expected_release` | Tracking |

When a code is subsequently issued, an administrator attaches it and the submission returns to `Ready for approval`.

**Rationale:** the workbook revision history records repeated batches of "Addition of new SNOMED-CT terms developed by CSIRO" (six consecutive releases from April 2025 to February 2026), meaning a concept had to be authored before the term could be catalogued. That step is currently invisible outside the working group and is the single largest source of unexplained delay for submitters. Making it a visible state converts an opaque wait into a tracked one.

**FR-31 (MAY):** Direct integration with a concept request or authoring system. Deferred, but the `external_reference` field MUST be structured enough (system identifier plus reference) that integration later does not require a data migration.

### 8.3 Interest signals

**FR-32 (MUST):** Members can register interest in an open submission. One record per user per submission, revocable.

**A recommendation on framing.** The brief calls this upvoting. I would not use that word in the interface, for a reason that goes beyond terminology. Upvote implies the count determines the outcome, which would make a clinical and technical standards decision into a popularity contest, and would create an incentive to organise votes. The signal is genuinely useful, but as *demand evidence*: it tells reviewers how many laboratories want this test standardised, which legitimately informs prioritisation. Label it accordingly, for example "Register implementer interest", and state on the interface that it informs prioritisation rather than determining approval.

**FR-33 (SHOULD):** The Reviewer and Administrator view of an interest count MUST show the distinct **organisations** represented, not only the headcount. Fifteen registrations from fifteen laboratories and fifteen registrations from one laboratory are entirely different signals, and only the former justifies prioritisation. This is cheap to build because organisation is already captured at registration, and it substantially improves the quality of the signal.

**FR-34 (MUST):** Interest counts are visible to all authenticated users. The **identities** of those registering interest are visible only to Reviewers and Administrators, per the matrix at 4.7. Provisional users and Observers cannot register interest.

**A limitation to state honestly:** hiding voter identity from peers does not prevent coordinated gaming, it only prevents peers from detecting it. Reviewer and Administrator visibility plus the organisational breakdown is the actual control. Do not present anonymity as an integrity mechanism.

### 8.4 Amendment submissions

**FR-35 (MUST):** Any role that can submit (FR-23) can propose an amendment to an existing published entry, most commonly an additional synonym. Observers cannot. Amendment submissions carry the same workflow, are linked to the target entry, and are shown on that entry's page to Reviewers and Administrators.

**Rationale:** synonym addition is by far the most frequent change type in the revision history. Making it a first-class flow rather than a general-purpose comment will handle the majority of community contributions.

### 8.5 Internal comments

**FR-95 (MUST):** Administrators and Reviewers can post free-text comments against a submission or a catalogue entry. Comments are **visible only to Administrators and Reviewers** (decision closing OI-14). They are never shown to Members, Provisional users, Observers, or the public, and never appear in any export or in the public API.

**Why internal rather than public.** The working group needs its deliberation recorded in the same place as the decision, which is a real gap today. But a public comment thread on a national standards site is a moderation surface requiring policy, abuse reporting, edit and delete rules, and a named person whose job is to watch it. Restricting comments to the two roles that hold editorial authority gets the deliberation record with none of that.

Requirements:

- Comments are **append-only**. They can be marked withdrawn, with the original text retained and visible to Administrators. Deliberation that can be silently deleted is not a record.
- Every comment generates an audit event.
- Comments are rendered as plain text, never as markup (NFR-23).
- The submitter is **not** notified of internal comments, and MUST NOT be able to infer their existence or count. Where the working group wants to communicate with a submitter, that belongs in the mandatory reason on a state transition (FR-28), which is visible.

**FR-96 (SHOULD):** Comments SHOULD support mentioning another Administrator or Reviewer, generating a notification. Small feature, and it is what makes the comment thread get used rather than abandoned in favour of email.

---

## 9. Functional requirements: administration

### 9.1 Catalogue editing

**FR-36 (MUST):** Administrators can create, amend and deprecate catalogue entries directly, including codings, designations, preferred term, and all registry properties.

**FR-37 (MUST):** Every save requires a changelog note. The note MUST be non-empty and MUST be validated as meaningful (minimum length, rejected if it matches a list of low-information strings such as "update", "fix", "."). This note becomes the published `History` text, so a lazy note today is a permanently unhelpful public record.

**FR-38 (MUST):** Concurrent editing MUST be handled with optimistic locking on `row_version`. When a second administrator saves an entry modified since they loaded it, the save is rejected and they are shown the conflicting changes. **Silent last-write-wins is unacceptable in a system whose audit log is intended to be authoritative**, because it produces an audit trail that records a change that was immediately and invisibly discarded.

**FR-39 (SHOULD):** Bulk operations for the common editorial patterns visible in the revision history, principally reclassifying discipline across a filtered set. The December 2024 discipline change touched every nucleic acid entry. Done one at a time through a web form, that is hours of clicking and a guaranteed source of inconsistency. Bulk operations MUST produce one audit event per affected entry, plus a single batch event linking them.

### 9.2 User administration

**FR-40 (MUST):** A user dashboard listing all users with registered name, organisation, username, email, role, status, submission counts, current rate limit, and registration date. Filterable and searchable.

**FR-41 (MUST):** Administrators can grant and revoke every role (Observer, Provisional, Member, Reviewer, Administrator), suspend accounts, and override the per-user submission rate limit. Reviewers can promote Provisional users to Member and no more. See the matrix at 4.7.

**FR-42 (MUST):** Registration captures real name, organisation and chosen username. Username is the only field displayed to other non-administrator users. See Section 13.3 on the privacy obligations this creates.

**FR-43 (MUST):** Rate limits MUST be enforced server-side. Rejections MUST be recorded in the audit log, because a user repeatedly hitting the limit is exactly the signal an administrator needs.

### 9.3 Permission model

**FR-44 (SHOULD):** Roles MUST be implemented as named sets of discrete permissions rather than as hard-coded checks against a role enum. Authorisation checks test for a permission, never for a role name.

**Rationale, now demonstrated rather than hypothetical.** The Reviewer and Observer roles (Sections 4.2 and 4.5) were added after v0.1 of this document, in response to a governance decision. Under a permission-based model that is a new permission set plus tests. Under a role-based model it would have been a search-and-replace through every controller, which is where authorisation bugs are born. Any further role the TSWG defines should cost the same as these two did.

---

## 10. Terminology validation

This is the capability that most clearly justifies replacing the spreadsheet, and it is under-specified in the source notes. What follows is a concrete design.

### 10.1 What validation checks

**FR-45 (MUST):** For every code binding in the catalogue, validation determines:

| Check | Finding type | Severity |
|---|---|---|
| Does the code exist in the target edition? | `code_not_found` | Error |
| Is the concept active? | `code_inactive` | Error |
| Does the stored `fsn` match the edition's current FSN, tag stripped? | `fsn_drift` | Error |
| Does the stored `au_preferred_term` match the current AU preferred term? | `preferred_term_drift` | Warning |
| If inactive, what is the historical association? | `replacement_available` | Info, attached to the inactivation finding |
| Is the concept subsumed by `71388002` \|Procedure\|? | `out_of_scope_hierarchy` | **Error.** See FR-84. |
| Is the concept a procedure whose semantic tag is not `(procedure)`? | `unexpected_semantic_tag` | Warning. See FR-99. |
| Do coded property values still resolve within their value set binding? | `binding_violation` | Error |
| Do local code system values still resolve to an active local code? | `local_code_retired` | Warning |

**Severity rationale for the two designation checks (see FR-83).** `fsn_drift` is an error because a changed FSN can mean the concept's meaning was revised. `preferred_term_drift` is a warning because it is usually AU editorial improving a label, and treating it as an error would produce a large standing alert backlog: 9 of 50 sample rows are already in that state.

**FR-46 (MUST):** Where a concept is inactive, the finding MUST include the inactivation reason **and** the associated historical association, and MUST treat the two as a pair rather than as independent facts. SNOMED CT pairs them deterministically, and the pairing is what determines whether a suggested replacement is safe:

| Inactivation reason | Historical association | Is the target a usable replacement? |
|---|---|---|
| Duplicate | `SAME AS` | Yes. Exactly one target, semantically identical. |
| Moved elsewhere | `MOVED TO` | **No.** The target is a module or namespace concept, not a replacement code. |
| Ambiguous | `POSSIBLY EQUIVALENT TO` (`MAY BE A`) | No. Zero or many candidates, none authoritative. |
| Outdated | `WAS A` or `REPLACED BY` | Only with review. `WAS A` identifies a former supertype, not an equivalent. |
| Erroneous, Limited, Not semantically equivalent | `REPLACED BY` where present | Only with review. |

An alert reading only "this code is inactive" leaves the administrator with all of the work still to do. An alert reading "inactive, reason: Duplicate, `SAME AS` 373873005 |Pharmaceutical / biologic product|" is actionable.

**One-click remediation MAY be offered only for `Duplicate` / `SAME AS`.** Every other combination requires the administrator to choose and to state a reason. `MOVED TO` in particular MUST never be offered as a replacement, because its target is a module identifier and substituting it would produce a nonsensical binding.

**Never auto-apply any replacement without confirmation.** In a catalogue driving pathology ordering, an incorrect automatic substitution is a patient safety issue.

### 10.2 Dual-edition strategy

**FR-47 (MUST):** Every validation run executes against **both** the latest SNOMED CT-AU edition and the latest International edition, and the results are diffed.

The three findings this produces, all of which the brief correctly identifies as necessary:

1. **AU-only code.** Resolves in AU, absent from International. Expected and correct for Australian extension content. No finding raised, but the edition is recorded.
2. **Inactive in International, still active in AU.** This is a *forecast*, not a current error. Because the AU edition incorporates International content on a lag, a concept inactivated in the 1 May International release will typically remain active in AU until the 31 May AU release. This finding type MUST be distinct and MUST be labelled as forecast, with the expected AU release date where derivable. It is the finding that lets RCPA-QAP make pre-emptive corrections instead of reactive ones.
3. **Absent from both.** A genuine error, most likely a transcription defect. Highest severity.

**FR-48 (MUST):** Each validation run MUST record the fully qualified version URI of each edition it resolved against, for example `http://snomed.info/sct/32506021000036107/version/20260531`. Without this a validation result is not reproducible, and a validation you cannot reproduce is not evidence.

**FR-49 (SHOULD):** Editions are referenced without a version parameter in normal operation, so runs target the latest available release. Version pinning is available for reproducing a historical run.

### 10.3 Execution and performance

**FR-50 (MUST):** Validation runs on demand (triggered by a Reviewer or Administrator, whole catalogue or filtered subset) and on a schedule.

**FR-51 (SHOULD):** The scheduled run SHOULD execute at least weekly, and the schedule SHOULD be configurable. Weekly rather than monthly because release dates shift and a weekly cadence removes the need to track them. On-demand alone, as the brief proposes, means findings surface only when someone thinks to look, which is the current failure mode.

**FR-52 (MUST):** Validation MUST NOT be implemented as one `$validate-code` call per code per edition. At the 20,000-entry planning ceiling (Section 5.1) that is 40,000 sequential HTTP requests, which is both slow and inconsiderate to a shared terminology server. The required approach:

1. **Bulk status resolution first.** Chunk the catalogue's codes and resolve active status with `ValueSet/$expand` against an ECL value set enumerating the chunk. One request per chunk, not per code. Determine chunk size empirically against the target Ontoserver instance; start around 200 to 500 codes and tune.
2. **Targeted `$lookup` only for the delta.** Codes that failed to appear in the expansion, or whose FSN differs, get an individual `$lookup` to retrieve inactivation reason, historical associations and module. This is a small fraction of the total.
3. **Bounded concurrency** on the second pass, with a configurable ceiling. Default conservatively.
4. **Respect HTTP caching** and honour `Retry-After` on 429 responses with exponential backoff.

**FR-53 (MUST):** Terminology server access MUST be behind an interface with a stub implementation for testing. The build MUST NOT require network access to a live Ontoserver to run its test suite, and MUST NOT depend on any Ontoserver behaviour outside the FHIR terminology specification, so the endpoint can be repointed at NCTS production or a locally hosted instance.

**FR-54 (SHOULD):** Terminology server failure MUST degrade gracefully. Validation is a background concern; an Ontoserver outage MUST NOT prevent browsing, searching or editing. Findings from a failed run are marked incomplete rather than being interpreted as clean.

**FR-55 (MUST):** Findings persist with a lifecycle: `open`, `acknowledged` (a Reviewer or Administrator has seen it and accepted the current state, with a reason), `resolved`, `superseded`. A finding acknowledged with a reason MUST NOT resurface as new on the next run. Without this, a persistent known-acceptable finding produces alert fatigue and the whole feature gets ignored.

**FR-56 (MUST):** A release MUST NOT be publishable while any error-severity finding is open and unacknowledged against an entry included in that release. This is the enforcement point for goal G3.

---

## 11. Releases, publication and export

### 11.1 Release model

**Cutover model (decision closing OI-10).** A **clean cutover at a nominated release**. The platform generates release N; from that point the spreadsheet is never hand-edited again. There is no dual-running period and no reconciliation process to build.

Two consequences the plan must carry:

1. **The P0 defect backlog must be cleared before the cutover release**, including the designation adjudication (FR-97) and the collision resolution (FR-05). There is no fallback of continuing by hand while the platform catches up.
2. **A consumer communication plan is required**, because the cutover release will differ from its predecessor in ways that have nothing to do with content: whitespace removed, `Length` values reduced for roughly one entry in five, and delimiters normalised. Those changes must be announced as a formatting migration so that consumers do not read them as content changes.

The alternative, dual running, was considered and rejected: maintaining two masters requires building and staffing a reconciliation process every cycle, and reconciling two masters is where this class of migration usually fails.

**FR-57 (MUST):** The catalogue has a mutable working state and a sequence of immutable published releases. Administrators edit the working state continuously. A release is a point-in-time snapshot, named on the existing convention (`2026-06`), which once published can never be altered.

**FR-58 (MUST):** Cutting a release requires: all error findings resolved or acknowledged (FR-56), a release note, and explicit confirmation. Publication is recorded in the audit log with the identity of the publisher.

**FR-59 (MUST):** The per-entry `Version` and `History` values in the SPIA export MUST be **generated from release membership and the changelog notes recorded against each change**, not stored as editable fields. This is the fix for Section 2.4. It also incidentally resolves the three-datatype problem in the current `Version` column, because a generated value has exactly one format.

**FR-60 (SHOULD):** A diff view between any two releases: entries added, removed, and changed with field-level detail. This is the artefact that lets an implementer plan an upgrade, and it is currently a hand-written prose paragraph in the Rev History sheet.

**FR-61 (SHOULD):** Release artefacts are immutable and retained indefinitely at stable URLs. Regenerating an artefact for a past release MUST produce an identical file. This requires that the export configuration version used be recorded with the release (FR-68).

### 11.2 Export formats

Three formats at launch.

**FR-62 (MUST): Simple CSV.** Code and FSN per entry. UTF-8 with BOM (Excel misreads UTF-8 without it, and this file will be opened in Excel regardless of intent), RFC 4180 quoting, `CRLF` line endings, codes as bare digit strings.

The label column carries the stored `fsn`, tag-stripped, per FR-98. It MUST have been validated at release time against a recorded edition version rather than published from a stale stored string, and the edition version MUST be stated in the accompanying manifest.

**FR-63 (MUST): SPIA spreadsheet.** Reproduces the current published workbook closely enough that existing consumers are not broken. Requirements:

- Column set, order and header text default to the current published layout: `RCPA Preferred term`, `RCPA Synonyms`, `Usage guidance`, `Length`, `Discipline`, `Subgroup`, `Specimen`, `Terminology binding (SNOMED CT-AU)`, `SNOMED CT Fully Specified Name`, `Version`, `History`.
- The `Rev History` worksheet is generated from release metadata, preserving the existing structure and copyright and SNOMED licence notices.
- **All code cells written with explicit text formatting** (FR-07).
- **No leading or trailing whitespace in any cell.** In the sample, 46 of 50 FSN values carry leading and trailing spaces. Generated output has none.
- **No non-ASCII whitespace anywhere.** Non-breaking space (U+00A0) and narrow no-break space (U+202F) are normalised out during ingestion and can never be introduced through the UI.
- Multi-valued fields serialised with a single, consistently applied delimiter. `;` with no surrounding space, matching the dominant existing convention.
- Deterministic row ordering. Specify the collation explicitly rather than inheriting a locale default, or the file will differ between machines and defeat FR-61.

**FR-64 (MUST): FHIR CodeSystem supplement.** The correct FHIR artefact for what the brief describes is a **CodeSystem supplement**: `CodeSystem.content = 'supplement'` with `CodeSystem.supplements = 'http://snomed.info/sct'`. This carries the RCPA-specific designations (preferred term and synonyms) and properties (discipline, specimen, subgroup) as an overlay on SNOMED CT without asserting any of it into SNOMED itself.

This is worth stating precisely because "adding RCPA synonyms to SNOMED" and "publishing a supplement that carries RCPA designations for SNOMED concepts" are different things with different governance consequences. The supplement is the correct mechanism: it is additive, attributable to RCPA, and consumers opt in.

The supplement is distinct from, and published alongside, the reference set and ValueSet that NCTS derives. It does not replace them.

**FR-65 (SHOULD):** A manifest accompanies every release listing each artefact, its SHA-256 hash, the export configuration version used, and the SNOMED edition versions the content was validated against.

### 11.2.1 Ad hoc export of the working state

**FR-78 (MUST):** Administrators MUST be able to generate an **ad hoc export of the current working state**, outside the release cycle, in any configured format. The source notes ask for administrator export of the catalogue generally; every other export requirement in this section is release-scoped, and an editor preparing a release needs to see the output before committing to it.

Ad hoc exports:

- Are clearly watermarked as a working draft, in the filename, in a header row or sheet, and in the manifest. A working draft MUST NOT be mistakable for a published release by a downstream consumer who receives the file second-hand.
- Are **not** retained as release artefacts and are not served from the public release URLs.
- Are **not** gated on validation findings (FR-56), because inspecting the current state including its problems is the point.
- Are recorded in the audit log, including who generated one and with which configuration version.

### 11.2.2 Delivery to NCTS

**FR-93 (MUST):** Release artefacts MUST be downloadable as a **single self-contained archive** suitable for delivery to NCTS through the existing submission process, which is email (decision closing OI-5). The archive contains every export artefact plus the manifest.

There is **no system-to-system integration with NCTS**. NCTS receives the exports as it does today and publishes reference sets, ValueSets and release archives through its existing pipeline unchanged. The development team needs no NCTS endpoint, credential, interface specification or availability dependency.

Two practical constraints follow from delivery by email:

- The archive MUST stay within ordinary attachment size limits, or the platform MUST provide a stable download URL to send instead. At the planning scale the archive will be small, but this MUST be verified rather than assumed.
- The manifest MUST travel **inside** the archive, so that the recipient can verify integrity without reference to the platform.

**FR-94 (SHOULD):** The platform SHOULD record that a release was delivered to NCTS, by whom and when, as an audit event. This is a manual confirmation rather than an automated one, and it exists so that the question "was release N sent" has an answer in the system rather than in somebody's sent items.

### 11.3 Export configuration

**FR-66 (MUST):** Administrators can configure each export format: which properties are included, and for CSV and spreadsheet formats, the column order and header text.

**FR-67 (MUST):** Reordering or removing a column MUST display an explicit warning that downstream implementers may be affected, and MUST require acknowledgement. Removal of a column present in the previous release MUST additionally require a stated reason, which is published in the release notes.

**FR-68 (MUST):** Export configurations are versioned with linear, append-only history. Reverting to an earlier version creates a **new** version whose content matches the earlier one. If current is v3 and the administrator reverts to v2, the result is v4 containing v2's content. v3 is retained and remains inspectable. Every version records who created it, when, and why.

**FR-69 (MUST):** Each published release records which export configuration version produced each artefact, satisfying reproducibility (FR-61).

---

## 12. Seeding transform and data migration

Phase P0. A standalone, runnable tool that converts the current published spreadsheet into the platform's data model.

**FR-70 (MUST):** A command-line transform that reads the published SPIA workbook and produces either a validated import dataset or a detailed defect report. **It MUST NOT silently repair anything it cannot repair deterministically.**

**FR-71 (MUST):** The transform classifies every finding into one of three bands, and the band determines behaviour:

| Band | Behaviour | Examples |
|---|---|---|
| **Auto-correctable** | Fixed automatically, every correction itemised in the report | Non-breaking and narrow no-break space normalisation to ordinary space; leading and trailing whitespace stripping; empty synonym removal from doubled delimiters; type coercion of codes to string; the `'Any'` specimen value, now deterministic under FR-89; compound `'X or Y'` discipline values, now deterministic as multiplicity under FR-90 |
| **Requires human decision** | Import aborts. Reported with row, column, current value and the decision required | Comma-versus-semicolon delimiter ambiguity where a synonym may legitimately contain a comma, or is space-separated with no delimiter at all; the `Fluids` specimen value; `Subgroup` values whose classification axis is unclear |
| **Data defect** | Import aborts. Reported for RCPA-QAP to correct at source | Codes failing Verhoeff check-digit validation; codes not resolving in either edition; **stored text matching no designation on the concept, or matching the FSN of a different concept** (FR-97); **codes not subsumed by `<<71388002`** (FR-84); duplicate codes; synonym colliding with another entry's preferred term. Note that a published label which is merely a *synonym* or a superseded FSN is **not** in this band: it is reported as information and seeded with the served FSN, because the catalogue lagging the terminology is expected rather than defective. |

**FR-100 (MUST):** A row that carries a `RCPA Preferred term` value but resolves **no** code binding MUST be reported as a data defect (`MISSING_CODE_BINDING`) and MUST NOT be seeded into the import dataset.

This is the mirror of the data-defect band's missing-preferred-term case above, on the opposite column, and closes a retrospective finding (#132): a row with a code binding and no preferred term was already caught and blocked, but a row with a preferred term and no code binding passed the scan silently and was seeded as an entry with an empty `code_bindings` list — nothing downstream could then distinguish a genuine unbound entry from a section heading or continuation line typed into the preferred-term column. Unlike the missing-preferred-term case, such a row is **not** seeded once flagged: a code-less row is judged more likely to be layout than a genuine entry awaiting a code, so it is omitted from the dataset entirely rather than carried forward with an empty binding.

**FR-97 (MUST):** The transform MUST produce a **designation reconciliation report**.

This is a **seeding-only** concern and does not exist at steady state. Once the platform stores designations as served (FR-82), every stored value came from the server by construction, so "matches nothing" cannot arise. It arises exactly once: when importing labels a human typed into a spreadsheet over more than a decade.

For each row the transform resolves the bound concept and classifies the published label:

| Outcome | Meaning | Action |
|---|---|---|
| Matches the tag-stripped FSN | The published label was correct and still is | None. Seed silently. |
| Matches another active designation on the concept | A valid synonym, or the FSN before it changed | Report as informational. Seed with the served FSN. |
| Matches the FSN or a designation of a **different** concept | Likely a transcription error pairing the wrong code with the right label, or the reverse | Report as a defect. **Abort.** The most dangerous outcome and the one most worth detecting, because both halves look individually plausible. |
| Matches no designation on any concept | The label is wrong, or was never a SNOMED designation | Report as a defect. **Abort.** |

The sample contains one instance of the fourth outcome: row 22 holds `Acanthamoeba species culture` against `122192001`, whose FSN is `Acanthamoeba culture (procedure)` and whose designation set does not contain that string. `$validate-code` rejects it.

The report MUST separately list, as information rather than defects, every row where the current AU preferred term differs from the published label, so that RCPA-QAP can decide whether to change what is published. Eight of the fifty sample rows are in that state.

**FR-72 (MUST):** The defect report is machine-readable (JSON or CSV) **and** human-readable. The human-readable form is the artefact RCPA-QAP will actually work from, so it must be organised by defect class, cite exact cell references, and state the required action.

**FR-73 (MUST):** The transform is **idempotent and re-runnable**. It will be run many times against progressively cleaner input. It MUST NOT require a clean database to run, and running it twice on the same input MUST produce identical output.

**FR-74 (MUST):** Every code in the source is validated against both editions during transform, using the same validation engine as FR-45. Do not build a second, divergent implementation for the migration path.

**FR-75 (SHOULD):** The transform reports semantic mismatches between the RCPA preferred term and the bound SNOMED FSN as warnings for editorial review. A heuristic pass over the 50-row sample surfaced four candidates where the preferred term carries a specimen or timing qualifier absent from the FSN, for example preferred term `'4-Hydroxy-3-methoxymandelate urine 24h'` bound to a concept whose FSN is `'3-Methyl,4-hydroxymandelate measurement'`, which carries no specimen or timing constraint.

**These are candidates for review, not confirmed defects.** Some will be legitimate: the SPIA preferred term is intentionally a request-facing label and need not mirror the FSN. Others may indicate a binding that is less specific than the term implies, which is a clinical safety concern in an ordering catalogue. The transform's role is to surface them for a terminologist to adjudicate, not to judge them.

**All four were subsequently verified against SNOMED CT-AU. Two were confirmed and two were false positives.** Appendix A.9 has the adjudication and the two design improvements it implies: the check MUST inspect the concept's `Has specimen` attribute rather than only its term text, and it MUST use a specimen synonym table so that CSF and cerebrospinal fluid do not read as different. Both false positives would have been eliminated by those two changes.

**FR-79 (SHOULD):** The transform SHOULD detect probable **misspellings** in designations, which the source notes name twice as a defect class and which no other requirement in this document addresses.

Detection heuristic, in order of reliability:

1. **Intra-entry inconsistency.** A synonym that is a near-match (high trigram similarity, small edit distance) to another designation on the *same* entry, or to a word in the bound FSN, where the two differ by one or two characters. This is the strongest signal because the correct spelling is present alongside the incorrect one.
2. **Cross-entry inconsistency.** A token appearing many times across the catalogue in one spelling and once or twice in another.
3. **Dictionary check** against a domain word list assembled from the SNOMED FSNs of all bound concepts, so that legitimate technical vocabulary is not flagged. **A general English dictionary is useless here** and would generate overwhelming noise.

The 50-row sample contains at least two instances, both caught by heuristic 1: row 48's synonym `'Epinephine 24 hr urine'` against the correctly spelled `'Epinephrine'` on the closely related row 47, and row 51's `'Alphafetoprotein antental'`, where `'antental'` does not match the entry's own preferred term `'AFP antenatal'`.

These are search targets in an ordering catalogue, not cosmetic defects: a misspelled synonym is dead weight that never matches a query, and a plausibly-misspelled one can match the wrong query. Findings are **warnings for editorial review, never auto-corrections.** Automatically "fixing" a term in a clinical terminology on the basis of an edit-distance heuristic is not acceptable.

This check SHOULD also be available on save in the application (FR-36), not only in the transform.

**FR-76 (MUST):** The seeded data MUST be imported as a synthetic baseline release representing the state at seeding, so that the first genuinely new release produces a meaningful diff (FR-60).

---

## 13. Non-functional requirements

### 13.1 Identity and authentication

**NFR-01 (MUST):** Authentication is delegated to a Keycloak instance deployed as part of the stack. The application implements the OIDC authorisation code flow with PKCE and never handles credentials.

**NFR-02 (MUST):** Keycloak launches with its **local user database enabled and external federation switched off**. From the user's perspective this is ordinary username and password registration. Enabling Microsoft Entra or Google sign-in later is realm configuration, requiring no application code change and no data migration.

**NFR-03 (MUST):** Keycloak realm configuration MUST be managed as code (exported realm JSON held in the repository, applied on startup) and MUST NOT be configured by hand through the admin console. Hand-configured realms are undocumented, unreproducible, and the most common cause of a broken environment rebuild.

**NFR-04 (MUST):** Application data MUST NEVER be keyed on the identity provider's subject claim. The schema is:

```
user
  id                 UUID   PRIMARY KEY     -- internal, stable, referenced everywhere
  username           text   UNIQUE
  display_name       text
  organisation       text
  role               ...
  status             ...

user_identity
  user_id            UUID   FK -> user.id
  issuer             text                   -- OIDC iss
  subject            text                   -- OIDC sub
  email              text
  email_verified     boolean
  linked_at          timestamptz
  UNIQUE (issuer, subject)
```

Every submission, interest record and audit event references `user.id`. Adding a federated identity later is a row insert into `user_identity`.

**NFR-05 (MUST):** When federation is enabled, automatic linking of an external identity to an existing account is permitted **only** where the issuer is explicitly trusted and asserts `email_verified = true`. In every other case the user must complete a manual link flow proving control of the existing account.

**Rationale, stated bluntly because it is the failure mode that matters:** automatic linking on an unverified email claim means anyone able to obtain a token asserting an administrator's email address inherits that administrator's privileges. This is the single most common serious vulnerability in systems that migrate from local to federated identity.

**NFR-06 (SHOULD):** Multi-factor authentication (TOTP) MUST be available and SHOULD be mandatory for the administrator role. Keycloak provides this natively; the requirement is to enable and enforce it, not to build it.

**NFR-07 (MUST):** All tokens validated server-side on every request: signature against the realm's JWKS, issuer, audience, and expiry. Authorisation decisions are made server-side from the internal user record, never from claims in the token. A token proves who the user is; the database decides what they may do.

### 13.2 Audit log

**NFR-08 (MUST):** A single append-only audit log capturing every state-changing operation. Each entry records:

| Field | Notes |
|---|---|
| `id`, `sequence` | Monotonic |
| `occurred_at` | timestamptz, UTC, server-assigned. Never client-supplied. |
| `actor_user_id` | Internal UUID. Null only for system-initiated events, which carry an explicit actor of `system`. |
| `actor_ip`, `user_agent` | |
| `correlation_id` | Groups all events from one request or batch operation |
| `action` | Controlled vocabulary, for example `catalogue_entry.updated` |
| `entity_type`, `entity_id` | |
| `before`, `after` | JSONB. Field-level, not whole-record blobs. |
| `reason` | The changelog note (FR-37). Mandatory for content changes. |
| `prev_hash`, `entry_hash` | Tamper-evidence chain |

**NFR-09 (MUST):** Append-only MUST be enforced at the database privilege level. The application's database role holds `INSERT` and `SELECT` on the audit table and **no** `UPDATE` or `DELETE`. Enforcing immutability only in application code means any SQL injection, any ORM mistake, and any future developer with good intentions can rewrite history.

**NFR-10 (SHOULD):** Each entry includes a SHA-256 hash over its own content plus the previous entry's hash, forming a chain. A verification command recomputes the chain and reports any break. This detects tampering performed with direct database access, which the privilege model alone cannot prevent.

**NFR-11 (MUST):** Authentication events (login, failure, lockout, MFA enrolment) are logged by Keycloak in its own event store. Domain events are logged by the application. Both are retained; keeping them separate keeps a burst of failed logins from burying the editorial history.

**NFR-12 (MUST):** Administrators can search and filter the audit log by actor, entity, action, and date range, and export a filtered view. An audit log nobody can query is a compliance artefact rather than a working tool.

**NFR-13 (MUST):** Audit entries are retained indefinitely and are **not** deleted when a user account is removed. See NFR-17 for how this reconciles with privacy obligations.

### 13.3 Privacy

The platform collects real names, organisations and email addresses. That is personal information, and the obligation attaches regardless of the platform's proof-of-concept status. Neither the source notes nor the TSWG Terms of Reference address it.

**NFR-14 (MUST):** A privacy policy and a collection notice are presented at registration, stating what is collected, why, who can see it (specifically: that administrators can see identities and interest records), how long it is retained, and how to seek access or correction. This is APP 1 and APP 5 under the *Privacy Act 1988* (Cth).

**NFR-15 (MUST):** Collect only what is necessary. Name and organisation are justified because attribution of standards contributions is a legitimate governance requirement. Anything beyond name, organisation, username and email requires a stated justification before it is added.

**NFR-16 (MUST):** Users can view and correct their own personal information (APP 12, APP 13).

**NFR-45 (MUST):** Terms of use MUST be presented at registration and MUST require positive acceptance. The platform MUST record **which version** each user accepted and when. When the terms change, existing users MUST be required to accept the new version before their next contribution, and the prior acceptance record MUST be retained.

**NFR-46 (MUST):** The terms MUST include a contribution licence. Pending legal confirmation, the position is (decision closing OI-9):

> The contributor **retains ownership** of their contribution, and grants RCPA a **perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence** to use, reproduce, modify, adapt, publish and distribute the contribution as part of the catalogue and any derivative work, including SNOMED CT reference sets, FHIR artefacts and published spreadsheets.

**Rationale for licence rather than assignment.** Retention of ownership keeps friction low. Many laboratory employees cannot assign copyright without employer approval, and requiring assignment would suppress contribution from exactly the organisations whose participation matters most. A broad irrevocable licence gives RCPA everything it needs to publish, without asking contributors for something their employer may refuse.

**Two things this position does not settle**, and which should go to legal review rather than being inferred from the above: the licence under which the catalogue itself is published downstream, which interacts with existing RCPA copyright and with SNOMED International licence terms; and whether contributions attract moral rights obligations requiring attribution.

**NFR-47 (SHOULD):** The terms MUST be versioned in the repository and rendered from a single source, so that the text a user accepted can be reproduced exactly.

**NFR-17 (MUST):** Account closure MUST **pseudonymise** rather than delete. The `user` row's identifying fields are replaced with a tombstone, `user_identity` rows are removed, and the internal UUID is retained so that audit entries and submission attribution remain intact and the chain in NFR-10 remains verifiable.

**Rationale:** deleting the user record would either break referential integrity in the audit log or require rewriting audit entries, and rewriting the audit log to satisfy a privacy request destroys the property that makes it an audit log. Pseudonymisation preserves evidentiary integrity while removing the identifying attributes.

**Whether it fully discharges APP 11.2 is a question for a privacy review, not an engineering assumption.** APP 11.2 requires destruction *or* de-identification once information is no longer needed, and OAIC's test for de-identification turns on re-identification risk in context. A retained stable internal identifier linked to attributed editorial actions may be re-identifiable from those actions alone, particularly in a small community of practice where a person's contributions are distinctive. Recorded as OI-15. The retention position MUST be stated explicitly in the privacy policy so that it is disclosed rather than discovered.

**NFR-18 (SHOULD):** Data residency in Australia. A community-of-practice platform for Australian health services with an Australian government-adjacent governance structure should not store personal information offshore without a deliberate, documented decision.

**NFR-19 (SHOULD):** A documented data breach response procedure, referencing the Notifiable Data Breaches scheme.

### 13.4 Security

**NFR-20 (MUST):** Every request authorised server-side against the internal user record. No authorisation decision made in the browser. Hiding a UI control is presentation, not access control.

**NFR-21 (MUST):** All traffic over TLS 1.2 or higher. HSTS enabled. No mixed content.

**NFR-22 (MUST):** Parameterised queries throughout. Where dynamic SQL is unavoidable for JSONB property filtering (FR-13), property keys MUST be validated against the registry before use in any identifier position, never interpolated from user input.

**NFR-23 (MUST):** Output encoding and a restrictive Content-Security-Policy. User-supplied content, particularly free-text justifications and changelog notes, is rendered as text and never as markup.

**NFR-24 (MUST):** Rate limiting at three layers: per-IP for anonymous requests, per-user for authenticated actions, and the domain-level submission limits in FR-43.

**NFR-25 (MUST):** Dependency vulnerability scanning in CI, with the build failing on high or critical findings in production dependencies. Container images scanned before publication.

**NFR-26 (MUST):** No secrets in the repository or in image layers. Configuration through environment variables or mounted secrets, with a documented `.env.example` carrying no real values.

**NFR-27 (SHOULD):** An independent security review before any production deployment. Budget for it in P5. A platform of this profile, with a public submission surface and administrator accounts controlling national standards content, will be asked for one by any adopting organisation.

**NFR-28 (SHOULD):** Containers run as a non-root user with a read-only root filesystem where practical.

### 13.5 Clinical safety

**NFR-29 (SHOULD):** Maintain a hazard log identifying clinical safety risks arising from catalogue defects, and the mitigations. Candidate hazards already identifiable from the sample data:

| Hazard | Mechanism | Mitigation |
|---|---|---|
| Wrong test ordered | Synonym collides with another entry's preferred term, requester selects the wrong item | FR-05 error-severity collision detection |
| Wrong test ordered | Ambiguous synonym maps to multiple entries differing only by specimen | FR-05 warning plus mandatory specimen display in search results |
| Under-specified request | Preferred term implies a specimen or timing the bound concept does not constrain | FR-75 semantic drift review |
| Test not found when searched | Misspelled synonym never matches the query a requester types | FR-79 misspelling detection |
| Order rejected downstream | Catalogue references an inactivated code | FR-45 to FR-47 validation, FR-56 publication gate |
| Silent content change | Undetected or unattributable modification | NFR-08 to NFR-10 audit chain |

The UK NHS standards **DCB0129** and **DCB0160** are the mature reference for this discipline. They do not apply in Australia and I am not suggesting formal compliance. The hazard log practice they describe is transferable, cheap, and directly responsive to the TSWG Terms of Reference obligation to "ensure the NPTC design supports clinical safety". Recorded as open issue OI-6.

**NFR-30 (SHOULD):** Governance should formally confirm that a passive reference catalogue falls outside the TGA's software-as-a-medical-device regulatory scope. This is almost certainly the case for a catalogue that does not interpret patient data or recommend action, but it should be answered on the record rather than assumed, particularly given the Terms of Reference contemplate future clinical decision support functionality that would change the analysis.

### 13.6 Accessibility, performance and operations

**NFR-31 (MUST):** WCAG 2.2 Level AA. Australian Government digital services are held to WCAG at Level AA, and obligations under the *Disability Discrimination Act 1992* apply to a public-facing service regardless. Verified by automated testing in CI plus a manual keyboard and screen-reader pass in P5.

**NFR-32 (SHOULD):** Search returns in under 500 ms at the 95th percentile for a catalogue of 20,000 entries. Comfortably achievable in PostgreSQL at this scale with correct indexing.

**NFR-33 (SHOULD):** A full dual-edition validation sweep of 20,000 entries completes within 30 minutes, running as a background job that does not degrade interactive use.

**NFR-34 (SHOULD):** Nightly database backup plus continuous WAL archiving. **A documented and actually exercised restore procedure.** An untested backup is a belief, not a control.

**NFR-35 (MUST):** Structured JSON application logs including the correlation ID from NFR-08. Liveness and readiness endpoints for every service. Application logs MUST NOT contain personal information or tokens.

**NFR-36 (SHOULD):** Operational documentation sufficient for an organisation other than the builder to run the platform: deployment, upgrade, backup, restore, Keycloak realm changes, and repointing the terminology server. This is what makes handover from CSIRO to RCPA-QAP possible rather than theoretical.

---

## 14. Architecture, technology and deployment

### 14.1 Recommended stack

| Layer | Recommendation | Why |
|---|---|---|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2 | The seeding transform, the anomaly report and the Ontoserver batch validation are all naturally Python and would be written in Python regardless of backend choice. Choosing a Python backend therefore removes a second language from the repository rather than adding one. Pydantic gives runtime validation with real enforcement, which the property registry needs. FastAPI emits OpenAPI 3.1 natively, which is what makes FR-20 nearly free. |
| **Database** | PostgreSQL 16+, extensions `pg_trgm` and `unaccent` | JSONB with expression indexing carries the property registry. Trigram similarity carries duplicate detection and typo-tolerant search. Row-level privilege control enforces the append-only audit log. One data store, one backup, one consistency model. |
| **Front end** | React 18+, TypeScript, Vite, TanStack Query | Client generated from the backend's OpenAPI document via `openapi-typescript`, so the contract is typed end to end without committing the whole system to one language. |
| **Identity** | Keycloak | Section 13.1. |
| **Background jobs** | PostgreSQL-backed queue using `SELECT ... FOR UPDATE SKIP LOCKED` | Validation sweeps, export generation and scheduled runs need a worker. At this scale a Postgres-backed queue avoids introducing Redis, which would be a fourth stateful component for no benefit. Celery plus Redis is the conventional alternative and is acceptable if the team already operates it. |
| **Reverse proxy** | Caddy | Automatic TLS certificate provisioning and renewal removes an entire class of deployment problem. nginx is fine if the operator prefers it. |
| **Packaging** | Docker Compose | Section 14.3. |

**Alternative that I would also accept:** full TypeScript (NestJS with Prisma, or a Next.js application), with the seeding transform still written as a standalone Python script. Choose this if the team's TypeScript experience is materially stronger than its Python experience. A team building confidently in a familiar stack will produce a better outcome than a team building carefully in the theoretically optimal one.

**What I would argue against:**

- **Django.** The property registry works against the ORM's assumptions, and the resulting code fights the framework at exactly the point where the system needs to be most flexible.
- **Elasticsearch or any second search store.** Unjustified at this data volume, and it introduces a consistency gap between the search index and the database of record.
- **A NoSQL primary store.** The audit log requires transactional integrity and enforced privileges; the release model requires referential integrity across snapshots. Both are things a relational database does natively and a document store makes you build.
- **Business logic in database triggers or functions.** It is invisible to the test suite, invisible in code review, and it will be the thing nobody can find in two years.

### 14.2 Testing requirements

**NFR-37 (MUST):** The test suite MUST run with no network access. Ontoserver is stubbed via the FR-53 interface. A build that requires a live terminology server is a build that fails intermittently for reasons unrelated to the change.

**NFR-38 (MUST):** Specific tests that MUST exist, because each closes a defect class documented in Section 16:

1. SCTIDs of 16, 17 and 18 digits each survive a write-then-read round trip through the generated `.xlsx` unchanged (FR-07). Sixteen is the boundary case.
2. No generated export cell contains U+00A0, U+202F, or leading or trailing whitespace.
3. Regenerating a release artefact from a fixed release ID and configuration version produces a byte-identical file (FR-61).
4. A synonym matching another entry's preferred term is rejected at error severity (FR-05).
5. The audit hash chain verifies, and an out-of-band `UPDATE` to an audit row is detected (NFR-10).
6. A property definition with recorded values cannot be deleted (FR-11).
7. An illegal workflow state transition is rejected server-side (FR-28).
8. A concurrent edit with a stale `row_version` is rejected (FR-38).
9. An Observer is refused at every write endpoint (FR-80), asserted per endpoint rather than once.
10. A Reviewer is refused when approving a submission, publishing a release, editing a published entry, granting a role above Member, or changing the export configuration (FR-81).
11. Semantic tag stripping in the export renderer turns `Microscopy (acid fast bacilli) (procedure)` into `Microscopy (acid fast bacilli)`, and a second application does not shorten it further (FR-83).
12. A stored FSN with no trailing parenthesised group causes the export to fail loudly rather than publish (FR-83).
13. A code not subsumed by `<<71388002` raises an error and blocks publication, and the check is asserted to issue **one** batch request rather than one per code (FR-84).
14. The seeding transform aborts on a published label that matches no designation on the bound concept, using row 22 of the sample as the fixture, and does **not** abort on one that matches a valid synonym (FR-97).
15. A user who has not accepted the current terms of use cannot contribute (NFR-45).
16. An internal comment is absent from the public API response and from every export artefact (FR-95).

**NFR-39 (SHOULD):** Integration tests run against a real PostgreSQL instance (testcontainers or equivalent), not an in-memory substitute. The JSONB indexing and privilege behaviour the design depends on cannot be verified against a simulation.

**NFR-40 (SHOULD):** End-to-end tests covering registration, submission, the full workflow path, and release publication. Contract testing of the public API against its published OpenAPI document.

### 14.3 Deployment topology

```
                             ┌─────────────┐
   Internet ────── 443 ─────▶│    Caddy    │  TLS termination, static assets
                             └──────┬──────┘
                          ┌─────────┴─────────┐
                          ▼                   ▼
                  ┌───────────────┐   ┌───────────────┐
                  │  API (FastAPI)│   │   Keycloak    │
                  └───────┬───────┘   └───────┬───────┘
                          │                   │
                  ┌───────▼───────┐           │
                  │    Worker     │           │
                  │ (validation,  │           │
                  │  exports)     │           │
                  └───────┬───────┘           │
                          ▼                   ▼
                  ┌────────────────────────────────┐
                  │        PostgreSQL 16           │
                  │  app schema  │ keycloak schema │
                  └────────────────────────────────┘
                          │
                          ▼
                  ┌────────────────┐
                  │ Object storage │  release artefacts (or a mounted volume for the PoC)
                  └────────────────┘

                  ─── outbound ───▶ Ontoserver (tx.ontoserver.csiro.au for the PoC)
```

**NFR-41 (MUST):** The whole stack starts from a single `docker compose up` against a documented `.env`, with no manual post-installation steps. Database migrations and Keycloak realm import run automatically on startup. This is the requirement that makes the platform demonstrable to a working group, and it is also the requirement that makes handover feasible.

**NFR-42 (SHOULD):** Separate compose overlays for development (hot reload, seeded demonstration data, permissive CORS) and production (no debug, restrictive CORS, resource limits).

**NFR-43 (SHOULD):** Images built in CI, tagged by commit SHA and semantic version, published to a registry. Deployment is pulling a tagged image, never building on the target host.

**NFR-44 (SHOULD):** The architecture MUST NOT depend on any single cloud provider's proprietary services. The likely handover to RCPA-QAP or Public Pathology Australia means the deployment target is not knowable now. Everything in the topology above runs equally on a single VM, a managed container service, or on-premises.

### 14.4 Sizing and indicative cost

The dataset is small (Section 5.1). Compute cost is dominated by having services running at all rather than by load.

| Component | Memory | Notes |
|---|---|---|
| PostgreSQL | 1 to 2 GB | The entire catalogue fits in shared buffers |
| API | 512 MB | |
| Worker | 512 MB | Peaks during validation sweeps |
| Keycloak | 768 MB to 1 GB | The largest single consumer |
| Caddy | 64 MB | |
| **Total** | **4 to 5 GB** | An 8 GB host gives comfortable headroom |

**Answering the specific question on language and cost: Python versus TypeScript makes no meaningful difference to infrastructure cost at this scale.** Both fit the same host. The only component that materially affects sizing is Keycloak, which pushes the requirement from 4 GB to 8 GB, meaning one VM size step.

Indicative and to be re-verified at procurement time: a single 2 vCPU / 8 GB instance in an Australian region, with PostgreSQL self-hosted in the compose stack, sits in the low hundreds of Australian dollars per month. Substituting managed PostgreSQL roughly adds the same again but removes the backup and patching burden, which is likely worth it once the platform is more than a demonstration. Object storage for release artefacts is negligible.

**The dominant cost is not infrastructure.** It is the ongoing operational effort: someone patching, monitoring, restoring, and responding. That should be named in the sustainability advice the TSWG is asked to provide, because it is the cost that determines whether a successful proof of concept can actually be adopted.

---

## 15. Open issues and risks

### 15.1 Decisions still required

Eleven of the fifteen open issues raised in v0.1 are closed. Four remain, and all four are governance or organisational rather than technical. **None of them blocks the start of development.**

| ID | Issue | Impact if unresolved | Owner | Blocks |
|---|---|---|---|---|
| **OI-1** | RCPA-QAP to nominate a maximum preferred-term length. The mechanism is specified (FR-85 to FR-87) and the platform will report the length distribution to inform the choice. Nothing was ever enforced historically. | Without a value the field stays vestigial. No build impact; the threshold is configuration. | RCPA-QAP | Nothing |
| **OI-6** | Will a clinical safety hazard log be maintained, and by whom? NFR-29 specifies the mechanism and seeds it with five hazards identified from the sample data. | The TSWG Terms of Reference commit to supporting clinical safety. The mechanism without an owner is a document nobody updates. | TSWG | Nothing in build. Required before adoption. |
| **OI-7** | Who hosts and operates the platform if the proof of concept succeeds? | Section 14.4: operational effort, not infrastructure, is the dominant cost and the real barrier to adoption. This is the single most likely way a technically successful project fails. Should feed the sustainability advice the TSWG owes the Steering Committee. | NPTC Steering Committee | Nothing in build. **Raise early, not at the end of P5.** |
| **OI-15** | Does retaining a stable internal user UUID linked to attributed audit entries satisfy de-identification under APP 11.2, or only pseudonymisation? See NFR-17. | May require an additional control on account closure. A short privacy review answers it. | RCPA / privacy officer | Nothing in build. Required before production. |

### 15.1.1 Decisions closed in v0.2

Recorded so that the reasoning is not lost and the decisions are not silently relitigated.

| ID | Decision | Basis |
|---|---|---|
| **OI-2** | Specimen `'Any'` becomes zero specimen codes plus an explicit `specimen_unconstrained` flag. `<123038009` binding confirmed adequate; `24 hour urine specimen`, `Platelet poor plasma specimen` and `Swab` all resolve. | Verified against SNOMED CT-AU. FR-88, FR-89. |
| **OI-3** | `Discipline` becomes a governed RCPA local code system with an optional non-authoritative SNOMED map. | Verified: `<394595002` returns 17 concepts; three of six RCPA disciplines match exactly, Microbiology is ambiguous between two, and Molecular and Serology have no match in the specialty hierarchy at all. FR-90, FR-91. |
| **OI-4** | Reviewer and Observer roles added. | Section 4, with an authoritative permission matrix at 4.7. |
| **OI-5** | No NCTS integration. Exports delivered as an archive through the existing email submission process; NCTS publishes through its existing pipeline. | FR-93, FR-94. Removes an entire integration from P4. |
| **OI-8** | `tx.ontoserver.csiro.au` throughout, including production. Accepted risk, recorded in 15.2. | Endpoint stays configurable behind FR-53. |
| **OI-9** | Contributors retain ownership and grant RCPA a perpetual, irrevocable, royalty-free licence. Terms accepted at registration with the version recorded. | NFR-45 to NFR-47. Downstream publication licence still needs legal review. |
| **OI-10** | Clean cutover at a nominated release. No dual running, no reconciliation process. | Section 11.1. Puts the P0 defect backlog on the critical path. |
| **OI-11** | `Subgroup` becomes a governed local code system alongside `Discipline`, with its mixed axes reconciled by RCPA-QAP first. | FR-90, FR-92. |
| **OI-12** | `Usage guidance` retained as free text. | Section 6.6. |
| **OI-13** | Optimise purely for requesting. `test_kind` and `binding_role` removed as speculative. A future Reporting catalogue is a second deployment of the same codebase. | Section 3.2, Section 6.2. |
| **OI-14** | Comments restricted to Administrators and Reviewers, internal only, append-only. | FR-95, FR-96. |
| **New** | Store `fsn` and `au_preferred_term` **exactly as served**, tag intact. Strip the tag only in the spreadsheet renderer. Validation becomes plain equality. | Storing a transformed value makes its state ambiguous, which is the sole source of the double-strip hazard. FR-82, FR-83, FR-98. |
| **New** | Every code binding MUST be subsumed by `71388002` \|Procedure\|, checked as one batch ECL call. Unexpected semantic tag is a separate warning. | Verified: all 50 sample codes comply, zero violations. Also verified that `71388002` subsumes `243120004` \|Regime/therapy\|, so subsumption does not imply the tag is `(procedure)`. FR-84, FR-99. |
| **New** | Seeding reconciles each published label against the bound concept's designation set, aborting where it matches nothing or matches a different concept. | Verified: 8 of 50 sample rows are valid synonyms or superseded FSNs (informational); row 22 matches nothing and blocks the import. FR-97. |

### 15.2 Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The property registry becomes a performance and complexity sink | Medium | High | The constrained design in Section 6.5: registry plus JSONB plus JSON Schema. No runtime DDL, no unbounded EAV. Generated indexes only for properties marked filterable. Review this design specifically at the end of P1. |
| The source spreadsheet contains more defects than the sample suggests, and P0 stalls | **High** | Medium | This is why P0 is first and standalone. The transform's job is to produce a defect report, and finding a large number of defects is a successful outcome, not a failure. Budget RCPA-QAP editorial time for remediation, not just developer time. |
| Community adoption does not materialise and submissions never arrive | Medium | High | Not a technical risk and not solvable by building more features. The public read requirement and the citable per-entry URLs (FR-17) are the lever: value to passive consumers is what creates the audience from which contributors emerge. |
| Interest registration is gamed to influence prioritisation | Low | Medium | FR-33 organisational breakdown, plus explicit framing that the signal is advisory (FR-32). Do not present anonymity as an integrity control. |
| Keycloak upgrade disruption | Medium | Low | Realm as code (NFR-03) makes rebuilds reproducible. Pin the major version and treat upgrades as planned work. |
| Ontoserver unavailability blocks validation before a release deadline | Medium | Medium | FR-54 graceful degradation, FR-55 acknowledgement lifecycle so a release is not hard-blocked by an infrastructure outage, and cached prior results remain visible and clearly dated. |
| **Accepted risk:** production depends on `tx.ontoserver.csiro.au`, a reference instance carrying no availability commitment | Medium | Medium | Decided deliberately (closing OI-8). Mitigations already in the design: FR-53 keeps the endpoint configurable behind an interface, so repointing at an NCTS or self-hosted instance is configuration rather than code; FR-54 ensures an outage degrades rather than blocks. **This will be queried in any adoption or security review**, and the answer should be that it is a known, configurable, single-line change rather than an oversight. |
| Terminology drift between build and adoption invalidates seeded content | Medium | Low | FR-51 scheduled validation means drift surfaces continuously rather than at publication. |
| The platform is built, demonstrated, and then has no operational home | **High** | High | OI-7. Raise it with the Steering Committee early, not at the end of P5. This is the most likely way a technically successful project fails. |

---

## 16. Appendix A: Sample data anomaly catalogue

Findings from the supplied 50-row sample of `RCPA SPIA Requesting Pathology RS_Jun 2026`. Every figure is reproducible by profiling the file. These are the concrete defects the platform's validation requirements are designed to prevent, and they are the specification for the P0 transform's checks.

**A 50-row sample is not a statistical basis for extrapolating to the full set.** It is a demonstration that each defect class is present in real data. The P0 transform's first run against the complete workbook will give the real numbers.

### A.1 Invisible character defects: 9 of 50 preferred terms (18%)

Non-breaking space (U+00A0) trailing the preferred term in rows 2, 4, 16, 21, 27, 34, 35 and others. Row 38 contains **both** a narrow no-break space (U+202F) and a non-breaking space (U+00A0), in that order, following the term. Row 16 carries two consecutive U+00A0 characters. Non-breaking spaces also appear in synonym cells and in **three code cells**: H16 (`121309009`), H17 (`82552000`) and H21 (`413359005`), each with a trailing U+00A0. **Fourteen cells across the worksheet are affected in total.**

These are described rather than quoted verbatim: reproducing the characters here would place them in this document, which is exactly what the platform's own output rules prohibit.

**Consequence:** exact string matching fails silently. A downstream consumer looking up `'Aciclovir level'` does not match `'Aciclovir level\xa0'`. Duplicate detection fails. Sort order is affected. None of it is visible on screen.

**Requirement:** FR-63 (normalisation on ingestion and prohibition at entry), NFR-38 test 2.

### A.2 SNOMED identifier storage: structural risk

Codes are stored as an integer type in 39 of 50 rows and as text in 11.

Excel's limit is 15 significant decimal digits. Australian extension SCTIDs in the sample run to 15, 16 and 18 digits, so **any code of 16 digits or more is corrupted if it is ever held in a numeric cell**. Six sample codes are at that length, and all six are intact only because they happen to be stored as text:

| Row | Code | Digits |
|---|---|---|
| 34 | `1393151000168101` | 16 |
| 40 | `1036561000168107` | 16 |
| 41 | `1682711000168101` | 16 |
| 20 | `933434771000036107` | 18 |
| 24 | `933451161000036104` | 18 |
| 42 | `933398481000036107` | 18 |

The 16-digit codes are the reason FR-07's test is specified at the boundary. A check written against "18-digit codes" is a check that misses half the exposure in this sample.

The workbook records the mechanism explicitly in two separate releases: February 2025, `'Rounding errors for some SCT codes fixed'`, and March 2026, `'Fixed random SCT code errors (Excel truncation/extra digits)'`. Row 42's own history entry, `'Jun 2025 - Incorrect SCT Code fixed'`, is consistent with the same cause but does not state a mechanism, so it is corroborating rather than independent evidence.

All 50 sample codes pass Verhoeff check-digit validation, so FR-71's Verhoeff band will not by itself block the seed import.

**Requirement:** FR-06, FR-07, NFR-38 test 1.

### A.3 Whitespace in FSN values: 46 of 50 (92%)

Three distinct patterns, which is the point:

- **45 rows** carry both a leading and a trailing space: `' 1,1,1-Trichloroethane measurement '`
- **1 row** (row 15) carries a leading space only: `' 3-Methyl,4-hydroxymandelate measurement'`
- **4 rows** (20, 24, 25, 42) are clean

The defect is not consistent enough to be removed by a rule that assumes symmetry. Strip both ends unconditionally.

**Requirement:** FR-63.

### A.4 Delimiter inconsistency in synonyms

The convention is `;`. Exceptions found:

- Row 42 uses a comma: `'ADA RBC, ADA red cells'`
- Row 20 uses no delimiter at all, only a space, plus a trailing space: `'7DHC 8DHC '`
- Row 27 contains a doubled delimiter producing an empty synonym: `'Acyclovir;ACV;Zovirax;;Cyclir;Lovir; Acihexal; ...'`
- Inconsistent spacing after the delimiter across many rows

Rows 42 and 20 are the hard cases, and they are why FR-71 classes delimiter ambiguity as *requires human decision* rather than auto-correctable. A transform cannot safely determine whether `'ADA RBC, ADA red cells'` is two synonyms or one synonym containing a comma, nor whether `'7DHC 8DHC '` is two abbreviations or one compound abbreviation. Both readings are plausible and only a terminologist can settle them.

**Requirement:** FR-04 (structural elimination), FR-71.

### A.5 Designation collisions

**Error severity, 1 instance.** `'Adrenal Ab'` is the preferred term of one entry (row 46) and simultaneously a synonym of `'21-Hydroxylase Ab'` (row 10). A requester searching that string is shown two results with no basis to choose.

**Warning severity, 9 instances.** The same synonym attached to multiple distinct entries:

| Synonym | Attached to |
|---|---|
| `ADA2` | `Adenosine deaminase`, `Adenosine deaminase CSF`, `Adenosine deaminase pleural fluid` |
| `ADA level` | `Adenosine deaminase`, `Adenosine deaminase CSF` |
| `17 OH Progesterone`, `17-OHP`, `Hydroxy Progesterone` | `17-Hydroxyprogesterone`, `17-Hydroxyprogesterone saliva` |
| `HIAA`, `5HIAA`, `5 Hydroxyindole acetic acid` | `5-HIAA urine`, `5-HIAA urine 24h` |
| `Acanthamoeba detection` | `Acanthamoeba sp culture`, `Acanthamoeba sp identification` |

Several of these are arguably legitimate: `ADA2` genuinely is the abbreviation for all three adenosine deaminase entries, disambiguated by specimen. That is precisely why they warn rather than block. But they are invisible today, and in a requesting catalogue an ambiguous term is a safety consideration, not merely an untidiness.

**Requirement:** FR-05.

### A.6 Compound values in a field intended to be coded: 5 of 50

`'Chemical pathology or Haematology'` (4 rows), `'Immunopathology or Chemical pathology'` (1 row). Eight distinct `Discipline` strings appear across 50 rows, of which two are compounds. No SNOMED concept corresponds to a disjunction of specialties.

**Requirement:** Section 6.6, FR-71, OI-3.

### A.7 Derived field drift

The `Version` column holds three Python types across 50 rows: 23 integers (`3`, `4`), two floats (`2.1`, `3.1`) and 25 datetimes. The distinct datetime values are `2024-07-01`, `2025-02-01`, `2025-04-01`, `2025-06-01`, `2025-10-01`, `2025-12-01` and `2026-02-06`. The column has evolved from a semantic version to a release date without a migration, so both conventions now coexist in the same column, and a consumer cannot parse it without handling three types.

The `Length` column agrees with the character count of the preferred term in all 50 rows, **including the non-breaking spaces**. Every cell is a `=LEN()` formula, so this is not editorial drift: the formula is correct and the input is dirty.

**Requirement:** FR-24, FR-59, OI-1.

### A.8 Classification instability

The December 2024 revision note reads `'RCPA Displine updated for all nucleic acid terms from Serology to Microbiology'` (typo in the original). In the June 2026 sample, those same nucleic acid entries carry `Discipline = Molecular`. The category has been recut twice in eighteen months.

Compare rows 23 and 24: `'Acanthamoeba sp identification'` is `Microbiology`, `'Acanthamoeba sp nucleic acid'` is `Molecular`. The axis of classification is testing method, not organism or clinical domain.

**This is an observation about the RCPA classification's stability, not a criticism of it.** Operational classifications legitimately evolve. It is direct evidence for OI-3: binding a live, actively-recut internal classification to an external terminology's release cycle constrains RCPA for no corresponding benefit.

### A.9 Terminology review candidates, adjudicated against SNOMED CT-AU

v0.1 flagged four entries by heuristic and could not verify them. All four have now been checked against SNOMED CT-AU via Ontoserver. **Two were confirmed and two were false positives**, which is the most useful thing in this appendix, because it calibrates what FR-75 is actually worth.

| Row | Preferred term | Code | Verified normal form | Verdict |
|---|---|---|---|---|
| 25 | `Acetone urine` | `47615003` | `... : Component = Ketone, Method = Measurement - action, Has specimen = Urine specimen` | **False positive.** The specimen is modelled. The heuristic simply could not see it. |
| 7 | `14-3-3 protein CSF` | `430551003` | AU preferred term is `CSF 14-3-3 protein measurement` | **False positive** on specimen. CSF is present; the heuristic lacked a synonym table for CSF and cerebrospinal fluid. It is, separately, one of the nine designation-drift rows in A.10. |
| 15 | `4-Hydroxy-3-methoxymandelate urine 24h` | `121302000` | `Measurement of substance : Component = 3-methyl,4-hydroxymandelate, Method = Measurement - action` | **Confirmed.** No specimen, no timing. The RCPA term asserts a 24-hour urine collection that the bound concept does not constrain at all. |
| 44 | `Adenovirus Ag faeces` | `121960004` | `Viral antigen assay : Component = Antigen of Adenovirus, Method = Measurement - action` | **Confirmed.** No specimen. The RCPA term asserts faeces. |

**A 50% false-positive rate is acceptable for a review queue and the PRD should not pretend otherwise.** FR-75 produces candidates for a terminologist, not findings. Two improvements are worth making before it runs at full scale: give it a specimen synonym table so CSF and cerebrospinal fluid do not diverge, and have it inspect the concept's `Has specimen` attribute rather than only its term text, which would have eliminated both false positives here.

**A separate observation on row 15, flagged rather than asserted.** SNOMED's descriptor is `3-methyl,4-hydroxymandelate`; the RCPA preferred term is `4-Hydroxy-3-methoxymandelate`. Methyl and methoxy are different substituents, and vanillylmandelic acid is the methoxy compound. If the RCPA term is chemically correct, SNOMED's is not. **This has not been verified against a chemistry or clinical authority and is not claimed here.** It is recorded because it is exactly the kind of thing a systematic review of the full set should catch, and because SNOMED content is the subject of this work rather than an authority on it.

### A.10 Verification against SNOMED CT-AU

Performed for v0.2. All 50 sample codes were resolved in a **single ECL value set expansion**, which incidentally confirms the batch strategy specified in FR-52 works against a live server.

**Code status: clean. Designation status: one blocking defect.**

All 50 codes are active in SNOMED CT-AU. None is inactive, none is missing, and all 50 pass Verhoeff validation. **Code validity will not block the seed import.**

One *designation* defect will. Row 22 holds `Acanthamoeba species culture` against `122192001`, whose FSN is `Acanthamoeba culture (procedure)` and whose designation set does not contain the stored string in any form. `$validate-code` rejects it. This is the fourth outcome in FR-97's reconciliation table, it sits in FR-71's data-defect band, and it therefore aborts the import until RCPA-QAP resolves it.

The distinction matters and v0.1 of this document got it wrong by treating all nine drift rows as the same thing. Eight of the nine are **drift**: the stored string is still an active English synonym on the concept and merely no longer the preferred term. Row 22 is a **defect**: the string matches nothing. Only the latter blocks anything.

**Designation drift: 9 of 50 (18%), of which 8 are drift and 1 is a defect.** The stored value in the `SNOMED CT Fully Specified Name` column differs from the current AU preferred term. The pattern is one-directional: AU editorial has been shortening these terms.

| Row | Code | Spreadsheet | AU preferred term |
|---|---|---|---|
| 3 | `873871000168106` | `Measurement of 1,5-Anhydroglucitol in serum specimen` | `Serum 1,5-Anhydroglucitol measurement` |
| 7 | `430551003` | `Measurement of 14-3-3 protein concentration in cerebrospinal fluid` | `CSF 14-3-3 protein measurement` |
| 22 | `122192001` | `Acanthamoeba species culture` | `Acanthamoeba culture` |
| 35 | `873991000168100` | `Measurement of anti-a disintegrin and metalloproteinase with a thrombospondin type 1 motif, member 13 antibody` | `Anti-ADAMTS13 antibody measurement` |
| 36 | `873961000168107` | `A disintegrin and metalloproteinase with a thrombospondin type 1 motif, member 13 activity assay` | `ADAMTS13 activity assay` |
| 37 | `873981000168103` | `A disintegrin and metalloproteinase with a thrombospondin type 1 motif, member 13 inhibitor assay` | `ADAMTS13 inhibitor assay` |
| 41 | `1682711000168101` | `Measurement of adenosine deaminase in pleural fluid` | `Pleural fluid adenosine deaminase measurement` |
| 42 | `933398481000036107` | `Measurement of adenosine deaminase in erythrocyte specimen` | `Erythrocyte adenosine deaminase measurement` |
| 45 | `413450008` | `Adenovirus nucleic acid detection` | `Adenovirus nucleic acid assay` |

**The column contains neither FSNs nor preferred terms, but it is not arbitrary text either.** No value in the column carries a semantic tag, so strictly none is an FSN. `413450008` makes the divergence explicit: FSN `Adenovirus nucleic acid detection (procedure)`, AU preferred term `Adenovirus nucleic acid assay`, spreadsheet `Adenovirus nucleic acid detection`.

What the stored values actually are, in 49 of 50 cases, is **an active English synonym on the concept**, which usually also happens to be the tag-stripped FSN. `Measurement of adenosine deaminase in pleural fluid`, `Measurement of 14-3-3 protein concentration in cerebrospinal fluid` and `Adenovirus nucleic acid detection` are all active designations of their concepts. So the column is validatable by equality, against the concept's designation *set* rather than against any single designation. That realisation is what produced the three-check model in FR-82, and it is a better design than the two-check model v0.1 would have produced.

One caution for implementers, drawn from row 29: `391483001` has FSN `Microscopy (acid fast bacilli) (procedure)`, so the correctly rendered label legitimately ends in a parenthesised phrase.

This is precisely the case that motivated storing designations **as served** rather than pre-stripped (FR-82). A stored value that has already been stripped is indistinguishable from one that has not, so a second strip silently yields `Microscopy`. Storing the tagged FSN removes the ambiguity by construction: the strip happens once, in the export renderer, on a value that definitionally carries exactly one tag. No list of known tags is required, and the rule stays a single trailing-group removal (FR-83).

**Discipline binding: not viable.** `<394595002` expands to 17 concepts in SNOMED CT-AU. Three of the six RCPA disciplines match exactly (`394596001`, `394916005`, `394598000`), Microbiology is ambiguous between `408454008` and `394820005`, and Molecular and Serology have no match. Their nearest neighbours, `708179009` \|Molecular pathology service\| and `708188000` \|Serology service\|, are healthcare service concepts: `check_subsumption(394595002, 708179009)` returns **not-subsumed**. This finding closed OI-3.

**Specimen binding: viable.** `276833005` \|24 hour urine specimen\|, `119362004` \|Platelet poor plasma specimen\| and `257261003` \|Swab\| all resolve under `<123038009`. The v0.1 concern about timed collections was unfounded. This finding closed OI-2.

**Hierarchy constraint: clean.** Expanding `(all 50 codes) MINUS <<71388002` returns **zero results**. Every sample code is subsumed by `71388002` \|Procedure (procedure)\|. The constraint in FR-84 is therefore empirically supported and will not block seeding, and the single-call ECL idiom is demonstrated to work.

**But subsumption does not imply the semantic tag.** `check_subsumption(71388002, 243120004)` returns **subsumes**, so `243120004` \|Regime/therapy (regime/therapy)\| is a descendant of Procedure carrying a different tag. A concept can be a structurally valid procedure and not be tagged `(procedure)`. This is why FR-84 (subsumption, error) and FR-99 (unexpected tag, warning) are separate checks rather than one, and why FR-83 does not rely on the tag being a known literal.

**Method.** SNOMED CT-AU, latest release, no version pin, via `tx.ontoserver.csiro.au`. Operations used: `ValueSet/$expand` with ECL including the `MINUS` operator, `CodeSystem/$lookup`, `$validate-code`, `$subsumes`.

### A.11 Summary

| Defect class | Count in 50 rows | Auto-correctable? |
|---|---|---|
| Invisible characters in preferred terms | 9 | Yes |
| FSN leading/trailing whitespace | 46 | Yes |
| Codes stored as numeric type | 39 | Yes (coerce to string) |
| Codes of 16+ digits at Excel corruption risk | 6 | Structural, prevented not corrected |
| Synonym delimiter inconsistency | 3 | No, human decision |
| Preferred-term/synonym collision (error) | 1 | No, editorial decision |
| Ambiguous synonym across entries (warning) | 9 | No, editorial decision |
| Compound discipline values | 5 | No, modelling decision |
| `Version` datatype inconsistency | 3 coexisting types across 50 rows | No, becomes derived |
| Terminology review candidates flagged | 4 (2 confirmed, 2 false positives) | No, terminologist decision |
| Probable misspellings in synonyms | 2 | No, editorial decision |
| Stored designation differs from current AU preferred term | 8 | No, editorial decision |
| Stored designation matches no designation on the concept (blocks import) | 1 (row 22) | No, editorial decision |
| Codes inactive or missing in SNOMED CT-AU | 0 | Verified clean |
| Codes not subsumed by `<<71388002` | 0 | Verified clean |

Every auto-correctable class is eliminated structurally by the platform: the defect becomes unrepresentable rather than merely corrected. Every non-auto-correctable class is surfaced for a human, with enough context to decide.

---

## 17. Appendix B: Delivery plan and acceptance criteria

### 17.1 Phase acceptance

Each phase is accepted against demonstrable criteria, not against a percentage.

**P0: Seeding transform**

- Runs against the complete published SPIA workbook and produces a defect report in both machine-readable and human-readable form.
- Every defect class in Appendix A is detected and correctly classified into its band (FR-71).
- Re-running against identical input produces identical output (FR-73).
- Runs offline against a stubbed terminology service, and against a live Ontoserver when one is configured.
- The designation reconciliation report (FR-97) classifies every published label against the bound concept's designation set, and aborts on the two defect outcomes while seeding silently through the two benign ones.
- The subsumption check (FR-84) runs as a single batch request and reports any code outside `<<71388002`.
- **Acceptance artefact:** the defect report and the designation change report from the real workbook, handed to RCPA-QAP. These have standalone value even if nothing further is built, and clearing them is a prerequisite for the cutover release (Section 11.1).

**P1: Core catalogue**

- A user can search and filter the seeded catalogue and reach any entry by stable URL, without logging in.
- A user can register, log in through Keycloak, and log out.
- An administrator can edit an entry, is required to supply a changelog note, and the change appears correctly in the audit log with before and after values.
- The audit hash chain verifies, and a direct out-of-band `UPDATE` to an audit row is detected.
- An administrator can add a new property through the UI, populate it on an entry, and filter by it, with no deployment and no restart.
- A property with recorded values cannot be deleted.
- Public API serves the catalogue and its OpenAPI 3.1 document validates.

**P2: Contribution**

- A provisional user can submit, is blocked at the sixth submission, and cannot register interest.
- A reviewer promotes them to member; they can then register interest and submit beyond five, and are rate-limited at 20 per hour.
- Duplicate detection surfaces a near-match at submission time.
- A submission traverses the full state graph including `Awaiting terminology` and back, with a mandatory reason at every transition. An illegal transition is rejected server-side.
- Interest counts are visible to authenticated users; identities and the organisational breakdown only to Reviewers and Administrators.
- **Every cell of the permission matrix at 4.7 has a passing test, including the negative cases.** Specifically: an Observer is refused at every write endpoint (FR-80); a Reviewer is refused when attempting to approve a submission, publish a release, edit a published entry, grant a role above Member, or change the export configuration (FR-81).
- Internal comments are visible to Administrators and Reviewers, invisible to every other role and absent from the public API and all exports. A withdrawn comment retains its original text for Administrators.
- A user cannot contribute without having accepted the current terms of use, and the accepted version is recorded (NFR-45).

**P3: Validation**

- A full dual-edition sweep of the seeded catalogue completes within the NFR-33 budget and records both edition version URIs.
- A known-inactive code produces a finding carrying its inactivation reason and historical association.
- A code inactive in International but active in AU produces a distinct forecast-type finding.
- An acknowledged finding does not resurface as new on the next run.
- Ontoserver unavailability does not prevent browsing, searching or editing, and the incomplete run is marked as such.

**P4: Release and export**

- A release cannot be published while an unacknowledged error finding exists.
- All three export formats generate; the spreadsheet is visually and structurally comparable to the current published workbook.
- The SCTID round-trip test passes.
- Regenerating a past release's artefacts produces byte-identical files.
- Reordering an export column warns, requires acknowledgement, and creates a new configuration version. Reverting v3 to v2 creates v4.
- A diff between two releases is correct at field level.

**P5: Hardening**

- Automated accessibility testing passes; manual keyboard and screen-reader pass completed.
- Dependency and container scans clean of high and critical findings.
- Backup taken and **restore actually performed** into a clean environment.
- Operational documentation validated by someone other than the author performing a deployment from scratch.
- Independent security review completed, findings triaged.

### 17.2 Definition of done, applying to every requirement

1. Automated tests covering the stated behaviour and its principal failure mode.
2. Every state-changing operation emits an audit event.
3. Authorisation enforced server-side and tested for the negative case, not only the positive one.
4. Accessible: keyboard operable, correctly labelled, sensible focus order.
5. Errors surfaced to the user in language that says what to do next, not a stack trace or an HTTP status.
6. Documented in the operational or user documentation where behaviour is not self-evident.

### 17.3 Sequencing advice

P0 runs in parallel with P1 from day one, ideally with a different developer. It has no dependency on the application and its output shapes RCPA-QAP's editorial workload, which is likely to be the longest-lead item in the entire programme.

Do not defer the audit log. Retrofitting comprehensive auditing onto a system that was built without it means touching every write path, and the result is always incomplete. Build it in P1 as infrastructure that every write path uses.

Do not defer the property registry. If the built-in fields are hard-coded in P1 and the registry arrives in P5, the system ends up with two mechanisms for the same concept and every export, form and filter has to handle both.

---

## 18. Appendix C: Glossary

| Term | Meaning |
|---|---|
| **AU Edition** | SNOMED CT-AU, the Australian national edition. System URI `http://snomed.info/sct/32506021000036107`. |
| **CodeSystem supplement** | A FHIR resource carrying additional designations and properties for concepts in another code system, without asserting content into it. `CodeSystem.content = 'supplement'`. |
| **ECL** | Expression Constraint Language, SNOMED CT's query syntax for defining sets of concepts. |
| **EAV** | Entity-Attribute-Value, a schema pattern for arbitrary properties. Referenced in Section 6.5 as the approach being deliberately avoided. |
| **FSN** | Fully Specified Name. The unambiguous SNOMED CT description of a concept. |
| **International Edition** | The SNOMED CT International release. System URI `http://snomed.info/sct/900000000000207008`. |
| **Historical association** | A SNOMED CT reference set relating an inactivated concept to another concept. The relationship type determines whether the target is a usable replacement: `SAME AS` (identical, safe), `REPLACED BY` and `WAS A` (review required), `POSSIBLY EQUIVALENT TO` / `MAY BE A` (candidate only), `MOVED TO` (**target is a module or namespace, not a replacement code**). See FR-46. |
| **NCTS** | National Clinical Terminology Service. Publishes SNOMED CT-AU and derives reference sets and ValueSets from the SPIA workbook. |
| **NPTC** | National Pathology Test Catalogue. |
| **Ontoserver** | The FHIR terminology server used for all SNOMED CT operations. |
| **RCPA-QAP** | Royal College of Pathologists of Australasia Quality Assurance Programs. Maintains the SPIA terminology. |
| **Reference set** | A SNOMED CT mechanism for defining a subset of concepts, with optional additional data per member. |
| **SCTID** | SNOMED CT identifier. Up to 18 digits, with a Verhoeff check digit. |
| **SPIA** | Standardised Pathology Informatics in Australia. |
| **TSWG** | NPTC Technical and Standards Working Group. |
| **Semantic tag** | The parenthesised phrase ending every SNOMED CT FSN, for example `(procedure)`, indicating the concept's hierarchy. Its absence from every value is how this document established that the SPIA column headed "Fully Specified Name" does not contain FSNs. Note that a tag is **not** implied by subsumption: `71388002` \|Procedure\| subsumes `243120004` \|Regime/therapy (regime/therapy)\|, so a valid procedure can carry a different tag (FR-99). |
| **Verhoeff** | The check-digit algorithm used in SNOMED CT identifiers, permitting offline detection of most transcription errors. |

---

## 19. Sources and provenance

| Input | Use in this document |
|---|---|
| `RCPA SPIA Requesting Pathology RS_Jun 2026 Snippet.xlsx` (50-row sample, supplied) | All quantitative findings in Appendix A, produced by direct profiling of the file. Release-level quotations come from the `Rev History` worksheet; per-entry quotations such as "Incorrect SCT Code fixed" come from the `History` column of the data worksheet. Both are cited as such where they appear. |
| `NPTC maintenance platform.txt` (requirements notes, supplied) | Source of the functional requirement set. Where this document departs from those notes, the departure is stated and argued rather than made silently. |
| `NPTC Technical and Standards Working Group ToR.pdf` v1.1, endorsed 23 February 2026 | Governance context, scope boundaries, and the clinical safety and sustainability obligations referenced in Sections 3, 13.5 and 15. |

**Verification status (updated for v0.2).** All SNOMED CT claims in this document have been verified against **SNOMED CT-AU, latest release, no version pin**, via `tx.ontoserver.csiro.au`. Appendix A.11 records the method and the results. All 50 sample codes are active and pass Verhoeff validation.

Two limitations remain, and both are stated rather than glossed:

1. **Verification was against the AU edition only.** The International edition was not separately queried, so the dual-edition forecast logic in FR-47 is specified but not demonstrated. It should be exercised during P3 against a code known to be inactive in International and active in AU.
2. **No claim about clinical or chemical correctness has been verified.** The observation about `3-methyl` versus `3-methoxy` in Appendix A.9 is flagged as a question, not asserted as a finding. SNOMED content is the subject of this work, not an authority for it.

**Where this document takes a position against the source notes**, for the development team's awareness:

| Section | Source notes said | This document recommends | Why |
|---|---|---|---|
| 6.5 | Administrators add properties with FHIR primitive datatypes | Same capability, but via a constrained registry with JSONB storage and per-property JSON Schema, explicitly not runtime DDL or open EAV. Code properties additionally require a value set binding, not just a datatype. | A code without a value set is an uncontrolled string. Runtime DDL and open EAV both make the system progressively harder to change. |
| 6.6 | Bind `Discipline` to `<394595002` | A governed RCPA local code system with an optional SNOMED map, leaving Molecular and Serology unmapped | **Verified against SNOMED CT-AU:** `<394595002` has 17 members; three of six disciplines match, one is ambiguous, and two have no match in the specialty hierarchy at all. Their nearest neighbours are healthcare service concepts, confirmed not-subsumed. Accepted by RCPA. |
| 8.2 | `Submitted → Rejected / To Be Reviewed → Ready for Approval → Approval → Withdrawn` | Adds `Awaiting terminology`; makes `Withdrawn` reachable from any post-approval state; makes `Rejected` reopenable; separates `Approved` from `Published`. | Withdrawal happens to published content, not at the end of a queue. The terminology wait is the largest source of invisible delay. |
| 8.3 | Upvoting | Interest registration, framed as advisory demand evidence, with an organisational breakdown for administrators | Vote counts should not decide a clinical and technical standards question, and fifteen registrations from one laboratory is a different signal from fifteen laboratories. |
| 10.3 | Validation on administrator command | On command **and** on a schedule | On-demand alone means findings surface when someone remembers to look, which is the current failure mode. |
| 11.1 | (not addressed) | `Version` and `History` generated from release membership, never editable | They are derived values, and hand-maintaining them is what let them drift into three datatypes. |
| 13.3 | (not addressed) | Privacy Act obligations, collection notice, pseudonymisation on account closure | Real names and organisations are being collected. The obligation attaches now, and pseudonymisation is the only way to satisfy it without corrupting the audit log. |
| 15.1 | (not addressed) | Contribution licensing: ownership retained, broad irrevocable licence to RCPA, terms version recorded per user | Accepting community contributions without a licence grant creates a position that is cheap to fix now and expensive later. Accepted by RCPA pending legal confirmation. |
| 6.4 | One column headed `SNOMED CT Fully Specified Name` | Store `fsn` and `au_preferred_term` separately and **exactly as served**; strip the tag only when rendering the spreadsheet | The column holds neither an FSN nor a preferred term. Verified: 9 of 50 sample rows disagree with the current AU preferred term; 8 remain active synonyms of their concept, and 1 matches no designation at all. |
| 6.4 | (not addressed) | Every code binding MUST be subsumed by `71388002` \|Procedure\| | A structural check catches an observable entity or clinical finding bound as a request code, which a textual check cannot. Verified: all 50 sample codes comply. |
| 4 | Users, provisional users, administrators | Adds Reviewer and Observer, with an authoritative permission matrix | Without Reviewer, giving a working group member triage access means granting release publication and user management. Without Observer, giving someone visibility means granting them the ability to contribute. |
| 8.5 | (not addressed) | Internal comments for Administrators and Reviewers only, append-only | The working group needs a deliberation record. A public comment thread needs a moderation owner, and there is not one. |
