"""Direct tests for `nptc_shared.terminology.errors.is_concept_absence`
(issue #240 review).

`sweep.py` only ever exercised the `"not-found"` member of
`NOT_FOUND_ISSUE_CODES` before this helper was promoted out of it - this
module is `is_concept_absence`'s own table-driven test, so every member of
that set (and every reason a `TerminologyError` is *not* an absence answer)
has direct coverage at the one place both `sweep.py` and the FR-26 route
depend on.
"""

from __future__ import annotations

import pytest

from nptc_shared.terminology.errors import (
    NOT_FOUND_ISSUE_CODES,
    OperationOutcomeIssue,
    TerminologyOutcomeError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
    is_concept_absence,
)


def test_not_found_issue_codes_has_exactly_the_three_documented_members() -> None:
    """A positive control for the parametrised tests below: if this set
    ever grows or shrinks, the coverage claim above has to be re-checked."""
    assert frozenset({"not-found", "code-invalid", "invalid-code"}) == NOT_FOUND_ISSUE_CODES


@pytest.mark.parametrize("issue_code", sorted(NOT_FOUND_ISSUE_CODES))
def test_a_4xx_carrying_any_not_found_issue_code_is_an_absence(issue_code: str) -> None:
    exc = TerminologyStatusError(
        "refused",
        status_code=400,
        issues=(OperationOutcomeIssue(severity="error", code=issue_code),),
    )
    assert is_concept_absence(exc) is True


def test_a_bare_404_with_no_issues_is_an_absence() -> None:
    assert is_concept_absence(TerminologyStatusError("not found", status_code=404)) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
def test_a_4xx_with_no_matching_issue_code_is_not_an_absence(status_code: int) -> None:
    exc = TerminologyStatusError(
        "refused",
        status_code=status_code,
        issues=(OperationOutcomeIssue(severity="error", code="invalid"),),
    )
    assert is_concept_absence(exc) is False


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_a_5xx_is_never_an_absence_even_with_a_not_found_issue_code(status_code: int) -> None:
    """Deliberately narrow (the function's own docstring): a 5xx is a server
    fault, never an answer to "does this code exist", regardless of what its
    body happens to say."""
    exc = TerminologyStatusError(
        "server error",
        status_code=status_code,
        issues=(OperationOutcomeIssue(severity="error", code="not-found"),),
    )
    assert is_concept_absence(exc) is False


def test_a_transport_failure_is_not_an_absence() -> None:
    assert is_concept_absence(TerminologyTransportError("connection refused")) is False


def test_a_timeout_is_not_an_absence() -> None:
    assert is_concept_absence(TerminologyTimeoutError("timed out")) is False


def test_a_protocol_error_is_not_an_absence() -> None:
    """`TerminologyOutcomeError` is not a `TerminologyStatusError` at all -
    it is a 2xx response, so `is_concept_absence`'s `isinstance` guard must
    reject it outright rather than raising on a missing `status_code`."""
    assert is_concept_absence(TerminologyOutcomeError("server refused the request")) is False
