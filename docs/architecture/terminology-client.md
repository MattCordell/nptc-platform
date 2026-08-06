# Terminology client (FR-53)

## What this is

`nptc_shared.terminology` is the one interface both the backend and the P0 seeding
transform use to talk to a FHIR R4 terminology server (FR-53, FR-74). It exists so the
test suite never needs a live server (NFR-37) and so the endpoint — `tx.ontoserver.csiro.au`
for the PoC, an accepted risk recorded in PRD Section 15.2 — can be repointed at NCTS
production or a self-hosted instance as configuration, not code.

Two implementations satisfy the `TerminologyClient` Protocol
(`nptc_shared/terminology/client.py`):

- `StubTerminologyClient` (`stub.py`) — in-memory, seeded by a test, never opens a socket.
- `OntoserverClient` (`ontoserver.py`) — synchronous, `httpx`-backed, against any
  spec-conformant FHIR terminology server (nothing in it is Ontoserver-specific).

Why both live in `shared/`, alongside the rejected alternatives, is ADR-0003.

## The four operations

| Operation | Method | Path | Purpose |
|---|---|---|---|
| `expand` | GET, or POST past a URL-length budget | `ValueSet/$expand` | Batch status resolution over an ECL expression — FR-52's bulk pass, and FR-84's hierarchy check |
| `lookup` | GET | `CodeSystem/$lookup` | FSN, preferred term, active status, inactivation reason and historical associations for one code — FR-52's targeted second pass |
| `subsumes` | GET | `CodeSystem/$subsumes` | A single ad-hoc subsumption check (FR-26) |
| `validate_code` | GET | `CodeSystem/$validate-code` or `ValueSet/$validate-code` | FR-97's designation reconciliation, or FR-10's value-set binding check |

`subsumes` is for one-off, interactive checks only. **It must never be called in a loop
over the catalogue** — that is exactly the anti-pattern FR-52 and FR-84 forbid. The
catalogue-wide hierarchy check is one `expand` call (see "Batch discipline" below).

## Editions and versions

`Edition` (`models.py`) carries a SNOMED CT module id, a label (`"au"`/`"int"`), and an
optional pinned `version` (the release's effective time, always a `str` — never an
`int`, the same FR-06 discipline applied to the one other all-digits token in this
domain). `SNOMED_CT_AU` and `SNOMED_CT_INTERNATIONAL` are the two editions FR-47's
dual-edition validation diffs against.

`Edition.system_version_uri` builds `http://snomed.info/sct/<module>[/version/<v>]`. With
no `version` set (FR-49's normal operation), a request targets the latest release, and the
server reports which one it resolved — every result type (`Expansion`, `LookupResult`,
`ValidationResult`) carries that reported, fully qualified version URI, because FR-48
requires it recorded: "a validation you cannot reproduce is not evidence." Pin a
historical run with `edition.pinned_to("20260531")`.

## Batch discipline

FR-52 forbids one `$validate-code` (or `$lookup`) call per catalogue code — at the PRD's
20,000-entry planning ceiling that is 40,000 sequential requests. The required shape:

1. **Bulk status resolution**: chunk the catalogue (200–500 codes; issue #27 makes the
   chunk size configurable) and resolve status with one `expand` call per chunk, using
   `nptc_shared.terminology.snomed.ecl_set_of(codes)` to build the disjunction.
2. **Targeted `lookup`** only for the delta — codes absent from the expansion, or whose
   FSN differs.
3. **Bounded concurrency** on the second pass (issue #27).
4. **Respect HTTP caching**, and honour `Retry-After` on 429 with exponential backoff
   (built into `OntoserverClient`; see "Errors" below).

FR-84's hierarchy check is the same primitive, once: expand
`(codes) MINUS <<71388002` and anything left in the result violates the check.

**A notation trap worth remembering.** The PRD writes this idiom as
`(<code1> OR <code2> OR ... OR <codeN>) MINUS <<71388002` — those angle brackets around
`code1` are the PRD's own placeholder notation, not ECL's descendant-of operator.
`ecl_set_of` never emits a literal `<` for a plain code; only `<<71388002` (built by the
caller) uses the real operator. Getting this backwards — wrapping every code in `<` —
would ask for each code's descendants (empty, for a leaf procedure) and make the check
silently pass everything, forever. Appendix A.10 confirms the intent by using the same
`MINUS` wording over the literal code list.

## Errors and FR-54

Every failure is an exception (`errors.py`), never a default-valued result — an
`Expansion` that is empty means the server answered and nothing matched; a failure never
produces one.

| Condition | Raised |
|---|---|
| Timeout, after retries | `TerminologyTimeoutError` (retryable) |
| Connection/transport failure, after retries | `TerminologyTransportError` (retryable) |
| 429 or 503, after retries | `TerminologyRateLimitError`, carries `.retry_after` |
| Other 5xx, after retries | `TerminologyStatusError`, `retryable=True` |
| 4xx | `TerminologyStatusError` with parsed `OperationOutcome` issues, `retryable=False`, never retried |
| 2xx body is an `OperationOutcome` | `TerminologyOutcomeError` — never parsed as an empty result |
| 2xx body is not the expected resource, or a code arrives as a JSON number | `TerminologyProtocolError` |

`TerminologyError.retryable` is the whole of this package's contribution to FR-54: a
caller (the P3 validation sweep, the P1 API) uses it to mark a run incomplete and retry
the transient half, without re-deriving the classification from a status code at every
call site. The *policy* FR-54 asks for — incomplete runs, cached prior results staying
visible and dated, browsing/searching/editing unaffected by an outage — is the caller's;
this client's obligation stops at failing loudly and classifiably.

Retry/backoff: 429 and 503 honour a `Retry-After` header (both delta-seconds and
HTTP-date forms), falling back to exponential backoff (`0.5 * 2^attempt`, capped at
`max_backoff_seconds`) when absent. 4xx is never retried.

## Configuration

`shared/` has no settings framework (`pydantic-settings` is backend-only, ADR-0001), so
`TerminologyConfig` (`config.py`) is an explicit frozen dataclass with a `from_env()`
classmethod. See [configuration.md](../operations/configuration.md) for the four
`NPTC_TX_*` variables it reads.

## Testing

`shared/tests/test_terminology_contract.py` is the FR-53 acceptance criterion made
mechanical: every test in it runs once against `StubTerminologyClient` and once against
`OntoserverClient` (via `httpx.MockTransport`, which opens no socket), both seeded from
the same captured FHIR response bodies in `shared/tests/fixtures/terminology/`, parsed by
the same production functions in `fhir.py`. A behaviour one implementation has and the
other lacks cannot pass. `test_terminology_ontoserver.py` and `test_terminology_stub.py`
cover what is specific to each implementation — URL construction, retry/backoff, and the
stub's small ECL subset, respectively.

An autouse fixture in `shared/tests/conftest.py` monkeypatches `httpx.HTTPTransport` to
raise if any test tries a real HTTP request — a local, immediate version of what
`ci.yml`'s `transform-offline` job proves after the fact by blocking egress with
`iptables` (NFR-37).

## Manual smoke check

CI can never run this (NFR-37 forbids network access in the test suite), so it is a
manual, one-off check that the fixtures still match a live server's shape:

```python
from nptc_shared.terminology import OntoserverClient, SNOMED_CT_AU, PROCEDURE_ROOT_CODE
from nptc_shared.terminology.snomed import ecl_set_of

with OntoserverClient() as client:
    result = client.lookup("122192001", edition=SNOMED_CT_AU)
    assert result.fully_specified_name == "Acanthamoeba culture (procedure)"

    violations = client.expand(
        f"({ecl_set_of(['122192001', '71388002'])}) MINUS <<{PROCEDURE_ROOT_CODE}",
        edition=SNOMED_CT_AU,
    )
    assert violations.codes == ()
```

## Not implemented here

- Chunking, bounded concurrency and the dual-edition diff (FR-47, FR-52, FR-84 at
  catalogue scale) — issue #27.
- Designation reconciliation at scale (FR-97) — issue #28.
- HTTP response caching, OAuth2 client-credentials, and any transform/backend call site —
  all deferred; see ADR-0003's Consequences.
