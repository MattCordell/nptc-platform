"""HTTP tests for `GET /terminology/procedures`, the code picker's scoped term search (FR-26).

Same shape as `test_api_terminology.py`: the real `create_app()` with the stub terminology
client `api_app_support.build_api_test_app` installs (NFR-37). The route's reason to exist is its
scope, so most tests here are about what it refuses to offer.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.auth.permissions import Role
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    Operation,
    StubConcept,
    TerminologyConfigError,
    TerminologyError,
    TerminologyOutcomeError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)
from nptc_shared.terminology.stub import StubNotSeededError


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

_PROCEDURE_ROOT = "71388002"
_MICROSCOPY = "391483001"
_CULTURE = "122192001"
_INACTIVE_PROCEDURE = "243120004"
_OUTSIDE_PROCEDURE = "123037004"
_SEARCH_ECL = "<71388002"
_AU_PREFERRED_TERM = "Acid fast bacilli microscopy"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _seed_procedures(api: ApiTestApp) -> None:
    """The root, two active procedures, an inactive one, and a body structure outside the
    hierarchy: the cases the scope and `active_only` must each exclude."""
    for code, fsn, term, parents, active in (
        (_PROCEDURE_ROOT, "Procedure (procedure)", "Procedure", (), True),
        (
            _MICROSCOPY,
            "Microscopy (acid fast bacilli) (procedure)",
            _AU_PREFERRED_TERM,
            (_PROCEDURE_ROOT,),
            True,
        ),
        (_CULTURE, "Culture (procedure)", "Acanthamoeba culture", (_PROCEDURE_ROOT,), True),
        (_INACTIVE_PROCEDURE, "Old (procedure)", "Retired microscopy", (_PROCEDURE_ROOT,), False),
        (_OUTSIDE_PROCEDURE, "Body structure (body structure)", "Microscopy site", (), True),
    ):
        api.terminology.add_concept(
            StubConcept(
                code=code,
                fsn=fsn,
                preferred_terms={AU_LANGUAGE_TAG: term},
                parents=parents,
                active=active,
            )
        )


def _search(api: ApiTestApp, token: str | None, q: str | None, **params: Any) -> Any:
    query: dict[str, Any] = dict(params)
    if q is not None:
        query["q"] = q
    return api.get("/terminology/procedures", token=token, params=query)


def _editor_token(api: ApiTestApp, subject: str) -> str:
    return api.exact_role_token(subject=subject, role=Role.PROVISIONAL)


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_search_by_term_offers_only_active_descendants_of_procedure(api: ApiTestApp) -> None:
    _seed_procedures(api)

    response = _search(api, _editor_token(api, "sub-search-scope"), "microscopy")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == [{"code": _MICROSCOPY, "au_preferred_term": _AU_PREFERRED_TERM}]
    assert body["total"] == 1
    assert f'"code":"{_MICROSCOPY}"' in response.text.replace(" ", "")
    assert body["label_provenance"] == {
        "au_preferred_term": {"designation": "au_preferred_term", "semantic_tag": "not_applicable"}
    }


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_search_pins_the_scope_and_edition_in_code_with_one_upstream_request(
    api: ApiTestApp,
) -> None:
    """The caller supplies `q` and `count` only. Scope and display language come from the
    server's own code, and the route makes exactly one `$expand`."""
    _seed_procedures(api)

    _search(api, _editor_token(api, "sub-search-pinned"), "culture", count=7)

    assert len(api.terminology.requests) == 1
    request = api.terminology.requests[0]
    assert request.operation == Operation.EXPAND
    assert request.detail == _SEARCH_ECL
    assert request.count == 7
    assert request.display_language == AU_LANGUAGE_TAG


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_search_never_offers_the_procedure_root_itself(api: ApiTestApp) -> None:
    _seed_procedures(api)

    response = _search(api, _editor_token(api, "sub-search-root"), "procedure")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_search_with_no_match_is_an_empty_200_not_an_error(api: ApiTestApp) -> None:
    _seed_procedures(api)

    response = _search(api, _editor_token(api, "sub-search-empty"), "zzzz")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_search_by_code_returns_that_concept_when_it_is_a_procedure(api: ApiTestApp) -> None:
    _seed_procedures(api)

    response = _search(api, _editor_token(api, "sub-search-code"), f" {_MICROSCOPY} ")

    assert response.status_code == 200, response.text
    assert [item["code"] for item in response.json()["items"]] == [_MICROSCOPY]
    assert [r.detail for r in api.terminology.requests] == [f"{_SEARCH_ECL} AND {_MICROSCOPY}"]


@pytest.mark.req("FR-84")
@pytest.mark.integration
@pytest.mark.parametrize(
    "code",
    [_OUTSIDE_PROCEDURE, _PROCEDURE_ROOT, _INACTIVE_PROCEDURE],
    ids=["outside-procedure", "procedure-root", "inactive"],
)
def test_search_by_code_refuses_what_lookup_would_accept(api: ApiTestApp, code: str) -> None:
    """`$lookup` answers 200 for each of these. The picker must not offer them, because the
    write path never checks FR-84."""
    _seed_procedures(api)
    token = _editor_token(api, f"sub-search-refused-{code}")

    assert api.get(f"/terminology/concepts/{code}", token=token).status_code == 200
    response = _search(api, token, code)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.req("FR-84")
@pytest.mark.integration
@pytest.mark.parametrize(
    "q",
    [f"{_MICROSCOPY} OR {_OUTSIDE_PROCEDURE}", f"<<{_PROCEDURE_ROOT}", f"* MINUS {_MICROSCOPY}"],
    ids=["or", "descendant-operator", "minus"],
)
def test_search_text_that_looks_like_ecl_is_a_filter_and_never_widens_the_scope(
    api: ApiTestApp, q: str
) -> None:
    """Only a bare 6 to 18 digit code is interpolated into the ECL. Anything else goes to the
    server as `filter`, so a caller cannot smuggle an operator into the scope."""
    _seed_procedures(api)

    response = _search(api, _editor_token(api, "sub-search-ecl-text"), q)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert [r.detail for r in api.terminology.requests] == [_SEARCH_ECL]


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_search_by_a_code_with_a_bad_check_digit_is_empty_with_no_upstream_request(
    api: ApiTestApp,
) -> None:
    """An editor typing a code passes through invalid prefixes, so this is not a 422."""
    response = _search(api, _editor_token(api, "sub-search-bad-digit"), "391483009")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert api.terminology.requests == ()


@pytest.mark.req("FR-26")
@pytest.mark.integration
@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"q": ""}, id="empty-q"),
        pytest.param({"q": "   "}, id="blank-q"),
        pytest.param({"q": None}, id="missing-q"),
        pytest.param({"q": "x", "count": 0}, id="count-zero"),
        pytest.param({"q": "x", "count": 51}, id="count-over-cap"),
    ],
)
def test_search_rejects_unusable_parameters_with_no_upstream_request(
    api: ApiTestApp, params: dict[str, Any]
) -> None:
    response = _search(api, _editor_token(api, "sub-search-422"), **params)

    assert response.status_code == 422, response.text
    assert api.terminology.requests == ()


@pytest.mark.req("FR-54")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "status"),
    [
        pytest.param(TerminologyTransportError("connection refused"), 503, id="transport"),
        pytest.param(TerminologyTimeoutError("timed out"), 503, id="timeout"),
        pytest.param(TerminologyOutcomeError("refused"), 502, id="operation-outcome"),
        pytest.param(TerminologyConfigError("bad config"), 500, id="config"),
        pytest.param(
            TerminologyStatusError("not found", status_code=404), 502, id="404-is-not-absence"
        ),
        pytest.param(StubNotSeededError("not seeded"), 502, id="unseeded-stub"),
    ],
)
def test_search_maps_a_terminology_failure_and_never_serves_an_empty_page(
    api: ApiTestApp, error: TerminologyError, status: int
) -> None:
    api.terminology.seed_error(Operation.EXPAND, error, key=_SEARCH_ECL)

    response = _search(api, _editor_token(api, f"sub-search-{type(error).__name__}"), "x")

    assert response.status_code == status, response.text
    assert set(response.json()) == {"detail"}


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_search_persisted_rate_limit_is_503_with_retry_after(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.EXPAND,
        TerminologyRateLimitError("rate limited", status_code=429, retry_after=30.0),
        key=_SEARCH_ECL,
    )

    response = _search(api, _editor_token(api, "sub-search-rate-limit"), "x")

    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == "30"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_search_no_credential_is_401_with_no_upstream_request(api: ApiTestApp) -> None:
    response = _search(api, None, "x")

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert api.terminology.requests == ()


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_search_without_registry_read_is_403_with_no_upstream_request(api: ApiTestApp) -> None:
    token = api.exact_role_token(subject="sub-search-observer", role=Role.OBSERVER)

    response = _search(api, token, "x")

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert api.terminology.requests == ()
