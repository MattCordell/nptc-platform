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

- `implemented` requires a test marker **or** an `evidence:` path (or both) — not neither.
- An `evidence:` path is checked for existence, that it is a file (not a directory), and
  that it resolves inside the repository root (rejecting `../` traversal and absolute
  paths) — same failure severity as a broken test marker reference. This is what keeps the
  field honest rather than becoming a write-only escape hatch nobody re-checks.
- If the path carries a `#fragment`, the fragment string must appear literally somewhere in
  the target file's text. This is a weak check — it does not parse a YAML job ID or a
  Markdown heading, just a substring search — but it is cheap, works across every file
  format an evidence path might name, and catches the case that actually matters: the
  fragment's target being renamed or deleted out from under it.
- A requirement may carry **both** a test marker and `evidence:` — they are not mutually
  exclusive. A requirement can be partially demonstrated by a test and partially by a
  non-test artefact (NFR-37 is exactly this shape once backend tests also run offline: a
  marker on the backend test, `evidence:` still pointing at the CI job that blocks egress
  for the rest of the suite). Treating "both present" as an error would force deleting a
  true statement to satisfy the checker.
- The `traceability` CI job (`.github/workflows/docs.yml`) runs unconditionally on every
  PR, not gated on a path filter — `evidence:` can point anywhere in the repository, so
  there is no enumerable set of paths whose changes should trigger a re-check.

`docs/requirements/traceability.md` gains an `Evidence` column, and its summary line a
"With evidence: N" count, so the report shows, per requirement, which kind(s) of proof
back it.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A new `verified-in-ci` status, distinct from `implemented` | Correct in spirit, but adds a second terminal status every future reader of the register has to learn and distinguish from `implemented` — for a distinction (test vs. non-test evidence) that matters to CI tooling, not to anyone reading the register to answer "is this done". |
| Accept it and document it: some NFRs stay `in-progress` forever | Cheapest, but leaves the register stating something false by omission — a reader has no way to tell "in-progress because unfinished" from "in-progress because untestable", and the traceability report's own header claims completeness it would not have. |
| A frontend `test.meta({req: '<id>'})` equivalent, extending the marker mechanism instead | Solves a different problem (frontend requirements needing markers) and does nothing for CI-configuration or document-backed requirements, which have no test runtime to attach a marker to regardless of language. The requirements.yaml header previously claimed this equivalent existed; it does not, and this ADR does not introduce it. |

## Consequences

- NFR-37 gets `evidence: ".github/workflows/ci.yml#transform-offline"` recorded, but stays
  `in-progress`: that job only blocks egress for `transform/tests` and `shared/tests`, and
  `ci.yml`'s `python` job runs `backend/tests` with unrestricted network, so the
  requirement is not yet met for the whole test suite. `evidence:` records what backs a
  requirement independent of whether that is enough to call it `implemented` — it is not,
  here, until backend integration tests get the same treatment (P1-1).
- NFR-29 stays `in-progress` with `evidence: "docs/governance/hazard-log.md"` — the log
  exists but its owner (OI-6) is still unassigned, so the requirement is genuinely not
  finished. Same shape as NFR-37: evidence recorded, status reflecting that it is not done.
- Future infrastructure/process requirements (e.g. future NFRs about deployment, backups,
  or documentation) can reach `implemented` the same way, without inventing a new
  mechanism each time.
- `scripts/tests/test_traceability_check.py` covers: evidence satisfies `implemented`; a
  missing, non-file, or root-escaping evidence path fails; a `#fragment` present in the
  target file passes and one that is absent fails; a test marker and `evidence:` together
  is allowed; and the report's `Evidence` column and "With evidence" count render correctly.
