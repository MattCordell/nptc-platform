"""The FR-53 terminology client's exception hierarchy.

One base, so a caller implementing FR-54's graceful degradation can catch a
single type. ``retryable`` is the only piece of FR-54 machinery this package
builds: it classifies every failure so a caller (the P3 validation sweep, the
P1 API) can mark a run incomplete and retry the transient half without
re-deriving that classification from a status code at every call site. The
*policy* FR-54 asks for - incomplete runs, cached prior results staying
visible and dated, browsing/searching/editing unaffected by an outage - is
the caller's; this package's entire FR-54 obligation is to fail loudly and
classifiably, never to return a default-valued result on failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from nptc_shared.terminology.models import Operation


@dataclass(frozen=True, slots=True)
class OperationOutcomeIssue:
    """One issue from a FHIR ``OperationOutcome``."""

    severity: str
    code: str
    diagnostics: str | None = None
    expression: tuple[str, ...] = ()


class TerminologyError(Exception):
    """Base for every failure this package raises.

    Carries ``operation`` so a raised error names what was being asked for,
    not only what went wrong.
    """

    retryable: bool = False

    def __init__(self, message: str, *, operation: Operation | None = None) -> None:
        super().__init__(message)
        self.operation = operation


class TerminologyConfigError(TerminologyError):
    """A ``TerminologyConfig.from_env`` value could not be parsed."""


class TerminologyTransportError(TerminologyError):
    """No HTTP response was obtained at all: DNS, TLS, connect or read failure."""

    retryable = True


class TerminologyTimeoutError(TerminologyTransportError):
    """The request timed out."""


class TerminologyStatusError(TerminologyError):
    """An HTTP response with a non-2xx status.

    Carries the parsed ``OperationOutcome`` issues when the body served one -
    the overwhelmingly common shape for a 4xx from a FHIR server - rather
    than a separate class for that case.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: Operation | None = None,
        status_code: int,
        issues: tuple[OperationOutcomeIssue, ...] = (),
    ) -> None:
        super().__init__(message, operation=operation)
        self.status_code = status_code
        self.issues = issues

    @property
    def retryable(self) -> bool:  # type: ignore[override]
        return self.status_code >= 500 or self.status_code == 429


class TerminologyRateLimitError(TerminologyStatusError):
    """429 or 503 persisted after retries were exhausted."""

    def __init__(
        self,
        message: str,
        *,
        operation: Operation | None = None,
        status_code: int,
        issues: tuple[OperationOutcomeIssue, ...] = (),
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, operation=operation, status_code=status_code, issues=issues)
        self.retry_after = retry_after


class TerminologyProtocolError(TerminologyError):
    """A 2xx response whose body was not the resource asked for.

    Includes a body that fails to parse as JSON, one with the wrong
    ``resourceType``, and one carrying a SNOMED CT identifier as a JSON
    number rather than a string (FR-06's chokepoint at the wire boundary).
    """


class TerminologyOutcomeError(TerminologyProtocolError):
    """A 2xx response whose body is an ``OperationOutcome``.

    Distinct from ``TerminologyStatusError`` because this is precisely the
    case that, parsed leniently as if it were the expected resource, would
    yield an empty ``Expansion`` and read as "nothing matched" instead of
    "the server refused this request" (FR-54's hazard).
    """

    def __init__(
        self,
        message: str,
        *,
        operation: Operation | None = None,
        issues: tuple[OperationOutcomeIssue, ...] = (),
    ) -> None:
        super().__init__(message, operation=operation)
        self.issues = issues


#: ``OperationOutcome`` issue codes that mean "this code is not in this
#: edition" rather than "this request failed" - see ``is_concept_absence``.
NOT_FOUND_ISSUE_CODES = frozenset({"not-found", "code-invalid", "invalid-code"})


def is_concept_absence(exc: TerminologyError) -> bool:
    """True if ``exc`` means "no such code here", not "the request failed".

    A ``$lookup`` for a code that is not in an edition is a 404 from a
    conformant FHIR server, which is an answer to "does this code exist"
    rather than a failure. Deliberately narrow: only a 4xx (never a 5xx,
    never a transport failure, never a protocol error) and only a 404 or an
    ``OperationOutcome`` that says not-found in as many words. Widening this
    to "any 4xx" would turn a malformed request - one the server rejected
    outright - into a false "code not found" answer; at catalogue scale
    (``sweep.py``'s own caller) that reads as thousands of plausible-looking
    absences instead of the one broken request that caused them.

    Promoted out of ``sweep.py`` (FR-52's second pass) so a second,
    interactive caller - the P1 API's single-code lookup route, FR-26 - can
    ask the same question without a second, independently-written answer to
    it (FR-74/ADR-0001: one implementation, never two that could drift).
    """
    if not isinstance(exc, TerminologyStatusError):
        return False
    if not 400 <= exc.status_code < 500:
        return False
    return exc.status_code == 404 or any(
        issue.code in NOT_FOUND_ISSUE_CODES for issue in exc.issues
    )
