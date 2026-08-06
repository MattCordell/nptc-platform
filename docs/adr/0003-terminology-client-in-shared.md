# ADR-0003: Terminology client in shared/, with httpx as its first runtime dependency

**Status:** Accepted
**Date:** 2026-08-06

## Context

FR-53 requires terminology server access to sit behind an interface with a stub
implementation, so the test suite never depends on a live Ontoserver (NFR-37) and the
endpoint can be repointed at NCTS production or a self-hosted instance as configuration,
not code. PRD Section 15.2 records depending on `tx.ontoserver.csiro.au` as an accepted
risk whose entire mitigation is FR-53 keeping the endpoint configurable: "a known,
configurable, single-line change rather than an oversight."

ADR-0001/FR-74 already settled that the transform must not have a second, divergent
implementation of anything the backend also validates — that is what `shared/` exists
for, and both `shared/src/nptc_shared/__init__.py` and `shared/pyproject.toml`'s
description already named "the terminology client contract" as work owed to this package.
Two consumers need to *call* a server, not merely describe one: the transform, for FR-74's
dual-edition validation at seeding (P0-5, issue #27); and the backend, for FR-26's live
check during form completion and FR-45/FR-50's validation sweep. `shared/` has had zero
runtime dependencies since ADR-0001 (`shared/pyproject.toml`: `dependencies = []`), and no
HTTP client exists anywhere in this workspace's `uv.lock` yet. There is also no settings
framework in Python here yet — `pydantic-settings` is a backend-only dependency, unused
even there.

## Decision

1. The `TerminologyClient` Protocol, `StubTerminologyClient`, and the Ontoserver HTTP
   implementation (`OntoserverClient`) all live in `shared/src/nptc_shared/terminology/`.
   One implementation, used identically by `backend` and `transform` (FR-74).
2. `shared/` takes `httpx` as its first runtime dependency (`shared/pyproject.toml`:
   `dependencies = ["httpx>=0.28"]`).
3. The contract is synchronous (`httpx.Client`, not `httpx.AsyncClient`).
4. Authentication is anonymous by default against `https://tx.ontoserver.csiro.au/fhir`,
   with an optional static bearer token read from `NPTC_TX_TOKEN`. Full OAuth2
   client-credentials is deferred to a future issue.
5. Configuration is an explicit frozen dataclass (`TerminologyConfig`) with a
   `from_env()` classmethod reading `os.environ`, not `pydantic-settings`.

`OntoserverClient`'s transport is injectable (`transport=httpx.MockTransport(...)`), not
the whole `httpx.Client` — a test that swaps only the transport still exercises the real
base-URL, header and query-string construction, and `httpx.MockTransport` opens no socket,
so `OntoserverClient` itself runs inside `ci.yml`'s `transform-offline` egress-blocked job
(NFR-37) alongside `StubTerminologyClient`. One test suite,
`shared/tests/test_terminology_contract.py`, runs against both implementations, seeded
from the same captured FHIR response bodies parsed by the same production parsers — the
issue's acceptance criterion ("the Ontoserver implementation and the stub satisfy the same
contract test suite") made mechanical rather than a documentation claim.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Keep `shared/` dependency-free: `TerminologyClient` Protocol in `shared/`, the Ontoserver implementation in `transform/` | The backend needs the identical implementation for FR-26 and FR-45, not just the transform. Putting it in `transform/` forces a choice between duplicating it in `backend/` (the exact divergence FR-74/ADR-0001 exist to prevent) or having the backend depend on the transform package, which inverts the intended dependency direction. |
| The Ontoserver implementation in `backend/`, with the transform calling the backend's API instead of a terminology server directly | Inverts the dependency the other way, and makes the P0 seeding transform depend on a backend that does not exist yet — the PRD's P0 phase is explicitly a standalone deliverable that runs before the backend is built. |
| `httpx.AsyncClient` | Buys concurrency neither current consumer is positioned to use: the transform is a batch CLI, and FR-52's bounded concurrency (issue #27) is a `ThreadPoolExecutor` over a few hundred requests, not a request-serving hot path. Async would force `pytest-asyncio` into the workspace, an async/sync split through `shared/`, and `asyncio.run()` wrappers at every call site — cost paid up front for a benefit nothing here currently needs. |
| `requests` instead of `httpx` | No equivalent to `httpx.MockTransport` without adding a second test-only dependency (`responses` or `requests-mock`) — which is what makes one contract suite exercise both implementations offline (NFR-37) without extra tooling. |
| `urllib.request` (standard library, keeps `shared/` dependency-free) | Keeps the dependency count at zero but hand-rolls connection pooling, timeouts, redirects and retries, and leaves no clean seam for offline contract testing — the cost shows up as more code and weaker tests, not as a smaller dependency graph. |
| `pydantic-settings` for `TerminologyConfig` | Backend-only per ADR-0001; adding it to `shared/` would pull pydantic into the transform, which has no other use for it. An explicit `from_env()` reading `os.environ` is roughly twenty lines and needs no new dependency. |

## Consequences

- `shared/` is no longer dependency-free. `transform/` and `backend/` both pick up
  `httpx` (and its own transitive dependencies: `httpcore`, `certifi`, `h11`) via
  `nptc-shared`, even when a consumer never calls the terminology client — accepted, since
  both already needed it directly or would soon.
- FastAPI's `TestClient` (which itself requires `httpx`) becomes available to the backend
  test suite at no additional dependency cost, once the backend has any routes to test.
- Repointing at NCTS production or a self-hosted Ontoserver is a `NPTC_TX_BASE_URL`
  change, not a code change — the configurable-endpoint mitigation PRD Section 15.2
  promises for its accepted risk.
- OAuth2 client-credentials, if a future NCTS endpoint requires it, adds fields to
  `TerminologyConfig` and a token-acquisition path inside `OntoserverClient` without
  changing the `TerminologyClient` Protocol or `StubTerminologyClient` at all.
- Issue #27 (chunked FR-52/FR-84 validation) adds bounded concurrency over this
  synchronous client via `ThreadPoolExecutor`, plus `NPTC_TX_CHUNK_SIZE` and
  `NPTC_TX_MAX_CONCURRENCY` configuration, without changing this contract.
