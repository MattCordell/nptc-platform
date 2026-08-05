# ADR-0002: Requirement evidence without a pytest test

**Status:** Accepted
**Date:** 2026-08-05

## Context

`CONTRIBUTING.md`'s Definition of Done and `scripts/traceability_check.py` both make a
requirement's `implemented` status in `docs/requirements/requirements.yaml` conditional on
a test carrying `@pytest.mark.req("<id>")`. That works for the overwhelming majority of
FR-nn/NFR-nn requirements, which describe application behaviour a test can exercise
directly.

It does not work for infrastructure and process requirements whose evidence is CI
configuration or a governance document, not application code:

- **NFR-37** ("The test suite MUST run with no network access") is built: the
  `transform-offline` job in `.github/workflows/ci.yml` blocks outbound egress with
  `iptables` and runs `transform/tests` and `shared/tests` against it (issue #5/F-2,
  closed). There is no plausible pytest test for "this CI job's network is blocked" — a
  test running inside that same sandboxed job could only assert its own inability to reach
  the network, which is indistinguishable from a misconfigured test environment, not proof
  the *intended* egress block is in place.
- **NFR-29** (maintain a hazard log) is evidenced by `docs/governance/hazard-log.md`
  existing and being populated — a document, not a code path a test can assert against.

Without a way to record this, both requirements are stuck at `in-progress` permanently
while the underlying work is actually done, and the register under-reports what is
finished. This started to matter once real markers landed on real requirements (FR-70,
FR-73 via the transform CLI work) — the traceability mechanism is no longer sitting idle
with nothing to check, so a gap in what it can represent is now a live cost, not a
theoretical one.

## Decision

Add an optional `evidence:` field to a requirement entry: a repo-relative path, optionally
with a `#fragment` for readability (e.g. `.github/workflows/ci.yml#transform-offline`),
pointing at whatever demonstrates the requirement outside of a test.

`traceability_check.py` treats `evidence:` as an alternative to a test marker, not an
unchecked free-text field:

- `implemented` requires a test marker **or** an `evidence:` path — not neither.
- An `evidence:` path that does not exist on disk is a CI failure, same severity as a
  broken test marker reference. This is what keeps the field honest rather than becoming a
  write-only escape hatch nobody re-checks.
- A requirement carrying **both** a test marker and `evidence:` is a CI failure. A
  requirement with a real test does not need a hand-written pointer standing in for it;
  allowing both invites the two to drift out of sync silently.

`docs/requirements/traceability.md` gains an `Evidence` column so the report shows, per
requirement, which of the two kinds of proof backs it.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A new `verified-in-ci` status, distinct from `implemented` | Correct in spirit, but adds a second terminal status every future reader of the register has to learn and distinguish from `implemented` — for a distinction (test vs. non-test evidence) that matters to CI tooling, not to anyone reading the register to answer "is this done". |
| Accept it and document it: some NFRs stay `in-progress` forever | Cheapest, but leaves the register stating something false by omission — a reader has no way to tell "in-progress because unfinished" from "in-progress because untestable", and the traceability report's own header claims completeness it would not have. |
| A frontend `test.meta({req: '<id>'})` equivalent, extending the marker mechanism instead | Solves a different problem (frontend requirements needing markers) and does nothing for CI-configuration or document-backed requirements, which have no test runtime to attach a marker to regardless of language. The requirements.yaml header previously claimed this equivalent existed; it does not, and this ADR does not introduce it. |

## Consequences

- NFR-37 moves to `implemented` with `evidence: ".github/workflows/ci.yml#transform-offline"`.
- NFR-29 stays `in-progress` with `evidence: "docs/governance/hazard-log.md"` — the log
  exists but its owner (OI-6) is still unassigned, so the requirement is genuinely not
  finished. This is the intended shape: `evidence:` records what backs a requirement,
  independent of whether that's enough to call it `implemented`.
- Future infrastructure/process requirements (e.g. future NFRs about deployment, backups,
  or documentation) can reach `implemented` the same way, without inventing a new
  mechanism each time.
- `scripts/tests/test_traceability_check.py` covers: evidence satisfies `implemented`, a
  missing evidence path fails, an evidence path plus a test marker fails, and a `#fragment`
  resolves to the underlying file rather than a literal (nonexistent) path.
