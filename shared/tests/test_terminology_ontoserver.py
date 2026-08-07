"""Tests for OntoserverClient's HTTP-specific behaviour: URL/request
construction, batching, retry/backoff and error mapping (FR-52, FR-84,
NFR-38.13's transport-level rehearsal).

Every test injects an ``httpx.MockTransport`` (never opens a socket, NFR-37)
and a recording ``sleep`` callable, so retry/backoff schedules are asserted
directly rather than by spending real wall-clock time.
"""

from __future__ import annotations

import datetime
import email.utils
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from nptc_shared.terminology.config import TerminologyConfig
from nptc_shared.terminology.errors import (
    TerminologyOutcomeError,
    TerminologyProtocolError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)
from nptc_shared.terminology.models import PROCEDURE_ROOT_CODE, SNOMED_CT_AU
from nptc_shared.terminology.ontoserver import OntoserverClient
from nptc_shared.terminology.snomed import ecl_set_of, implicit_value_set_url

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "terminology"


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float] | None = None,
    max_retries: int = 3,
    bearer_token: str | None = None,
) -> OntoserverClient:
    config = TerminologyConfig(
        base_url="https://tx.example.test/fhir",
        max_retries=max_retries,
        bearer_token=bearer_token,
        backoff_base_seconds=0.5,
        max_backoff_seconds=30.0,
    )
    sink = sleeps if sleeps is not None else []
    return OntoserverClient(config, transport=httpx.MockTransport(handler), sleep=sink.append)


def _expansion_body(*, contains: list[dict[str, object]] | None = None) -> dict[str, object]:
    items = contains or []
    return {
        "resourceType": "ValueSet",
        "expansion": {"total": len(items), "offset": 0, "parameter": [], "contains": items},
    }


def _lookup_body(*, display: str = "Acanthamoeba culture") -> dict[str, object]:
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "display", "valueString": display}],
    }


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# -- URL and request construction -------------------------------------------


def test_expand_hits_the_fhir_base_path_not_dropped() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert captured["path"] == "/fhir/ValueSet/$expand"


def test_expand_url_parameter_round_trips_to_the_implicit_value_set_url() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_param"] = request.url.params.get("url")
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("<<71388002", edition=SNOMED_CT_AU)
    assert captured["url_param"] == implicit_value_set_url("<<71388002", SNOMED_CT_AU)


@pytest.mark.req("FR-97")
def test_expand_sends_display_language_on_the_query_string_when_given() -> None:
    """The contract suite cannot distinguish "sent and honoured" from "not
    sent" - both implementations are seeded from the same canned body
    regardless of what was asked for. This is the assertion that
    ``display_language`` actually reaches the wire, which is what FR-97's
    reconciliation and FR-82's preferred-term comparison both depend on."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["display_language"] = request.url.params.get("displayLanguage")
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand(
        "122192001",
        edition=SNOMED_CT_AU,
        include_designations=True,
        display_language="en-x-sctlang-32570271-00003610-6",
    )
    assert captured["display_language"] == "en-x-sctlang-32570271-00003610-6"


@pytest.mark.req("FR-97")
def test_expand_omits_display_language_when_not_given() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["display_language"] = request.url.params.get("displayLanguage")
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert captured["display_language"] is None


def test_pinned_edition_includes_the_version_segment() -> None:
    pinned = SNOMED_CT_AU.pinned_to("20260531")
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_param"] = request.url.params.get("url")
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("122192001", edition=pinned)
    assert captured["url_param"] is not None
    assert "/version/20260531?fhir_vs=" in captured["url_param"]


def test_lookup_sends_the_full_version_uri_not_a_bare_effective_time() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["version"] = request.url.params.get("version")
        return httpx.Response(200, json=_lookup_body())

    client = _client(handler)
    client.lookup("122192001", edition=SNOMED_CT_AU)
    assert captured["version"] == SNOMED_CT_AU.system_version_uri
    assert captured["version"] is not None
    assert captured["version"].startswith("http://snomed.info/sct/")


def test_no_authorization_header_when_anonymous() -> None:
    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert "authorization" not in captured["headers"]
    assert captured["headers"]["accept"] == "application/fhir+json"


def test_authorization_header_present_when_token_configured() -> None:
    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler, bearer_token="s3cr3tTOKEN")
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert captured["headers"]["authorization"] == "Bearer s3cr3tTOKEN"


# -- batching (FR-52, FR-84, NFR-38.13's transport-level rehearsal) ----------


def _large_fr84_ecl() -> str:
    codes = [str(100000000 + i) for i in range(500)]
    return f"({ecl_set_of(codes)}) MINUS <<{PROCEDURE_ROOT_CODE}"


def test_expanding_a_five_hundred_code_chunk_issues_exactly_one_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand(_large_fr84_ecl(), edition=SNOMED_CT_AU)
    assert len(calls) == 1


def test_expand_uses_get_for_a_short_ecl() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert captured["method"] == "GET"


def test_expand_switches_to_post_past_the_get_url_budget() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(200, json=_expansion_body())

    client = _client(handler)
    client.expand(_large_fr84_ecl(), edition=SNOMED_CT_AU)
    assert captured["method"] == "POST"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["resourceType"] == "Parameters"
    url_param = next(p for p in body["parameter"] if p["name"] == "url")
    assert "valueUri" in url_param


# -- retry and backoff (FR-52 item 4) ---------------------------------------


def test_429_with_retry_after_delta_seconds_then_success() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=_expansion_body())

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps)
    result = client.expand("122192001", edition=SNOMED_CT_AU)
    assert sleeps == [2.0]
    assert len(calls) == 2
    assert result.total == 0


def test_429_with_retry_after_http_date_then_success() -> None:
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=3)
    header_value = email.utils.format_datetime(future, usegmt=True)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": header_value})
        return httpx.Response(200, json=_expansion_body())

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 4.0


def test_429_exhaustion_raises_rate_limit_error_with_the_exponential_schedule() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429)

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps, max_retries=3)
    with pytest.raises(TerminologyRateLimitError):
        client.expand("122192001", edition=SNOMED_CT_AU)
    assert sleeps == [0.5, 1.0, 2.0]
    assert len(calls) == 4


def test_retry_after_far_in_the_future_is_capped_at_max_backoff() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "999999"})
        return httpx.Response(200, json=_expansion_body())

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert sleeps == [30.0]


def test_503_is_retried_then_succeeds() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_expansion_body())

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert sleeps == [0.5]


def test_500_is_retried_then_raises_a_retryable_status_error() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps, max_retries=2)
    with pytest.raises(TerminologyStatusError) as exc_info:
        client.expand("122192001", edition=SNOMED_CT_AU)
    assert exc_info.value.status_code == 500
    assert exc_info.value.retryable is True
    assert sleeps == [0.5, 1.0]
    assert len(calls) == 3


def test_400_is_not_retried_and_carries_operation_outcome_issues() -> None:
    body = _load_fixture("operation-outcome-invalid-ecl.json")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json=body)

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps)
    with pytest.raises(TerminologyStatusError) as exc_info:
        client.expand("this is not valid ecl (((", edition=SNOMED_CT_AU)
    assert len(calls) == 1
    assert sleeps == []
    assert exc_info.value.retryable is False
    assert exc_info.value.issues[0].diagnostics is not None


# -- transport and protocol error mapping -----------------------------------


def test_timeout_raises_terminology_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated", request=request)

    sleeps: list[float] = []
    client = _client(handler, sleeps=sleeps, max_retries=1)
    with pytest.raises(TerminologyTimeoutError):
        client.expand("122192001", edition=SNOMED_CT_AU)
    assert sleeps == [0.5]


def test_connect_error_raises_terminology_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    client = _client(handler, max_retries=0)
    with pytest.raises(TerminologyTransportError):
        client.expand("122192001", edition=SNOMED_CT_AU)


def test_a_200_operation_outcome_raises_rather_than_an_empty_expansion() -> None:
    """FR-54's hazard: a refusal must never be parsed as "nothing matched"."""
    body = _load_fixture("operation-outcome-invalid-ecl.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = _client(handler)
    with pytest.raises(TerminologyOutcomeError):
        client.expand("122192001", edition=SNOMED_CT_AU)


def test_a_200_response_that_is_not_valid_json_raises_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client(handler)
    with pytest.raises(TerminologyProtocolError):
        client.expand("122192001", edition=SNOMED_CT_AU)


def test_a_code_returned_as_a_json_number_is_rejected_not_coerced() -> None:
    """FR-06's chokepoint at the wire boundary."""
    body = _expansion_body(contains=[{"system": "http://snomed.info/sct", "code": 122192001}])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = _client(handler)
    with pytest.raises(TerminologyProtocolError):
        client.expand("122192001", edition=SNOMED_CT_AU)


def test_a_lookup_property_value_arriving_as_a_json_number_is_rejected_not_coerced() -> None:
    """The same FR-06 chokepoint, for a $lookup property (e.g. a SAME_AS
    historical-association target sent as valueDecimal instead of valueCode)."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "property",
                "part": [
                    {"name": "code", "valueCode": "SAME_AS"},
                    {"name": "value", "valueDecimal": 122192001},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = _client(handler)
    with pytest.raises(TerminologyProtocolError):
        client.lookup("122192001", edition=SNOMED_CT_AU)


def test_a_valueset_with_no_expansion_element_raises_rather_than_an_empty_result() -> None:
    """A ValueSet *definition* (no expansion) must never parse as a clean,
    empty Expansion - that is FR-54's hazard: a response the server never
    actually expanded reading as "nothing matched"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"resourceType": "ValueSet", "url": "http://snomed.info/sct"}
        )

    client = _client(handler)
    with pytest.raises(TerminologyProtocolError, match="expansion"):
        client.expand("122192001", edition=SNOMED_CT_AU)


def test_value_set_validate_code_sends_systemversion_not_hyphenated() -> None:
    """R4 names this parameter ``systemVersion`` on ValueSet/$validate-code -
    ``system-version`` (the $expand-implicit-URL form) is a different, and
    wrong, parameter name that a real server silently ignores."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["systemVersion"] = request.url.params.get("systemVersion")
        captured["hyphenated"] = request.url.params.get("system-version")
        return httpx.Response(
            200,
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "result", "valueBoolean": True}],
            },
        )

    client = _client(handler)
    client.validate_code("122192001", edition=SNOMED_CT_AU, value_set_url="http://example.test/vs")
    assert captured["systemVersion"] == SNOMED_CT_AU.system_version_uri
    assert captured["hyphenated"] is None


def test_configured_token_never_appears_in_a_raised_exceptions_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler, max_retries=0, bearer_token="s3cr3tTOKEN")
    with pytest.raises(TerminologyStatusError) as exc_info:
        client.expand("122192001", edition=SNOMED_CT_AU)
    assert "s3cr3tTOKEN" not in str(exc_info.value)


# -- lifecycle ----------------------------------------------------------


def test_close_can_be_called_more_than_once() -> None:
    client = _client(lambda request: httpx.Response(200, json=_expansion_body()))
    client.close()
    client.close()


def test_context_manager_closes_the_client() -> None:
    with _client(lambda request: httpx.Response(200, json=_expansion_body())) as client:
        client.expand("122192001", edition=SNOMED_CT_AU)
    client.close()


@pytest.mark.req("NFR-37")
def test_a_client_with_no_injected_transport_cannot_reach_the_real_network() -> None:
    """Confirms the autouse guard in conftest.py actually intercepts httpx's
    real HTTPTransport - a local, immediate stand-in for what ci.yml's
    transform-offline job proves after the fact by blocking egress."""
    client = OntoserverClient(
        TerminologyConfig(base_url="https://tx.example.test/fhir", max_retries=0)
    )
    with pytest.raises(AssertionError, match="NFR-37"):
        client.expand("122192001", edition=SNOMED_CT_AU)
