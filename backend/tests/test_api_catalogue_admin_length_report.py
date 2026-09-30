"""HTTP tests for `GET /catalogue/admin/preferred-term-length-distribution`
(issue #152, FR-87).

Follows `test_api_catalogue_admin_read.py`'s own shape: the service layer
(`nptc.catalogue.length_report`) already has its own unit tests in
`test_catalogue_length_report.py`; this module proves the HTTP adapter -
request/response shape and authorisation (FR-44) - against the real
`create_app()`.

Whole-table aggregate, so every numeric assertion here is a relative delta
against a baseline this test itself establishes, matching
`test_catalogue_length_report.py`'s own CLAUDE.md-mandated convention - the
session-scoped Postgres container is shared with every other test in the
run.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import literal, select
from sqlalchemy.engine import Connection

import nptc.catalogue.length_report as length_report
from nptc.api.routers.catalogue_admin import LengthDistributionBucket, LengthDistributionReport
from nptc.api.routers.catalogue_designations import AmendDesignationResult
from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.entries import create_entry
from nptc.catalogue.length_report import build_length_histogram_statement
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


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

_PATH = "/catalogue/admin/preferred-term-length-distribution"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _token_with_role(api: ApiTestApp, *, subject: str, role: Role, with_mfa: bool = True) -> str:
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    return _token_with_role(api, subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa)


def _report(api: ApiTestApp, token: str | None) -> Any:
    return api.get(_PATH, token=token)


def _new_entry(api: ApiTestApp, preferred_term: str) -> None:
    create_entry(
        api.session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for the FR-87 HTTP report test",
    )
    api.session.flush()


# --- happy path --------------------------------------------------------


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_the_report_reflects_entries_this_test_created(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-length-report-happy")
    before = _report(api, token).json()
    before_buckets = {b["length"]: b for b in before["buckets"]}

    short = "Iron"
    long = "Full blood count, automated for this test"
    _new_entry(api, short)
    _new_entry(api, long)

    after = _report(api, token).json()
    after_buckets = {b["length"]: b for b in after["buckets"]}

    assert (
        after_buckets[len(short)]["count"] - before_buckets.get(len(short), {}).get("count", 0) == 1
    )
    assert len(long) in after_buckets
    assert after["maximum"] is not None
    assert after["maximum"] >= len(long)
    # `entries_exceeding` at `short`'s length counts `long` (strictly
    # greater), not itself. The baseline is a suffix sum over the buckets
    # already present, because `short`'s own bucket may not exist yet.
    exceeding_short_before = sum(
        bucket["count"] for length, bucket in before_buckets.items() if length > len(short)
    )
    assert after_buckets[len(short)]["entries_exceeding"] - exceeding_short_before == 1, (
        "the newly created longer entry should count as exceeding the shorter one's length"
    )


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_the_longest_bucket_never_exceeds_itself(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-length-report-boundary")
    longest = "Adenosine deaminase, cerebrospinal fluid, quantitative, extended panel edition"
    _new_entry(api, longest)

    body = _report(api, token).json()
    buckets = {b["length"]: b for b in body["buckets"]}

    assert body["maximum"] == max(buckets)
    assert buckets[body["maximum"]]["entries_exceeding"] == 0


@pytest.mark.req("FR-87")
@pytest.mark.integration
def test_an_empty_catalogue_serialises_no_maximum_as_null(
    api: ApiTestApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared container is never empty across a full run, so the
    histogram statement is narrowed to match nothing - what a truly empty
    catalogue returns - and the real route and serialiser run against it."""
    token = _admin_token(api, subject="sub-length-report-empty")
    monkeypatch.setattr(
        length_report,
        "build_length_histogram_statement",
        lambda: build_length_histogram_statement().where(literal(False)),
    )

    response = _report(api, token)

    assert response.status_code == 200, response.text
    assert response.json() == {"buckets": [], "maximum": None}


# --- response contract -------------------------------------------------


@pytest.mark.req("FR-87")
def test_the_report_response_is_immutable_and_hashable() -> None:
    """`frozen=True` alone would leave a `list` field appendable, so the
    buckets are a `tuple`: the model is hashable only if every field is."""
    report = LengthDistributionReport(
        buckets=(LengthDistributionBucket(length=4, count=2, entries_exceeding=1),),
        maximum=4,
    )

    assert isinstance(report.buckets, tuple)
    assert hash(report) == hash(report.model_copy())


@pytest.mark.req("FR-87")
@pytest.mark.req("FR-86")
@pytest.mark.parametrize(
    ("model", "field"),
    [
        (LengthDistributionReport, "maximum"),
        (AmendDesignationResult, "length_warning"),
    ],
)
def test_a_nullable_report_field_is_required_so_null_is_always_deliberate(
    model: type[BaseModel], field: str
) -> None:
    """Both fields may be `null`, and neither may be omitted: a default of
    `None` would let a forgotten value read as "empty catalogue" or "no
    warning"."""
    assert model.model_fields[field].is_required()


# --- authorisation (FR-44, NFR-06, NFR-20) ------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    response = _report(api, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_observer_is_403_with_no_challenge(api: ApiTestApp) -> None:
    token = _token_with_role(api, subject="sub-length-report-observer", role=Role.OBSERVER)

    response = _report(api, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_reviewer_is_403(api: ApiTestApp) -> None:
    """A Reviewer holds `validation.acknowledge` but not
    `catalogue.edit_published` - proving the gate is this specific
    permission, not "any elevated role", matching every other admin route's
    own test of the same shape."""
    token = _token_with_role(api, subject="sub-length-report-reviewer", role=Role.REVIEWER)

    response = _report(api, token)

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-length-report-no-mfa", with_mfa=False)

    response = _report(api, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge
