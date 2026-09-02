"""HTTP-status-bearing errors for FR-26's live concept lookup route.

`nptc_shared.terminology.errors` carries no `http_status` - it is a
non-API package shared with the transform (FR-74), which has no HTTP
surface at all. These three wrap that package's `TerminologyError`
hierarchy exactly once, at the one place its classification becomes an
HTTP response (`nptc.terminology.concepts.resolve_concept`), following
`nptc.catalogue.bindings`'s own convention of a typed exception per
response family rather than an if/elif ladder at the route body.

Two conditions in FR-26's error table need no new type here: a malformed
or Verhoeff-failing SCTID is `nptc_shared.sctid.InvalidSCTIDError`, already
mapped to 422 by `nptc.api.errors`, and a malformed `NPTC_TX_*` value is
`nptc_shared.terminology.TerminologyConfigError`, already mapped to 500.
"""

from __future__ import annotations

from typing import ClassVar


class ConceptNotFoundError(Exception):
    """The terminology server does not have this code.

    Raised for a 404 from a conformant server, or a 4xx `OperationOutcome`
    that says as much (`nptc_shared.terminology.errors.is_concept_absence`)
    - never for an unrecognised failure, which is exactly what
    `TerminologyUpstreamError` below exists to catch instead. Reading an
    unclassified failure as "not found" would let an unseeded
    `StubTerminologyClient` make a test pass vacuously (see
    `nptc.terminology.concepts`'s own module docstring)."""

    http_status: ClassVar[int] = 404


class TerminologyUnavailableError(Exception):
    """The server could not be reached, or refused with a status that
    stayed retryable through every retry `OntoserverClient` already
    attempted - a timeout, a transport failure, a 5xx, or a 429/503 that
    persisted.

    FR-54's bounded, explained refusal: nothing here degrades a result,
    it only tells the caller the live check could not run. `retry_after`
    carries `TerminologyRateLimitError.retry_after` through when the
    failure was a persisted 429/503, so the route can echo it as a
    `Retry-After` header (`nptc.api.errors`); `None` for every other
    retryable failure, which has no server-supplied wait time to report.
    """

    http_status: ClassVar[int] = 503

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TerminologyUpstreamError(Exception):
    """Every other terminology failure: an unparseable 2xx, a 2xx
    `OperationOutcome`, a 4xx that is not itself an absence answer, or the
    stub's own `StubNotSeededError` (a bare `TerminologyError`, neither a
    status nor a transport failure).

    Deliberately the catch-all rather than `ConceptNotFoundError` - the
    hazard this class exists to avoid is named in
    `nptc.terminology.concepts`'s module docstring: a widened "not found"
    branch would make an unseeded stub, or a genuinely malformed upstream
    response, read as a clean absence instead of the defect either one
    actually is."""

    http_status: ClassVar[int] = 502
