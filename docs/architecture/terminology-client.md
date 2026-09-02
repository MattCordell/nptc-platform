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

`TerminologySweep` (`sweep.py`) sits on top of the Protocol, not beside it: it is the one
batch caller both the transform and the backend use, so FR-52's request discipline and
FR-84's hierarchy check exist once (FR-74). See "Batch discipline" below.

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

`Edition` (`models.py`) carries a SNOMED CT module id, a label (`"au"`/`"int"`), an
optional pinned `version` (the release's effective time, always a `str` — never an
`int`, the same FR-06 discipline applied to the one other all-digits token in this
domain), and an optional `display_language`. `SNOMED_CT_AU` and
`SNOMED_CT_INTERNATIONAL` are the two editions FR-47's dual-edition validation diffs
against; only `SNOMED_CT_AU` sets `display_language` (to `AU_LANGUAGE_TAG`), because
that language reference set doesn't exist in the International edition — sending it on
both would risk a server-side fallback returning some other language's preferred term
labelled as if it were AU's. `Edition.pinned_to` carries `display_language` through
unchanged, so FR-49's reproduce-a-historical-run path doesn't silently drop it.

`Edition.system_version_uri` builds `http://snomed.info/sct/<module>[/version/<v>]`. With
no `version` set (FR-49's normal operation), a request targets the latest release, and the
server reports which one it resolved — every result type (`Expansion`, `LookupResult`,
`ValidationResult`) carries that reported, fully qualified version URI, because FR-48
requires it recorded: "a validation you cannot reproduce is not evidence." Pin a
historical run with `edition.pinned_to("20260531")`.

## Batch discipline

FR-52 forbids one `$validate-code` (or `$lookup`) call per catalogue code — at the PRD's
20,000-entry planning ceiling that is 40,000 sequential requests. `TerminologySweep`
(`sweep.py`) is the implementation, shared by the transform and the backend because FR-74
forbids the migration path having its own:

1. **Bulk status resolution**: chunk the catalogue (`NPTC_TX_CHUNK_SIZE`, default 300) and
   resolve status with one `expand` call per chunk with `activeOnly=true`, using
   `nptc_shared.terminology.snomed.ecl_set_of(codes)` to build the disjunction. Codes are
   de-duplicated and sorted first, so the request sequence depends on the *set* of codes,
   not on row order.
2. **Targeted `lookup`** only for the delta — the codes the expansion did not return. That
   call is what separates "inactive" (and, with it, FR-46's inactivation reason and
   historical association) from "not in this edition at all", which a conformant server
   answers with a 404. It requests `inactive` and FR-46's historical-association property
   codes explicitly (`SAME_AS`, `MOVED_TO`, `POSSIBLY_EQUIVALENT_TO`, `WAS_A`,
   `REPLACED_BY`) rather than relying on a server volunteering them unprompted — FHIR R4
   makes no such guarantee, and `LookupResult.inactive` coming back `None` (not reported,
   distinct from reported-false) would otherwise misclassify an active code as inactive.
3. **Bounded concurrency** on that second pass (`NPTC_TX_MAX_CONCURRENCY`, default 4),
   submitted in batches rather than all at once — a failure in one batch means the next is
   never submitted, rather than every remaining code being queued before the failure
   surfaces. The chunk expansions themselves are sequential — see
   [ADR-0005](../adr/0005-sweep-chunk-size-and-concurrency-defaults.md) for both defaults
   and why they are untuned.
4. **Respect HTTP caching**, and honour `Retry-After` on 429 with exponential backoff
   (built into `OntoserverClient`, inherited by the sweep; see "Errors" below).

`expand` makes exactly one request per call and never pages on its own. A server-side
page-size ceiling can cap the returned page below what `total` promises — check
`Expansion.is_complete` and re-call with an advanced `offset` if it is `False`; treating a
single call as exhaustive would let a truncated page look identical to a genuinely short
result. `TerminologySweep` owns that loop, comparing the *accumulated* count against
`total` (`is_complete` compares one page, which is the right question for a single call and
the wrong one while paging), and a code missing from a truncated page is recovered by the
delta pass rather than reported absent.

FR-84's hierarchy check is the same primitive, chunked the same way: expand
`(chunk) MINUS <<71388002` per `chunk_size` chunk of the codes the status/delta passes
already resolved, and anything left in a chunk's result violates the check. Not one request
for the whole catalogue — a single disjunction over 20,000 codes is itself too large to send
(measured: ~340KB of percent-encoded ECL; see [ADR-0005](../adr/0005-sweep-chunk-size-and-concurrency-defaults.md)'s
2026-08-07 amendment). Only *resolved* codes are offered to this ECL at all, never every
code handed to the sweep — a code absent from the edition is excluded before the request is
built, rather than relying on every server tolerating an unknown concept reference inside
one, and absence is always the status pass's finding, never a false hierarchy violation.

FR-99's semantic-tag warning rides on the bulk pass rather than costing requests of its
own: the status expansion asks for designations, so the served FSN — and therefore its tag,
read by `snomed.semantic_tag` — is already in hand for every concept the expansion
returned. A concept already reported as an FR-84 violation is not also tag-warned, and a
concept whose FSN the server did not return is not warned at all: "no tag observed" is not
evidence of a wrong tag — but it is counted, in `SweepResult.unresolved_fsn_count`, since a
server that never returns an identifiable FSN for anything would otherwise make the check
pass silently and permanently, with nothing to show it never ran. Paging that overlaps
(a server ignoring `offset`) is de-duplicated by code before either the tag list or that
count is built, so a repeated page cannot double-count either one.

FR-97's seeding-time designation reconciliation rides on the same bulk pass, for the same
reason: `SweepResult.designations` is a deduplicated, sorted `ConceptDesignations` per
active code — FSN, AU-language `display`, and every designation value the expansion
returned — projected from the concepts `_unexpected_tags` already reads, at zero further
requests. Only the labels that projection cannot settle locally cost anything:
`TerminologySweep.confirm_labels` issues one `CodeSystem/$validate-code` per unique
`(code, display)` pair still unmatched, batched and bounded the same way the delta
`lookup` pass is (`nptc_transform.designation_check` is the caller — see the
[transform runbook](../operations/runbooks/transform.md#interpreting-a-designation-finding-fr-97)
for the four outcomes it classifies). Unlike the delta pass, a probe failure is never
folded into "no match" — every code probed already resolved as active in this same sweep,
so an error here is a contradiction with the status pass, not an answer, and propagates
rather than becoming a false designation defect (FR-54).

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

## FR-26: the interactive lookup route (issue #240)

`GET /api/v1/terminology/concepts/{code}` (`nptc.api.routers.terminology`,
`nptc.terminology.concepts.resolve_concept`) is the one interactive caller of this
package's `lookup` operation - the form-completion half of FR-26, distinct from
`TerminologySweep`'s batch callers above. It resolves one code's served FSN (semantic
tag intact, FR-82), AU preferred term, and tri-state active status, so #150's code
binding edit screen can accept a code and derive the labels itself rather than trusting
an editor to type them.

Edition is fixed to `SNOMED_CT_AU` in code, never a query parameter - the same
`display_language`-ambiguity reasoning "Editions and versions" above gives for why only
the AU edition carries it. Only the `inactive` property is requested, not FR-46's
inactivation-reason/historical-association set: FR-26 asks for active status, not a
"replace with the successor" affordance, and that reading belongs to FR-46/FR-47's own
table. `au_preferred_term` is `LookupResult.display` under `display_language=
AU_LANGUAGE_TAG`, never a designation scan - see "Editions and versions" again for why a
second rule here would silently disagree with the sweep's own.

The route's error table reuses this package's classification (`TerminologyError.
retryable`, `errors.is_concept_absence` - promoted out of `sweep.py`'s own private
helper for exactly this second caller, FR-74) rather than re-deriving it:

| Condition | HTTP |
|---|---|
| Malformed or Verhoeff-failing SCTID (pre-flight, no request) | 422 |
| `is_concept_absence` | 404 |
| `TerminologyRateLimitError`, or a timeout/transport failure, or another retryable `TerminologyStatusError` | 503 (`Retry-After` when the server supplied one) |
| Anything else - an unparseable body, a 2xx `OperationOutcome`, an unclassified 4xx, or the stub's own `StubNotSeededError` | 502 |
| `TerminologyConfigError` - a malformed `NPTC_TX_*` value | 500 |

The fourth row is deliberately the catch-all, never 404: reading an unrecognised failure
as "not found" would let an unseeded `StubTerminologyClient` answer a test with a
clean-looking absence instead of the authoring defect it actually is - see `stub.py`'s
own module docstring. The last row is `resolve_concept` re-raising `TerminologyConfigError`
unchanged rather than letting it reach that same catch-all - it is itself a
`TerminologyError` subclass, so without that carve-out a malformed `NPTC_TX_*` value
would misreport as 502 instead of the 500 `nptc.api.errors` already gives it; in normal
operation this is a start-up failure (`create_app` builds the client eagerly), so 500
here only covers a path that bypasses that warm-up. No server-side cache and no bespoke
rate limiter: FR-82 forbids a stale served label, and `Permission.REGISTRY_READ` already
bounds and attributes traffic to signed-in, submission-capable callers, which is the
control an anonymous limiter cannot provide.

## Not implemented here

- FR-47's *forecast* finding — a concept inactivated in International while still active in
  AU. `TerminologySweep` reports status per edition and the transform combines them
  (`nptc_transform.terminology_check`), but the forecast, with its expected AU release date,
  belongs to the scheduled validation sweep.
- FR-54's degradation *policy* — incomplete runs, cached prior results staying visible and
  dated. The sweep's obligation stops at raising; the transform CLI's response is to exit 3
  and write no report at all.
- HTTP response caching and OAuth2 client-credentials — deferred; see ADR-0003's
  Consequences.
