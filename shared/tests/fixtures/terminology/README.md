# Terminology contract fixtures

FHIR R4 response bodies for `ValueSet/$expand`, `CodeSystem/$lookup`,
`CodeSystem/$subsumes` and `$validate-code`, used by
`test_terminology_contract.py` to seed both `OntoserverClient` (via
`httpx.MockTransport`) and `StubTerminologyClient` from the *same* bodies,
parsed by the *same* `nptc_shared.terminology.fhir` functions.

Every code, FSN and designation used here was confirmed against a live
Ontoserver instance (`tx.ontoserver.csiro.au`) while this fixture set was
authored. The surrounding FHIR `Parameters`/`ValueSet`/`OperationOutcome`
envelope is hand-constructed to the R4 spec shape rather than a literal HTTP
capture - the tools available in this environment return Ontoserver's data
through an already-parsed interface, not raw HTTP responses, so a byte-for-
byte capture was not possible. Where a fixture needed a concept this project
has no live-verified data for (the inactive/historical-association case),
`873871000168106` is used - a real, Verhoeff-valid SCTID already established
as a test fixture in `shared/tests/test_sctid.py` - with an invented
inactivation history, since no live-inactive AU concept was looked up during
authoring.

| File | Operation | Scenario |
|---|---|---|
| `expand-two-active-concepts.json` | `$expand` | Two active concepts, no designations |
| `expand-empty.json` | `$expand` | FR-84's `MINUS <<71388002` check, zero violations |
| `expand-with-designations.json` | `$expand` | `includeDesignations=true`, FSN + AU preferred term |
| `expand-filtered-by-text.json` | `$expand` | `filter=` narrows a two-concept expansion to the one matching display (issue #247) - hand-constructed to the R4 shape like the inactive-concept fixture above, not live-captured |
| `lookup-active-concept.json` | `$lookup` | Active concept, FSN + AU preferred term + `inactive=false` |
| `lookup-inactive-duplicate-same-as.json` | `$lookup` | Inactive, `inactivationReason=Duplicate`, `SAME_AS` target (FR-46) |
| `subsumes-equivalent.json` | `$subsumes` | `outcome: equivalent` |
| `subsumes-subsumes.json` | `$subsumes` | `outcome: subsumes` |
| `subsumes-subsumed-by.json` | `$subsumes` | `outcome: subsumed-by` |
| `subsumes-not-subsumed.json` | `$subsumes` | `outcome: not-subsumed` |
| `validate-code-true.json` | `$validate-code` | Matching display |
| `validate-code-false-display-mismatch.json` | `$validate-code` | PRD Appendix A.11 row 22 (FR-97) |
| `operation-outcome-invalid-ecl.json` | (error) | A 4xx `OperationOutcome`, used only by `test_terminology_ontoserver.py` |
