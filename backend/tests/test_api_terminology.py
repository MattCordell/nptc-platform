"""HTTP tests for `nptc.api.routers.terminology` (issue #240, FR-26).

Follows `test_api_registry_properties.py`'s own precedent: the domain logic
already has its own unit tests (a follow-up to this module, over
`nptc.terminology.concepts.resolve_concept` directly); this module proves
the HTTP adapter - status codes, the `nptc.api.errors` mapping, and
`Permission.REGISTRY_READ` authorisation - against the real `create_app()`,
with `api.terminology` (a `StubTerminologyClient`, installed centrally by
`api_app_support.build_api_test_app`) standing in for the terminology
server (NFR-37).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked, revoke_all_roles_unchecked
from nptc.auth.permissions import Role
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    SNOMED_SYSTEM,
    Designation,
    LookupResult,
    Operation,
    StubConcept,
    TerminologyConfigError,
    TerminologyOutcomeError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)


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

#: A valid SCTID (Verhoeff-passing, used elsewhere as fixture data - see
#: `public_catalogue_support.ACTIVE_CODE`) whose FSN ends in a parenthesised
#: group, so tests can assert the semantic tag survives (FR-82).
_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_AU_PREFERRED_TERM = "Acid fast bacilli microscopy"
_RESOLVED_VERSION = "http://snomed.info/sct/32506021000036107/version/20260531"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _role_token(api: ApiTestApp, *, subject: str, role: Role) -> str:
    """Resolves `subject` to exactly `role` - see
    `test_api_registry_properties.py`'s identically-named helper for why
    the default Provisional grant has to be cleared first."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    revoke_all_roles_unchecked(api.session, target_user_id=user.id, audit=AuditContext.system())
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    return api.token(subject=subject)


def _seed_concept(api: ApiTestApp, *, code: str = _CODE, active: bool = True) -> None:
    api.terminology.add_concept(
        StubConcept(
            code=code,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: _AU_PREFERRED_TERM},
            active=active,
        )
    )


def _get(api: ApiTestApp, token: str | None, code: str = _CODE) -> Any:
    return api.get(f"/terminology/concepts/{code}", token=token)


# --- happy paths -------------------------------------------------------


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_lookup_resolves_fsn_with_tag_and_au_preferred_term(api: ApiTestApp) -> None:
    _seed_concept(api)
    token = _role_token(api, subject="sub-lookup-happy", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == _CODE
    assert body["fsn"] == _FSN
    assert body["au_preferred_term"] == _AU_PREFERRED_TERM
    assert body["au_preferred_term"] != body["fsn"]
    assert body["active"] is True
    assert body["edition"] == "au"


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_lookup_18_digit_code_round_trips_exactly_as_a_string(api: ApiTestApp) -> None:
    """The failure mode FR-06 exists to eliminate: an SCTID long enough to
    lose precision or leading-zero semantics if it were ever coerced to a
    number (`nptc_shared.sctid`'s own module docstring) - a 6-18 digit code
    at the long end of that range, asserted against the raw response text,
    not just the parsed JSON `code` field `isinstance` would already accept
    silently if it were a number by the time this test saw it."""
    code = "123456789012345605"
    _seed_concept(api, code=code)
    token = _role_token(api, subject="sub-lookup-18-digit", role=Role.PROVISIONAL)

    response = _get(api, token, code=code)

    assert response.status_code == 200, response.text
    assert f'"code":"{code}"' in response.text.replace(" ", "")
    assert response.json()["code"] == code


@pytest.mark.req("FR-48")
@pytest.mark.integration
def test_lookup_response_carries_the_resolved_version_the_server_reported(
    api: ApiTestApp,
) -> None:
    """FR-48: which release actually answered. `StubConcept`/`add_concept`
    never populates `resolved_version` (there is no argument for it), so
    this seeds a raw `LookupResult` directly - the only way to reach a
    non-null value against the stub, and proof the field is actually wired
    through rather than always serving `null` regardless of what the
    "client" returned."""
    api.terminology.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display=_AU_PREFERRED_TERM,
            resolved_version=_RESOLVED_VERSION,
            designations=(
                Designation(value=_FSN, use_system=SNOMED_SYSTEM, use_code="900000000000003001"),
            ),
            properties=(),
        ),
    )
    token = _role_token(api, subject="sub-lookup-resolved-version", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
    assert response.json()["resolved_version"] == _RESOLVED_VERSION


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_lookup_active_property_not_reported_serves_active_null(api: ApiTestApp) -> None:
    """Hazard H-05: a server that never reports `inactive` at all must not
    be read as "active" - see `resolve_concept`'s own module docstring.
    `StubConcept` always seeds an `inactive` property (via
    `_lookup_result_from_concept`), so this seeds a raw `LookupResult`
    directly, with no `inactive` property, to reach the case a real
    non-conformant server could produce."""
    api.terminology.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display=_AU_PREFERRED_TERM,
            designations=(
                Designation(
                    value=_FSN,
                    use_system=SNOMED_SYSTEM,
                    use_code="900000000000003001",
                ),
            ),
            properties=(),
        ),
    )
    token = _role_token(api, subject="sub-lookup-unreported", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
    assert response.json()["active"] is None


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_lookup_inactive_concept_is_200_with_active_false(api: ApiTestApp) -> None:
    _seed_concept(api, active=False)
    token = _role_token(api, subject="sub-lookup-inactive", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
    assert response.json()["active"] is False


@pytest.mark.req("FR-52")
@pytest.mark.integration
def test_lookup_issues_exactly_one_upstream_request(api: ApiTestApp) -> None:
    """FR-26's landing note: "one request, not N" (FR-52's own discipline)
    and, simultaneously, the offline proof (FR-53, NFR-37) - this is what
    proves the route calls `lookup` and nothing else."""
    _seed_concept(api)
    token = _role_token(api, subject="sub-lookup-one-request", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
    assert len(api.terminology.requests) == 1
    assert api.terminology.requests[0].operation == Operation.LOOKUP


# --- error mapping (issue #240's table) ---------------------------------


@pytest.mark.req("FR-26")
@pytest.mark.integration
@pytest.mark.parametrize("bad_code", ["not-a-code", "391483009"], ids=["malformed", "bad-checksum"])
def test_lookup_invalid_sctid_is_422_with_no_upstream_request(
    api: ApiTestApp, bad_code: str
) -> None:
    token = _role_token(api, subject=f"sub-lookup-invalid-{bad_code}", role=Role.PROVISIONAL)

    response = _get(api, token, code=bad_code)

    assert response.status_code == 422, response.text
    assert api.terminology.requests == ()


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_lookup_code_not_on_server_is_404(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP,
        TerminologyStatusError("not found", status_code=404),
        key=_CODE,
    )
    token = _role_token(api, subject="sub-lookup-404", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 404, response.text
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_timeout_is_503(api: ApiTestApp) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"), key=_CODE)
    token = _role_token(api, subject="sub-lookup-timeout", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 503, response.text
    assert set(response.json()) == {"detail"}


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_transport_failure_is_503(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP, TerminologyTransportError("connection refused"), key=_CODE
    )
    token = _role_token(api, subject="sub-lookup-transport", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 503, response.text


@pytest.mark.req("FR-54")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("retry_after", "expected_header"),
    [
        pytest.param(30.0, "30", id="whole-number"),
        # ceil, not int(): a sub-second wait must never round down to "0",
        # which HTTP clients would read as "retry immediately" rather than
        # "wait about a second" (issue #240 review).
        pytest.param(0.4, "1", id="sub-second-rounds-up"),
        # `is not None`, not truthiness: a server-supplied `0.0` is a real
        # "retry immediately" answer and must still produce a header, not
        # silently be treated the same as "no retry_after at all".
        pytest.param(0.0, "1", id="zero-still-emits-a-header"),
    ],
)
def test_lookup_persisted_rate_limit_is_503_with_retry_after(
    api: ApiTestApp, retry_after: float, expected_header: str
) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP,
        TerminologyRateLimitError("rate limited", status_code=429, retry_after=retry_after),
        key=_CODE,
    )
    token = _role_token(
        api, subject=f"sub-lookup-rate-limit-{expected_header}", role=Role.PROVISIONAL
    )

    response = _get(api, token)

    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == expected_header
    assert set(response.json()) == {"detail"}


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_rate_limit_with_no_retry_after_omits_the_header(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP,
        TerminologyRateLimitError("rate limited", status_code=429, retry_after=None),
        key=_CODE,
    )
    token = _role_token(api, subject="sub-lookup-rate-limit-none", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 503, response.text
    assert "Retry-After" not in response.headers


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_operation_outcome_body_is_502(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP, TerminologyOutcomeError("server refused the request"), key=_CODE
    )
    token = _role_token(api, subject="sub-lookup-outcome", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 502, response.text
    assert set(response.json()) == {"detail"}


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_unseeded_stub_is_502_never_404(api: ApiTestApp) -> None:
    """The landmine `nptc.terminology.concepts`'s own module docstring
    names: `StubNotSeededError` is a bare `TerminologyError`, neither a
    status nor a transport failure, so it must fall through to the 502
    catch-all - reading it as 404 would let an unseeded stub answer every
    lookup with a clean-looking absence."""
    token = _role_token(api, subject="sub-lookup-unseeded", role=Role.PROVISIONAL)

    response = _get(api, token, code="71388002")

    assert response.status_code == 502, response.text


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_lookup_terminology_config_error_is_500(api: ApiTestApp) -> None:
    """Round-2 review: `TerminologyConfigError` is a `TerminologyError`
    subclass `resolve_concept` re-raises unchanged, so it must reach
    `nptc.api.errors`'s own config-error handler (500) rather than falling
    into `classify_terminology_error`'s 502 catch-all."""
    api.terminology.seed_error(Operation.LOOKUP, TerminologyConfigError("bad config"), key=_CODE)
    token = _role_token(api, subject="sub-lookup-config-error", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 500, response.text
    assert set(response.json()) == {"detail"}


# --- authorisation (FR-44, ADR-0028) -------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_lookup_no_credential_is_401_with_bearer_challenge(api: ApiTestApp) -> None:
    _seed_concept(api)

    response = _get(api, token=None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_lookup_authenticated_without_registry_read_is_403(api: ApiTestApp) -> None:
    """`Role.OBSERVER` is the one authenticated role ADR-0028 excludes from
    `Permission.REGISTRY_READ` - see `test_api_registry_properties.py`'s
    identically-reasoned test."""
    _seed_concept(api)
    token = _role_token(api, subject="sub-lookup-observer", role=Role.OBSERVER)

    response = _get(api, token)

    assert response.status_code == 403, response.text
    # The 401-vs-403 pair endpoints most reliably get backwards: a 403 must
    # never carry the challenge, since the credential itself was fine here.
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_lookup_provisional_role_is_200(api: ApiTestApp) -> None:
    """The positive control this route exists to serve (FR-26 names the
    submitter, and FR-23 makes that Provisional and up) - fails if someone
    later re-gates this route on `Permission.CATALOGUE_EDIT_PUBLISHED`."""
    _seed_concept(api)
    token = _role_token(api, subject="sub-lookup-provisional", role=Role.PROVISIONAL)

    response = _get(api, token)

    assert response.status_code == 200, response.text
