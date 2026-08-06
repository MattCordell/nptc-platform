"""The Ontoserver (FHIR R4 terminology server) implementation of ``TerminologyClient``.

Satisfies FR-53 against Ontoserver or any spec-conformant FHIR terminology
server - nothing here is Ontoserver-specific, so repointing at NCTS
production or a self-hosted instance is a ``TerminologyConfig`` change, not a
code change (PRD Section 15.2's accepted risk).

The transport is injectable (``transport=httpx.MockTransport(...)``), not the
whole ``httpx.Client``: a test that swaps the transport still exercises the
real base-URL, header and query-string construction, which is exactly the
code most worth testing. ``httpx.MockTransport`` is built into httpx, so no
additional mocking dependency exists in this workspace, and it opens no
socket - this client can therefore run inside the ``transform-offline`` CI
job's egress block (NFR-37).
"""

from __future__ import annotations

import datetime
import email.utils
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import httpx

from nptc_shared import __version__
from nptc_shared.terminology import fhir
from nptc_shared.terminology.config import TerminologyConfig
from nptc_shared.terminology.errors import (
    OperationOutcomeIssue,
    TerminologyProtocolError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)
from nptc_shared.terminology.models import (
    Edition,
    Expansion,
    LookupResult,
    Operation,
    SubsumptionOutcome,
    ValidationResult,
)
from nptc_shared.terminology.snomed import implicit_value_set_url

if TYPE_CHECKING:
    from nptc_shared.terminology.client import TerminologyClient

#: Statuses where a ``Retry-After`` header, if present, is honoured ahead of
#: the exponential backoff schedule (FR-52 item 4 names 429 explicitly; 503
#: is the other status HTTP itself defines ``Retry-After`` for).
_RETRY_AFTER_STATUSES = frozenset({429, 503})

#: Above this many characters of path + query string, ``expand`` switches
#: from GET to POST - a 200-500 code FR-52 chunk's ECL is comfortably past
#: what a typical reverse proxy allows in a request line.
_MAX_GET_URL_LENGTH = 4096

_POST_PARAM_VALUE_KEY: dict[str, str] = {
    "url": "valueUri",
    "count": "valueInteger",
    "offset": "valueInteger",
    "includeDesignations": "valueBoolean",
    "activeOnly": "valueBoolean",
    "displayLanguage": "valueCode",
}


class OntoserverClient:
    """FHIR R4 terminology client (FR-53)."""

    def __init__(
        self,
        config: TerminologyConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config or TerminologyConfig()
        self._sleep = sleep
        headers = {
            "Accept": "application/fhir+json",
            "User-Agent": f"nptc-platform/{__version__}",
        }
        if self._config.bearer_token:
            headers["Authorization"] = f"Bearer {self._config.bearer_token}"
        self._client = httpx.Client(
            base_url=self._config.base_url,
            timeout=httpx.Timeout(self._config.timeout_seconds),
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OntoserverClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- TerminologyClient ------------------------------------------------

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        params: dict[str, str] = {
            "url": implicit_value_set_url(ecl, edition),
            "offset": str(offset),
        }
        if count is not None:
            params["count"] = str(count)
        if include_designations:
            params["includeDesignations"] = "true"
        if display_language is not None:
            params["displayLanguage"] = display_language
        if active_only is not None:
            params["activeOnly"] = "true" if active_only else "false"

        query_length = len(f"{Operation.EXPAND.value}?{httpx.QueryParams(params)}")
        if query_length <= _MAX_GET_URL_LENGTH:
            body = self._request(Operation.EXPAND, method="GET", params=params)
        else:
            body = self._request(
                Operation.EXPAND, method="POST", json_body=_parameters_body(params)
            )
        return fhir.parse_expansion(body)

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> LookupResult:
        query = httpx.QueryParams(
            {"system": edition.system, "code": code, "version": edition.system_version_uri}
        )
        if display_language is not None:
            query = query.set("displayLanguage", display_language)
        for prop in properties:
            query = query.add("property", prop)
        body = self._request(Operation.LOOKUP, method="GET", params=query)
        return fhir.parse_lookup(body, code=code, system=edition.system)

    def subsumes(self, code_a: str, code_b: str, *, edition: Edition) -> SubsumptionOutcome:
        params = {
            "system": edition.system,
            "codeA": code_a,
            "codeB": code_b,
            "version": edition.system_version_uri,
        }
        body = self._request(Operation.SUBSUMES, method="GET", params=params)
        return fhir.parse_subsumes(body)

    def validate_code(
        self,
        code: str,
        *,
        edition: Edition,
        display: str | None = None,
        value_set_url: str | None = None,
    ) -> ValidationResult:
        if value_set_url is not None:
            operation = Operation.VALUE_SET_VALIDATE_CODE
            params = {
                "url": value_set_url,
                "system": edition.system,
                "code": code,
                "system-version": edition.system_version_uri,
            }
        else:
            operation = Operation.CODE_SYSTEM_VALIDATE_CODE
            params = {"url": edition.system, "code": code, "version": edition.system_version_uri}
        if display is not None:
            params["display"] = display
        body = self._request(operation, method="GET", params=params)
        return fhir.parse_validate_code(body, code=code)

    # -- transport ---------------------------------------------------------

    def _request(
        self,
        operation: Operation,
        *,
        method: str,
        params: Mapping[str, str] | httpx.QueryParams | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        request_headers = (
            {"Content-Type": "application/fhir+json"} if json_body is not None else None
        )
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method, operation.value, params=params, json=json_body, headers=request_headers
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._config.max_retries:
                    raise TerminologyTimeoutError(
                        f"{operation.value} timed out after {attempt + 1} attempt(s)",
                        operation=operation,
                    ) from exc
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            except httpx.TransportError as exc:
                if attempt >= self._config.max_retries:
                    raise TerminologyTransportError(
                        f"{operation.value} failed: {exc}", operation=operation
                    ) from exc
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            if response.status_code in _RETRY_AFTER_STATUSES:
                if attempt >= self._config.max_retries:
                    raise TerminologyRateLimitError(
                        f"{operation.value} returned {response.status_code} after "
                        f"{attempt + 1} attempt(s)",
                        operation=operation,
                        status_code=response.status_code,
                        issues=_issues_from_response(response),
                        retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
                    )
                delay = _retry_after_seconds(response.headers.get("Retry-After"))
                if delay is None:
                    delay = self._backoff(attempt)
                self._sleep(min(delay, self._config.max_backoff_seconds))
                attempt += 1
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self._config.max_retries:
                    raise TerminologyStatusError(
                        f"{operation.value} returned {response.status_code} after "
                        f"{attempt + 1} attempt(s)",
                        operation=operation,
                        status_code=response.status_code,
                        issues=_issues_from_response(response),
                    )
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            if response.status_code >= 400:
                # 4xx never retries - retrying a request the server has
                # already rejected as malformed only adds load to a shared
                # server for no chance of a different outcome.
                raise TerminologyStatusError(
                    f"{operation.value} returned {response.status_code}",
                    operation=operation,
                    status_code=response.status_code,
                    issues=_issues_from_response(response),
                )

            return fhir.parse_response_body(_decode_json(response, operation), operation=operation)

    def _backoff(self, attempt: int) -> float:
        # 2.0 (a float), not 2 (an int): int.__pow__ types its result as Any
        # for a non-literal exponent (a negative int exponent would produce a
        # float), which would make this whole expression Any under mypy strict.
        return min(
            self._config.backoff_base_seconds * (2.0**attempt),
            self._config.max_backoff_seconds,
        )


def _parameters_body(params: Mapping[str, str]) -> dict[str, object]:
    """Builds the ``Parameters`` resource for a POST ``$expand`` (used past
    ``_MAX_GET_URL_LENGTH``), typing each parameter as its FHIR value[x]."""
    parameter = []
    for name, value in params.items():
        key = _POST_PARAM_VALUE_KEY.get(name, "valueString")
        typed_value: object = value
        if key == "valueInteger":
            typed_value = int(value)
        elif key == "valueBoolean":
            typed_value = value == "true"
        parameter.append({"name": name, key: typed_value})
    return {"resourceType": "Parameters", "parameter": parameter}


def _decode_json(response: httpx.Response, operation: Operation) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise TerminologyProtocolError(
            f"{operation.value}: response body was not valid JSON", operation=operation
        ) from exc


def _issues_from_response(response: httpx.Response) -> tuple[OperationOutcomeIssue, ...]:
    """Best-effort ``OperationOutcome`` extraction for an error response - a
    server is not obliged to return one, so any failure here is swallowed
    and yields no issues rather than masking the original status error."""
    try:
        body = response.json()
    except ValueError:
        return ()
    if not isinstance(body, Mapping) or body.get("resourceType") != "OperationOutcome":
        return ()
    try:
        return fhir.parse_operation_outcome(fhir.as_mapping(body, context="OperationOutcome"))
    except TerminologyProtocolError:
        return ()


def _retry_after_seconds(header: str | None) -> float | None:
    """Parses a ``Retry-After`` header in either RFC 7231 form: delta-seconds
    or an HTTP-date. Returns ``None`` if absent or unparseable."""
    if header is None:
        return None
    text = header.strip()
    if text.isdigit():
        return float(text)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    delta = (parsed - datetime.datetime.now(datetime.UTC)).total_seconds()
    return max(delta, 0.0)


if TYPE_CHECKING:

    def _conforms(client: OntoserverClient) -> TerminologyClient:
        """Compile-time proof of Protocol conformance. mypy's ``files``
        setting covers ``shared/src`` but not ``shared/tests``, so this has
        to live in source to be checked at all."""
        return client
