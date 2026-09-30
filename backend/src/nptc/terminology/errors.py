"""HTTP-status-bearing errors for FR-26's live concept lookup route.

`nptc_shared.terminology.errors` carries no `http_status` because the transform
shares it (FR-74). These three types wrap its `TerminologyError` hierarchy once,
in `nptc.terminology.concepts.classify_terminology_error`.

Two conditions in FR-26's error table need no type here. A malformed or
Verhoeff-failing SCTID is `nptc_shared.sctid.InvalidSCTIDError` (422), and a
malformed `NPTC_TX_*` value is `nptc_shared.terminology.TerminologyConfigError`
(500). Both already map in `nptc.api.errors`.
"""

from __future__ import annotations

from typing import ClassVar


class ConceptNotFoundError(Exception):
    """The terminology server does not have this code.

    Raised for a 404 from a conformant server, or a 4xx `OperationOutcome`
    that says as much (`nptc_shared.terminology.errors.is_concept_absence`).
    Never raised for an unrecognised failure: that is `TerminologyUpstreamError`.
    """

    http_status: ClassVar[int] = 404


class TerminologyUnavailableError(Exception):
    """The server could not be reached, or stayed retryable through every retry
    `OntoserverClient` made: a timeout, a transport failure, a 5xx, or a
    persisted 429/503.

    This is FR-54's bounded refusal: no result is degraded, the live check
    simply could not run. `retry_after` carries
    `TerminologyRateLimitError.retry_after` for a persisted 429/503, so the
    route can echo it as `Retry-After` (`nptc.api.errors`). It is `None` for
    every other failure.
    """

    http_status: ClassVar[int] = 503

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TerminologyUpstreamError(Exception):
    """Every other terminology failure: an unparseable 2xx, a 2xx
    `OperationOutcome`, a 4xx that is not an absence answer, or the stub's
    `StubNotSeededError` (a bare `TerminologyError`).

    This is the catch-all and is never a 404. Reading an unclassified failure
    as "not found" would let an unseeded `StubTerminologyClient` answer every
    lookup with a clean absence, and a malformed upstream response read as a
    missing code, instead of the defects they are.
    """

    http_status: ClassVar[int] = 502
