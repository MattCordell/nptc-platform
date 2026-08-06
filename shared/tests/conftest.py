"""Shared fixtures for shared/tests.

Two responsibilities: the FR-53 terminology contract suite's dual-
implementation ``client`` fixture (seeded from the same captured FHIR bodies
for both ``StubTerminologyClient`` and ``OntoserverClient``), and an autouse
NFR-37 guard that fails any test in this tree making a real HTTP request -
``ci.yml``'s ``transform-offline`` job proves the same thing with
``iptables``, but only in CI and only after the fact; this fails locally, at
the exact call, with a message naming the requirement.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from nptc_shared.terminology import fhir
from nptc_shared.terminology.client import TerminologyClient
from nptc_shared.terminology.config import TerminologyConfig
from nptc_shared.terminology.models import (
    SNOMED_CT_AU,
    SNOMED_CT_INTERNATIONAL,
    SNOMED_SYSTEM,
    Edition,
)
from nptc_shared.terminology.ontoserver import OntoserverClient
from nptc_shared.terminology.stub import StubTerminologyClient

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "terminology"

AU_VERSION_URI = "http://snomed.info/sct/32506021000036107/version/20260531"

# ECL strings used by test_terminology_contract.py - named here so an
# Exchange's key and the test's client.expand(...) argument can never drift
# apart from each other.
ECL_TWO_CODES = "122192001 OR 71388002"
ECL_FR84_CHECK = "(122192001 OR 71388002) MINUS <<71388002"
ECL_SINGLE_CONCEPT = "122192001"


@dataclass(frozen=True)
class Exchange:
    """One captured response and the request identity that selects it.

    ``key``'s shape depends on its first element (the operation): ``expand``
    keys are ``("expand", ecl, edition_label)``; ``lookup`` keys are
    ``("lookup", code, edition_label)``; ``subsumes`` keys are
    ``("subsumes", code_a, code_b, edition_label)``; ``validate_code`` keys
    are ``("validate_code", code, display, edition_label)``. Both the
    stub-seeding path and the ``MockTransport`` handler resolve a request to
    this same key - the stub from its own call arguments, the handler by
    decoding the request it was handed - so the same behaviour genuinely
    runs through the same identity for both implementations.
    """

    key: tuple[str | None, ...]
    fixture: str


EXCHANGES: tuple[Exchange, ...] = (
    Exchange(("expand", ECL_TWO_CODES, "au"), "expand-two-active-concepts.json"),
    Exchange(("expand", ECL_FR84_CHECK, "au"), "expand-empty.json"),
    Exchange(("expand", ECL_SINGLE_CONCEPT, "au"), "expand-with-designations.json"),
    Exchange(("lookup", "122192001", "au"), "lookup-active-concept.json"),
    Exchange(("lookup", "873871000168106", "au"), "lookup-inactive-duplicate-same-as.json"),
    Exchange(("subsumes", "71388002", "71388002", "au"), "subsumes-equivalent.json"),
    Exchange(("subsumes", "71388002", "122192001", "au"), "subsumes-subsumes.json"),
    Exchange(("subsumes", "122192001", "71388002", "au"), "subsumes-subsumed-by.json"),
    Exchange(("subsumes", "122192001", "243120004", "au"), "subsumes-not-subsumed.json"),
    Exchange(
        ("validate_code", "122192001", "Acanthamoeba culture", "au"), "validate-code-true.json"
    ),
    Exchange(
        ("validate_code", "122192001", "Acanthamoeba species culture", "au"),
        "validate-code-false-display-mismatch.json",
    ),
)


@pytest.fixture(scope="session")
def canned() -> Mapping[tuple[str | None, ...], Mapping[str, object]]:
    """Every captured fixture body, loaded once and keyed by request identity."""
    bodies: dict[tuple[str | None, ...], Mapping[str, object]] = {}
    for exchange in EXCHANGES:
        text = (FIXTURES_DIR / exchange.fixture).read_text(encoding="utf-8")
        bodies[exchange.key] = json.loads(text)
    return bodies


def _edition_for_label(label: str | None) -> Edition:
    if label == "int":
        return SNOMED_CT_INTERNATIONAL
    return SNOMED_CT_AU


_MODULE_ID_LABELS = {SNOMED_CT_AU.module_id: "au", SNOMED_CT_INTERNATIONAL.module_id: "int"}


def _label_from_version_uri(version: str | None) -> str | None:
    if version is None:
        return None
    for module_id, label in _MODULE_ID_LABELS.items():
        if module_id in version:
            return label
    return None


def _key_for_request(request: httpx.Request) -> tuple[str | None, ...]:
    """Reconstructs an ``Exchange`` key from a real outgoing request - the
    ``MockTransport`` handler's half of the shared request identity."""
    path = request.url.path
    params = request.url.params
    if path.endswith("$expand"):
        url_param = params.get("url", "")
        ecl = ""
        label = None
        if "?fhir_vs=ecl/" in url_param:
            base, _, encoded_ecl = url_param.partition("?fhir_vs=ecl/")
            ecl = unquote(encoded_ecl)
            label = _label_from_version_uri(base)
        return ("expand", ecl, label)
    if path.endswith("$lookup"):
        return ("lookup", params.get("code", ""), _label_from_version_uri(params.get("version")))
    if path.endswith("$subsumes"):
        return (
            "subsumes",
            params.get("codeA", ""),
            params.get("codeB", ""),
            _label_from_version_uri(params.get("version")),
        )
    if path.endswith("$validate-code"):
        version = params.get("version") or params.get("systemVersion")
        return (
            "validate_code",
            params.get("code", ""),
            params.get("display", ""),
            _label_from_version_uri(version),
        )
    return ("unknown",)


_NOT_FOUND_OUTCOME: dict[str, object] = {
    "resourceType": "OperationOutcome",
    "issue": [
        {
            "severity": "error",
            "code": "not-found",
            "diagnostics": "no canned contract-suite response matches this request",
        }
    ],
}


def _make_transport(
    canned_bodies: Mapping[tuple[str | None, ...], Mapping[str, object]],
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = canned_bodies.get(_key_for_request(request))
        if body is None:
            return httpx.Response(404, json=_NOT_FOUND_OUTCOME)
        return httpx.Response(200, json=dict(body))

    return httpx.MockTransport(handler)


def _seeded_stub(
    canned_bodies: Mapping[tuple[str | None, ...], Mapping[str, object]],
) -> StubTerminologyClient:
    """A stub seeded from the *same* captured bodies, parsed with the
    production ``fhir.py`` functions - a transport-less replay of one
    interpretation of the wire format, never a second one."""
    stub = StubTerminologyClient(resolved_version={"au": AU_VERSION_URI})
    for key, body in canned_bodies.items():
        operation = key[0]
        edition = _edition_for_label(key[-1])
        if operation == "expand":
            _, ecl, _label = key
            assert ecl is not None
            stub.seed_expansion(ecl, fhir.parse_expansion(body), edition=edition)
        elif operation == "lookup":
            _, code, _label = key
            assert code is not None
            stub.seed_lookup(
                code, fhir.parse_lookup(body, code=code, system=SNOMED_SYSTEM), edition=edition
            )
        elif operation == "subsumes":
            _, code_a, code_b, _label = key
            assert code_a is not None
            assert code_b is not None
            stub.seed_subsumes(code_a, code_b, fhir.parse_subsumes(body), edition=edition)
        elif operation == "validate_code":
            _, code, display, _label = key
            assert code is not None
            stub.seed_validate_code(
                code,
                fhir.parse_validate_code(body, code=code),
                display=display or None,
                edition=edition,
            )
    return stub


@pytest.fixture(params=("stub", "ontoserver"), ids=("stub", "ontoserver"))
def client(
    request: pytest.FixtureRequest, canned: Mapping[tuple[str | None, ...], Mapping[str, object]]
) -> Iterator[TerminologyClient]:
    """The FR-53 contract, once per implementation.

    Every test in ``test_terminology_contract.py`` runs against both values -
    the issue's acceptance criterion made mechanical: a behaviour one
    implementation has and the other lacks cannot pass.
    """
    if request.param == "stub":
        yield _seeded_stub(canned)
        return
    config = TerminologyConfig(base_url="https://tx.example.test/fhir", max_retries=0)
    ontoserver = OntoserverClient(
        config, transport=_make_transport(canned), sleep=lambda _seconds: None
    )
    try:
        yield ontoserver
    finally:
        ontoserver.close()


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-37: nothing under shared/tests may open a real socket.

    ``ci.yml``'s ``transform-offline`` job proves this with ``iptables``, but
    only in CI and only after the fact. This fails locally, at the exact
    call, with a message naming the requirement - which is what stops a
    network-dependent test being written in the first place.

    Covers httpx's sync transport (this workspace's only HTTP client, per
    ADR-0003), its async counterpart (nothing uses it today, but a future
    async terminology client passing this guard silently, caught only by
    CI's iptables job after the fact, would defeat the point of a local
    guard), and the socket layer itself, so the claim above is actually true
    rather than "no real *httpx* request".
    """

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shared/tests must not make a real HTTP request (NFR-37)")

    async def _refuse_async(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shared/tests must not make a real HTTP request (NFR-37)")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse_async)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
