# ADR-0005: Batch sweep defaults — chunk size 300, delta concurrency 4, first pass sequential

**Status:** Accepted
**Date:** 2026-08-06

## Context

FR-52 forbids validating the catalogue one `$validate-code` per code per edition — 40,000
sequential requests at the PRD's 20,000-entry planning ceiling — and prescribes the shape of
what replaces it: chunked `ValueSet/$expand` for bulk status, a targeted `$lookup` pass for
the delta, bounded concurrency on that second pass, and `Retry-After`/backoff on 429. FR-84
adds a single batch expansion of `(codes) MINUS <<71388002` for the hierarchy check. P0-5
(issue #27) implements both, in `nptc_shared.terminology.sweep`.

Two numbers have to be chosen, and FR-52 deliberately does not fix either:

> Determine chunk size empirically against the target Ontoserver instance; start around 200
> to 500 codes and tune. […] **Bounded concurrency** on the second pass, with a configurable
> ceiling. Default conservatively.

**What tuning evidence actually exists.** Honestly: not much, and this ADR should not
pretend otherwise.

- PRD Appendix A.10 (performed for PRD v0.2, against `tx.ontoserver.csiro.au`) resolved all
  50 sample codes in a **single ECL value set expansion**, and expanded
  `(all 50 codes) MINUS <<71388002` in one request returning zero results. That is the only
  measurement against a live server anywhere in this repository. It establishes that the
  idiom works and that 50 codes in one expansion is comfortable; it says nothing about where
  the ceiling is.
- No load test at 200/300/500 has been run, because the target instance is a shared public
  service and NFR-37 keeps the test suite off the network entirely — nothing in CI can
  measure this, now or later.
- The published SPIA Requesting workbook is roughly 1,300 entries, not 20,000. At chunk size
  300 that is 5 expansions per edition, 10 for a dual-edition seeding run; the planning
  ceiling would be 67 per edition.

So the defaults are a judgement inside FR-52's stated range, not a measurement — and the
right response to that is to make them configurable, state the tuning procedure, and record
that they are untuned rather than burying the fact in a constant.

## Decision

1. **`chunk_size` defaults to 300**, the midpoint of FR-52's 200–500 range, configurable via
   `NPTC_TX_CHUNK_SIZE`. The midpoint because the risk is asymmetric but bounded at both
   ends: too small wastes round trips (recoverable, just slower), too large risks a server
   or proxy rejecting the request outright (a hard failure, but a loud one — the sweep
   raises, it never silently under-reports).
2. **`max_concurrency` defaults to 4**, configurable via `NPTC_TX_MAX_CONCURRENCY`. Four is
   "conservative" as FR-52 asks: enough to hide per-request latency on the delta pass,
   low enough that a seeding run is not mistaken for an attack on a shared server, and low
   enough that it stays under any plausible per-client connection ceiling.
3. **The first pass (chunk expansions) is sequential; only the delta pass is concurrent.**
   FR-52 puts bounded concurrency on the second pass specifically, and the first pass is
   both the smaller number of requests (67 at the ceiling) and the more expensive request
   for the server to serve.
4. **The FR-84 hierarchy check is one request for the whole catalogue**, not chunked — the
   requirement and issue #27's acceptance criterion both say one. `OntoserverClient` already
   switches `$expand` from GET to POST past a URL-length budget, which is what makes a
   20,000-code ECL expressible at all. Paging exists in the loop but engages only if a
   server caps the page below the number of violations found.
5. **Both values are validated at construction**: a `chunk_size` or `max_concurrency` below
   1 raises `TerminologyConfigError` rather than being coerced to a default. A zero chunk
   makes no progress and a negative one slices to nothing; either would let a sweep report a
   catalogue it never checked as clean, which is the FR-54 hazard exactly.
6. **Retry, `Retry-After` and exponential backoff are not reimplemented in the sweep.** They
   live in `OntoserverClient` (ADR-0003, issue #26) and the sweep inherits them, so a sweep
   against the stub and a sweep against a real server differ in no respect the sweep can
   see.

### How to tune these against a real instance

The procedure, for whoever first runs a seeding transform against a live server:

1. Run `nptc-transform run --workbook <workbook> --check-terminology` with
   `NPTC_TX_CHUNK_SIZE` at 200, then 300, then 500, timing each run.
2. Watch for 413 (payload too large), 414 (URI too long) or a 400 naming ECL length — any of
   these means the chunk is past what the server or its proxy accepts, and the sweep will
   raise rather than degrade.
3. Keep the largest size that completes cleanly with a comfortable margin, not the largest
   that completes at all: the ECL grows with the number of digits in the codes, and AU
   extension identifiers are 16–18 digits against International's 8–9.
4. Raise `NPTC_TX_MAX_CONCURRENCY` only if the delta pass dominates the wall clock, and only
   with the server operator's knowledge. On a first seeding run the delta is expected to be
   large (many codes will not resolve), which is exactly when restraint matters most.

Record the result here as an amendment when it exists.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Hard-code 300 and 4 as constants, no environment variables | FR-52 explicitly requires the concurrency ceiling be configurable, and requires the chunk size be tuned per instance — which cannot be done if changing it is a code change and a release. |
| Default the chunk size to 500 (the top of FR-52's range) to minimise requests | The failure at the top of the range is a rejected request, and the value of the extra 200 codes per chunk is a handful of round trips on a run that happens rarely. Optimising a seeding run's wall clock at the cost of a hard failure against an untested server is the wrong trade. |
| Make the first pass concurrent as well | FR-52 places concurrency on the second pass, and the first pass is the heavier request. It is also where a mistake is least recoverable: 67 concurrent large ECL expansions against a shared public server is precisely the "inconsiderate" behaviour the requirement names. If the first pass ever dominates the wall clock, that is a measurement worth having before changing this. |
| Chunk the FR-84 hierarchy check the same way as the status pass | Contradicts FR-84 and issue #27's acceptance criterion ("the subsumption check issues exactly one request, asserted by call count"), and the one-request property is itself the thing NFR-38 test 13 verifies. If a server ever refuses the full expression, chunking is the fallback — but it should be a deliberate, documented change with a failing request behind it, not a pre-emptive hedge. |
| Derive the concurrency ceiling from CPU count | The bound exists to be polite to a remote shared server; it has nothing to do with local cores. A 32-core CI runner would produce 32 concurrent requests for no reason at all. |
| Tune the defaults now, by measuring against `tx.ontoserver.csiro.au` | NFR-37 keeps the test suite off the network, so a measurement made once by hand would not be re-checked by anything and would decay into a claim. Better to ship a documented, configurable, honestly-labelled default and record the measurement when someone runs the real seeding transform. |

## Consequences

- Two new environment variables (`NPTC_TX_CHUNK_SIZE`, `NPTC_TX_MAX_CONCURRENCY`) join the
  four in `docs/operations/configuration.md` and `deploy/.env.example`.
- The defaults are **untuned**, and this ADR is where that is said out loud. Anyone reading
  `DEFAULT_CHUNK_SIZE = 300` should land here and find "midpoint of the PRD's range, not a
  measurement", not infer that 300 was measured.
- The sweep uses a `ThreadPoolExecutor` for the delta pass, which makes `nptc_shared` the
  first package in the workspace to run application code on more than one thread. The
  `TerminologyClient` implementations must therefore be thread-safe for concurrent
  `lookup` calls: `httpx.Client` is, and the stub holds only appends and dict reads.
  A `max_concurrency` of 1 skips the pool entirely and runs in the calling thread, so a
  failure surfaces with the caller's own stack.
- A future asynchronous client (`httpx.AsyncClient`) would replace the pool, not the
  interface — the two knobs and their semantics stay.
