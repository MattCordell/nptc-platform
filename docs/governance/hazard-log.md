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
| H-03 | Under-specified request | The preferred term implies a specimen or timing constraint the bound SNOMED concept does not carry | FR-75 semantic drift review (terminologist adjudication, not automated correction) | Mitigation designed, not yet implemented |
| H-04 | Test not found when searched | A misspelled synonym never matches the query a requester types | FR-79 misspelling detection (flags for review, never auto-corrects) | Mitigation designed, not yet implemented |
| H-05 | Order rejected downstream, or ordered against an inactivated concept | The catalogue references a SNOMED CT code that has since been inactivated | FR-45–FR-47 dual-edition validation, FR-56 publication gate | Mitigation designed, not yet implemented |
| H-06 | Silent content change | A modification to catalogue content is undetected or unattributable | NFR-08–NFR-10 append-only, hash-chained audit log | Mitigation designed, not yet implemented |

## Adding a hazard

Open an issue with Issue Type "Bug" and severity "Critical — clinical safety hazard" (see
the bug report template), and add a row here once its mitigation is agreed, in the same
PR that implements or designs the mitigation. See the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).

## Related

- OI-6 — hazard log ownership, open (PRD §15.1)
- OI-15 — privacy review of pseudonymisation, open (PRD §13.3, §15.1)
- PRD §13.5, §15.1
