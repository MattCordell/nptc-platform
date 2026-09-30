"""The FR-53 terminology client's exception hierarchy.

One base, so a caller implementing FR-54's graceful degradation can catch a
single type. ``retryable`` classifies every failure so a caller (the P3
validation sweep, the P1 API) can mark a run incomplete and retry the transient
half without re-deriving that from a status code at each call site.

The FR-54 policy (incomplete runs, dated cached results, browsing and editing
unaffected by an outage) is the caller's. This package's obligation is to fail
loudly and classifiably, never to return a default-valued result on failure.
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

    Carries the parsed ``OperationOutcome`` issues when the body served one,
    the usual shape of a 4xx from a FHIR server, rather than a separate class.
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
    ``resourceType``, and one carrying a SNOMED CT identifier as a JSON number
    rather than a string (FR-06 at the wire boundary).
    """


class TerminologyOutcomeError(TerminologyProtocolError):
    """A 2xx response whose body is an ``OperationOutcome``.

    Distinct from ``TerminologyStatusError`` because parsed leniently as the
    expected resource it would yield an empty ``Expansion``, reading as "nothing
    matched" instead of "the server refused this request" (FR-54).
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

    A ``$lookup`` for a code not in an edition is a 404 from a conformant FHIR
    server: an answer, not a failure. Deliberately narrow: only a 4xx (never a
    5xx, transport or protocol error), and only a 404 or an ``OperationOutcome``
    that says not-found. Widening to any 4xx would turn a request the server
    rejected outright into a false "code not found", which at catalogue scale
    reads as thousands of plausible absences instead of the one broken request.

    Shared by ``sweep.py`` and the P1 API's single-code lookup route (FR-26), so
    there is one answer to the question (FR-74, ADR-0001).
    """
    if not isinstance(exc, TerminologyStatusError):
        return False
    if not 400 <= exc.status_code < 500:
        return False
    return exc.status_code == 404 or any(
        issue.code in NOT_FOUND_ISSUE_CODES for issue in exc.issues
    )
