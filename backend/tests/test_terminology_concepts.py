"""Unit tests for `nptc.terminology.concepts.resolve_concept` (issue #240,
FR-26).

No database, no HTTP, no Docker - a `StubTerminologyClient` is all
`resolve_concept` ever touches. `test_api_terminology.py` proves the HTTP
adapter (status codes, header mapping, authorisation) built on top of this;
this module proves the classification and field-derivation rules
themselves, one exception family at a time.
"""

from __future__ import annotations

import pytest

from nptc.terminology.concepts import resolve_concept
from nptc.terminology.errors import (
    ConceptNotFoundError,
    TerminologyUnavailableError,
    TerminologyUpstreamError,
)
from nptc_shared.sctid import InvalidSCTIDError
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    SNOMED_CT_AU,
    SNOMED_SYSTEM,
    Designation,
    LookupResult,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyOutcomeError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)

_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_AU_PREFERRED_TERM = "Acid fast bacilli microscopy"


def _client_with_concept(*, active: bool = True) -> StubTerminologyClient:
    client = StubTerminologyClient()
    client.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: _AU_PREFERRED_TERM},
            active=active,
        )
    )
    return client


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-82")
def test_resolves_fsn_with_tag_intact_and_au_preferred_term() -> None:
    """`fsn` and `au_preferred_term` are two different served values, and
    neither is derived from the other - a designation scan would silently
    disagree with `LookupResult.display` (see the module's own docstring)."""
    resolved = resolve_concept(_client_with_concept(), _CODE, edition=SNOMED_CT_AU)

    assert resolved.code == _CODE
    assert resolved.fsn == _FSN
    assert resolved.au_preferred_term == _AU_PREFERRED_TERM
    assert resolved.edition == "au"


@pytest.mark.req("FR-26")
def test_active_concept_is_active_true() -> None:
    resolved = resolve_concept(_client_with_concept(active=True), _CODE, edition=SNOMED_CT_AU)
    assert resolved.active is True


@pytest.mark.req("FR-26")
def test_inactive_concept_is_active_false() -> None:
    resolved = resolve_concept(_client_with_concept(active=False), _CODE, edition=SNOMED_CT_AU)
    assert resolved.active is False


@pytest.mark.req("FR-82")
def test_inactive_property_not_reported_is_active_none() -> None:
    """Hazard H-05: "not reported" must never collapse into "active" - see
    the module's own docstring. A raw `LookupResult` with no `inactive`
    property is what a non-conformant server that ignores the requested
    property would actually send."""
    client = StubTerminologyClient()
    client.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display=_AU_PREFERRED_TERM,
            designations=(
                Designation(value=_FSN, use_system=SNOMED_SYSTEM, use_code="900000000000003001"),
            ),
            properties=(),
        ),
    )

    resolved = resolve_concept(client, _CODE, edition=SNOMED_CT_AU)

    assert resolved.active is None


@pytest.mark.req("FR-06")
def test_malformed_code_raises_invalid_sctid_before_any_request() -> None:
    client = _client_with_concept()

    with pytest.raises(InvalidSCTIDError):
        resolve_concept(client, "not-a-code", edition=SNOMED_CT_AU)

    assert client.requests == ()


@pytest.mark.req("FR-06")
def test_check_digit_failure_raises_invalid_sctid_before_any_request() -> None:
    client = _client_with_concept()

    with pytest.raises(InvalidSCTIDError):
        resolve_concept(client, "391483009", edition=SNOMED_CT_AU)

    assert client.requests == ()


@pytest.mark.req("FR-26")
def test_code_not_on_server_raises_concept_not_found() -> None:
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("not found", status_code=404))

    with pytest.raises(ConceptNotFoundError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_timeout_raises_terminology_unavailable() -> None:
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))

    with pytest.raises(TerminologyUnavailableError) as excinfo:
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)
    assert excinfo.value.retry_after is None


@pytest.mark.req("FR-54")
def test_transport_failure_raises_terminology_unavailable() -> None:
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyTransportError("connection refused"))

    with pytest.raises(TerminologyUnavailableError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_persisted_rate_limit_carries_retry_after() -> None:
    client = StubTerminologyClient()
    client.seed_error(
        Operation.LOOKUP,
        TerminologyRateLimitError("rate limited", status_code=429, retry_after=30.0),
    )

    with pytest.raises(TerminologyUnavailableError) as excinfo:
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)
    assert excinfo.value.retry_after == 30.0


@pytest.mark.req("FR-54")
def test_other_5xx_raises_terminology_unavailable() -> None:
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("server error", status_code=500))

    with pytest.raises(TerminologyUnavailableError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_operation_outcome_body_raises_terminology_upstream() -> None:
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyOutcomeError("server refused the request"))

    with pytest.raises(TerminologyUpstreamError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_unclassified_4xx_raises_terminology_upstream_not_not_found() -> None:
    """A 4xx that is not an absence answer (no 404, no not-found
    `OperationOutcome` issue) must not be read as "code not found" - see
    `is_concept_absence`'s own deliberately narrow rule."""
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("bad request", status_code=400))

    with pytest.raises(TerminologyUpstreamError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_unseeded_stub_raises_terminology_upstream_not_not_found() -> None:
    """The landmine this module's own docstring names: `StubNotSeededError`
    is a bare `TerminologyError`, neither a status nor a transport failure
    - it must fall through to the catch-all, never read as "not found"."""
    client = StubTerminologyClient()  # nothing seeded at all

    with pytest.raises(TerminologyUpstreamError):
        resolve_concept(client, _CODE, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-52")
def test_resolve_concept_issues_exactly_one_request() -> None:
    client = _client_with_concept()

    resolve_concept(client, _CODE, edition=SNOMED_CT_AU)

    assert len(client.requests) == 1
    assert client.requests[0].operation == Operation.LOOKUP
