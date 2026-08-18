# Clinical safety hazard log

Required by NFR-29. Seeded from the candidate hazards the PRD identifies from the
50-row sample (§13.5); extended as new hazards are found during build or operation.

**Ownership is unresolved** — tracked as governance issue OI-6 (see the corresponding
GitHub issue, or PRD §15.1 in the meantime).
This log exists and is maintained by the development team in the interim, but a
document with no accountable owner does not stay current on its own. The TSWG Terms of
Reference commits to supporting clinical safety; naming an owner discharges that
commitment, this document alone does not.

This practice follows the spirit of the UK NHS standards **DCB0129** and **DCB0160**.
They do not apply in Australia and formal compliance is not claimed — the practice of
maintaining a hazard log is what is being borrowed, because it is cheap and directly
useful, not the regulatory framework around it.

## Log

| ID | Hazard | Mechanism | Mitigation | Status |
|---|---|---|---|---|
| H-01 | Wrong test ordered | A synonym collides with another entry's preferred term; a requester selects the wrong item | FR-05 error-severity collision detection blocks the save | Mitigation designed, not yet implemented |
| H-02 | Wrong test ordered | An ambiguous synonym maps to multiple entries differing only by specimen | FR-05 warning-severity detection plus mandatory specimen display in search results | Mitigation designed, not yet implemented |
| H-03 | Under-specified request | The preferred term implies a specimen or timing constraint the bound SNOMED concept does not carry | FR-75 semantic drift review (terminologist adjudication, not automated correction) | Mitigation implemented in the seeding transform (P0-7/#29) |
| H-04 | Test not found when searched | A misspelled synonym never matches the query a requester types | FR-79 misspelling detection (flags for review, never auto-corrects) | Mitigation implemented in the seeding transform (P0-7/#29); the same check on save in the application (FR-36) remains designed, not yet implemented |
| H-05 | Order rejected downstream, or ordered against an inactivated concept | The catalogue references a SNOMED CT code that has since been inactivated | FR-45–FR-47 dual-edition validation, FR-56 publication gate | Mitigation designed, not yet implemented |
| H-06 | Silent content change | A modification to catalogue content is undetected or unattributable | NFR-08–NFR-10 append-only, hash-chained audit log | Privilege (issue #33) and hash-chain (issue #36, ADR-0017) both implemented: `audit_event`'s INSERT/SELECT-only grant plus a SHA-256 `prev_hash`/`entry_hash` chain make an out-of-band `UPDATE`/`DELETE`/re-order detectable, proven by `backend/tests/test_audit_tamper_detection.py`. One real write path exists (`close_account`'s `user.closed` event); NFR-08 stays `in-progress` until every state-changing write emits. **Known gap, distinct from the above**: deleting the *tail* of the chain (the most recent N rows) leaves a table that still verifies cleanly - `verify_chain` walks forward from genesis and has nothing after the truncation point to detect a break against, so tail truncation is undetectable from the table alone. The operator-facing verification CLI (`scripts/verify_audit_chain.py`, issue #38) is implemented: it wraps `verify_chain()` with stable, documented exit codes (`docs/operations/runbooks/verify-audit-chain.md`) and, when an operator supplies `--expected-head-hash`/`--expected-record-count` (recorded from a previous run and stored off-box), detects a mismatch against that anchor as exit `4` - closing the tail-truncation gap only for a run given that expectation. There is still no automatically-maintained, off-box anchor store; a run given neither flag remains as blind to truncation as `verify_chain` alone |
| H-07 | Wrong test ordered | A catalogue entry pairs a plausible published label with the SNOMED code of a different concept — the label reads correctly to a requester while the transcribed pairing carries the wrong concept downstream | FR-97 designation reconciliation blocks the seed import where the published label matches a designation of a different bound concept, or of none at all | Implemented for seeding (P0-6, #28); structurally prevented at steady state once designations are stored as served (FR-82) |
| H-08 | Wrong or altered test seeded | A seeding auto-correction alters a published clinical term without review | FR-71 restricts auto-correction to three whitespace/character-normalisation classes plus three narrowly-scoped structural repairs (empty synonym removal, "Any" specimen resolution, compound-value splitting) — never a semantic edit — every correction is itemised in the report with its cell reference, and `--report-only` previews every one of them before `--emit-dataset` ever writes them | Mitigation implemented in the seeding transform (P0-9/#31) |

## Adding a hazard

Open an issue with Issue Type "Bug" and severity "Critical — clinical safety hazard" (see
the bug report template), and add a row here once its mitigation is agreed, in the same
PR that implements or designs the mitigation. See the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).

## Related

- OI-6 — hazard log ownership, open (PRD §15.1)
- OI-15 — privacy review of pseudonymisation, open (PRD §13.3, §15.1)
- PRD §13.5, §15.1
