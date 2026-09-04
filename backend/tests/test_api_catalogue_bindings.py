"""HTTP tests for `nptc.api.routers.catalogue_bindings` (issue #219, FR-06,
FR-08, FR-36, NFR-08, NFR-20).

The service layer (`nptc.catalogue.bindings`) already has its own unit
tests in `test_catalogue_bindings.py`; this module proves the HTTP adapter
on top of it - request/response shape, status codes, the exception-handler
mapping in `nptc.api.errors`, and authorisation (FR-44) - against the real
`create_app()`, not a throwaway one.

The negative case is the point (CLAUDE.md): every domain refusal
(`CodeBinding*`) and every authorisation refusal (no credential, no
permission, no MFA step-up) has its own test here, not just the happy path.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue import queries
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity

REPO_ROOT = Path(__file__).resolve().parents[2]


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

#: Real, Verhoeff-valid SCTIDs - the same two `test_catalogue_bindings.py`
#: and `public_catalogue_support.py` already use, plus one more for the
#: cross-entry conflict test, since that needs three distinct codes live at
#: once. Invented digits do not insert at all: `code`'s `CHECK` constraint
#: calls `nptc_sctid_is_valid`.
CODE_A = "391483001"
FSN_A = "Microscopy (acid fast bacilli) (procedure)"
AU_PREFERRED_A = "Microscopy (acid fast bacilli)"
CODE_B = "71388002"
FSN_B = "Procedure (procedure)"
CODE_C = "122192001"
FSN_C = "Injury of hip region (disorder)"

_REASON = "Bound during onboarding of the current SPIA release."


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _seed_entry(
    api: ApiTestApp,
    *,
    preferred_term: str = "Full blood count",
    status: str = "draft",
) -> str:
    entry = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for issue #219 API test",
        status=status,
    )
    api.session.flush()
    return entry.business_key


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    """Signs `subject` in, grants `Role.ADMINISTRATOR`, and returns a token
    - with an `acr` claim the realm maps to LoA-2 unless `with_mfa` is
    `False`, matching `test_api_error_mapping.py`'s own MFA pair."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    # By `subject`, not "the newest `User` row": `created_at` is
    # server-side `now()`, so two users provisioned inside one transaction
    # (as parallel tests under one session-scoped container can do) may tie
    # on it, and picking the wrong one would grant ADMINISTRATOR to a
    # different test's principal (issue #219 review).
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _latest_audit_event(session: Any, *, entity_type: str, entity_id: Any) -> AuditEvent:
    """Scoped per CLAUDE.md - keyed on `entity_type` + `entity_id`, not a
    whole-table read. This router's writes key the audit event on the
    `code_binding` row itself, never on the entry (see `record_change`'s
    default `entity_id`)."""
    return session.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == str(entity_id))
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).scalar_one()


def _entry_id(api: ApiTestApp, business_key: str) -> Any:
    return api.session.execute(
        select(CatalogueEntry.id).where(CatalogueEntry.business_key == business_key)
    ).scalar_one()


def _binding_id(api: ApiTestApp, *, entry_id: Any, code: str, status: str = "active") -> Any:
    from nptc.db.models.code_binding import CodeBinding

    return api.session.execute(
        select(CodeBinding.id).where(
            CodeBinding.entry_id == entry_id, CodeBinding.code == code, CodeBinding.status == status
        )
    ).scalar_one()


def _bind(api: ApiTestApp, business_key: str, token: str, **overrides: object) -> Any:
    body = {"code": CODE_A, "fsn": FSN_A, "au_preferred_term": AU_PREFERRED_A, "reason": _REASON}
    body.update(overrides)
    return api.post(f"/catalogue/entries/{business_key}/bindings", token=token, json=body)


# --- happy paths -------------------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_bind_code_returns_201_with_the_binding_as_served(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bind-happy")
    before = _audit_event_count(api)

    response = _bind(api, business_key, token)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == CODE_A
    assert isinstance(body["code"], str)
    assert body["fsn"] == FSN_A
    assert body["au_preferred_term"] == AU_PREFERRED_A
    assert body["status"] == "active"
    assert body["retirement_reason"] is None
    assert body["replaced_by_code"] is None
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-06")
@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_bind_code_audits_the_created_row_with_reason(api: ApiTestApp) -> None:
    """The parent-level acceptance criterion #61 owns for code bindings: a
    bind is a `ChangeKind.CREATED` diff, so `before` is `None` and `after`
    carries every non-null field `CodeBinding.__audit_fields__` declares -
    and the changelog note reaches `AuditEvent.reason` verbatim (FR-37)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bind-audit")
    entry_id = _entry_id(api, business_key)
    reason = "Bound during onboarding of the current SPIA release - audit test."

    response = _bind(api, business_key, token, reason=reason)

    assert response.status_code == 201, response.text
    binding_id = _binding_id(api, entry_id=entry_id, code=CODE_A)
    event = _latest_audit_event(api.session, entity_type="code_binding", entity_id=binding_id)
    assert event.action == "code_binding.created"
    assert event.before is None
    assert event.after == {
        "entry_id": str(entry_id),
        "system": "http://snomed.info/sct",
        "code": CODE_A,
        "fsn": FSN_A,
        "au_preferred_term": AU_PREFERRED_A,
        "edition_hint": "unknown",
        "status": "active",
    }
    assert event.reason == reason


@pytest.mark.integration
def test_bind_code_location_header_points_at_a_route_that_actually_serves_it(
    api: ApiTestApp,
) -> None:
    """`response.headers["Location"]` must be a URL a client can actually
    follow - `/api/v1` prefix included, since that comes from `include_
    router`, not the router's own `prefix` (issue #219 review: an earlier
    version omitted it and pointed at a path with no `GET` handler at
    all). `status="active"` specifically: the public `GET` this test
    follows the header to only serves `active` entries (FR-20), unlike
    every other test in this module."""
    business_key = _seed_entry(api, status="active")
    token = _admin_token(api, subject="sub-bind-location")

    response = _bind(api, business_key, token)

    assert response.status_code == 201, response.text
    location = response.headers["Location"]
    assert location == f"/api/v1/catalogue/entries/{business_key}"

    followed = api.client.get(location, headers={"Authorization": f"Bearer {token}"})
    assert followed.status_code == 200, followed.text
    codes = {b["code"] for b in followed.json()["bindings"]}
    assert CODE_A in codes


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_retire_binding_requires_and_records_a_reason(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-happy")
    _bind(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Superseded during SPIA edition update."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "retired"
    assert body["retirement_reason"] == "Superseded during SPIA edition update."
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_retire_binding_audits_the_status_change_with_reason(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-audit")
    _bind(api, business_key, token)
    entry_id = _entry_id(api, business_key)
    binding_id = _binding_id(api, entry_id=entry_id, code=CODE_A)
    reason = "Superseded during SPIA edition update - retirement audit test."

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": reason},
    )

    assert response.status_code == 200, response.text
    event = _latest_audit_event(api.session, entity_type="code_binding", entity_id=binding_id)
    assert event.action == "code_binding.retired"
    assert event.before == {"status": "active", "retirement_reason": None}
    assert event.after == {"status": "retired", "retirement_reason": reason}
    assert event.reason == reason


@pytest.mark.req("FR-08")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_replace_binding_retires_creates_and_links_in_one_request(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-replace-happy")
    _bind(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={
            "successor": {"code": CODE_B, "fsn": FSN_B},
            "reason": "Replaced with the successor concept for this SPIA edition.",
        },
    )

    assert response.status_code == 200, response.text
    items = {item["code"]: item for item in response.json()["items"]}
    assert items[CODE_A]["status"] == "retired"
    assert items[CODE_A]["replaced_by_code"] == CODE_B
    assert items[CODE_B]["status"] == "active"
    # Three audit events: retired, created, replacement_linked - all in the
    # one request's transaction (the module docstring's whole point).
    assert _audit_event_count(api) == before + 3


@pytest.mark.req("FR-08")
@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_replace_binding_audits_all_three_steps_with_before_after_and_reason(
    api: ApiTestApp,
) -> None:
    """The one write path with three audit events sharing one request-level
    reason: retiring the predecessor, creating the successor, and linking
    them. Each event's `before`/`after` must reflect only the field(s) that
    step actually changed - the module's own "retire, create, link" claim,
    proven at the audit layer rather than only via the response body."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-replace-audit")
    _bind(api, business_key, token)
    entry_id = _entry_id(api, business_key)
    predecessor_id = _binding_id(api, entry_id=entry_id, code=CODE_A)
    # The high-water mark before the replacement request - `predecessor_id`
    # already carries its own `code_binding.created` event from `_bind`
    # above, so filtering by entity_id alone (predecessor + successor)
    # would also pick that one up. Filtering by sequence instead isolates
    # exactly the three events this one request produces.
    sequence_floor = api.session.execute(select(func.max(AuditEvent.sequence))).scalar_one()
    reason = "Replaced with the successor concept for this SPIA edition - audit test."

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={"successor": {"code": CODE_B, "fsn": FSN_B}, "reason": reason},
    )

    assert response.status_code == 200, response.text
    successor_id = _binding_id(api, entry_id=entry_id, code=CODE_B)

    events = (
        api.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "code_binding",
                AuditEvent.entity_id.in_([str(predecessor_id), str(successor_id)]),
                AuditEvent.sequence > sequence_floor,
            )
            .order_by(AuditEvent.sequence.asc())
        )
        .scalars()
        .all()
    )
    actions = [event.action for event in events]
    assert actions == [
        "code_binding.retired",
        "code_binding.created",
        "code_binding.replacement_linked",
    ]
    retired_event, created_event, linked_event = events

    assert retired_event.before == {"status": "active", "retirement_reason": None}
    assert retired_event.after == {"status": "retired", "retirement_reason": reason}
    assert retired_event.reason == reason

    assert created_event.before is None
    # `successor` in the replacement request omits `au_preferred_term`
    # (only `code`/`fsn` are given) - `create_binding`'s default is `None`,
    # and a `CREATED` diff only includes fields whose after-value is not
    # `None` (`diff_instance`), so the column is absent here, not present
    # with a null value.
    assert created_event.after == {
        "entry_id": str(entry_id),
        "system": "http://snomed.info/sct",
        "code": CODE_B,
        "fsn": FSN_B,
        "edition_hint": "unknown",
        "status": "active",
    }
    assert created_event.reason == reason

    assert linked_event.before == {"replaced_by_binding_id": None}
    assert linked_event.after == {"replaced_by_binding_id": str(successor_id)}
    assert linked_event.reason == reason


# --- domain refusals -----------------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_bind_malformed_sctid_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-malformed")

    response = _bind(api, business_key, token, code="not-a-code")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_bind_verhoeff_failing_sctid_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-verhoeff")

    # One digit off CODE_A's own valid check digit.
    response = _bind(api, business_key, token, code="391483002")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_second_active_binding_on_one_entry_is_409(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-second-active")
    _bind(api, business_key, token)

    response = _bind(api, business_key, token, code=CODE_C, fsn=FSN_C)

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_same_code_active_on_a_different_entry_is_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-cross-entry")
    first_entry = _seed_entry(api, preferred_term="Full blood count")
    second_entry = _seed_entry(api, preferred_term="Urine microscopy")
    _bind(api, first_entry, token)

    response = _bind(api, second_entry, token)

    assert response.status_code == 409, response.text


@pytest.mark.integration
def test_retire_without_a_reason_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-no-reason")
    _bind(api, business_key, token)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": ""},
    )

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_retiring_an_already_retired_binding_is_404_not_409(api: ApiTestApp) -> None:
    """`nptc.catalogue.bindings.retire_binding` itself raises
    `CodeBindingAlreadyRetiredError` (409) given an already-retired
    `CodeBinding` instance - but this route addresses a binding by `code`
    through `load_active_binding`, which is scoped to `status == 'active'`
    (see that function's own docstring). A second retirement attempt can
    therefore never reach `retire_binding` with a retired binding in hand:
    the code is simply no longer addressable this way once retired, so the
    honest answer is 404, and `CodeBindingAlreadyRetiredError` is
    unreachable from this particular route (it stays mapped in
    `nptc.api.errors` for whichever future write path holds an already-
    loaded `CodeBinding` rather than resolving one by code)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-double-retire")
    _bind(api, business_key, token)
    api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "First retirement."},
    )

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Second retirement attempt."},
    )

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_retire_a_code_with_no_active_binding_is_404(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-missing")

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Nothing to retire."},
    )

    assert response.status_code == 404, response.text


@pytest.mark.req("FR-36")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "active", "deprecated", "withdrawn"])
def test_bind_code_works_against_an_entry_in_any_status(api: ApiTestApp, status: str) -> None:
    """`load_entry_for_update` is deliberately status-agnostic (see its own
    docstring) - an editing surface has to reach `draft` before an entry
    can ever become `active`, and this module's own docstring says
    `deprecated`/`withdrawn` come along with that choice rather than being
    separately excluded. Pinned explicitly per status (issue #219 review),
    not just exercised incidentally via `draft` fixtures elsewhere."""
    business_key = _seed_entry(api, status=status)
    token = _admin_token(api, subject=f"sub-status-{status}")

    response = _bind(api, business_key, token)

    assert response.status_code == 201, response.text


@pytest.mark.integration
def test_bind_unknown_business_key_is_404(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-unknown-entry")

    response = _bind(api, "NPTC-999999", token)

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_replace_against_a_never_bound_code_is_404(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-replace-missing")

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={"successor": {"code": CODE_B, "fsn": FSN_B}, "reason": _REASON},
    )

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_replace_against_an_already_retired_code_is_404(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-replace-retired")
    _bind(api, business_key, token)
    api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Retired ahead of the replacement attempt."},
    )

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={"successor": {"code": CODE_B, "fsn": FSN_B}, "reason": _REASON},
    )

    assert response.status_code == 404, response.text


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_bind_blank_fsn_is_422_not_500(api: ApiTestApp) -> None:
    """`ck_code_binding_fsn_not_blank` checks `btrim(fsn)`, not raw length,
    so a whitespace-only `fsn` would otherwise reach the flush and 500 as
    an unmapped `IntegrityError` (issue #219 review)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-blank-fsn")

    response = _bind(api, business_key, token, fsn="   ")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_bind_blank_au_preferred_term_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-blank-au")

    response = _bind(api, business_key, token, au_preferred_term=" ")

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_bind_unrecognised_edition_hint_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bad-edition-hint")

    response = _bind(api, business_key, token, edition_hint="not-a-real-edition")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_replace_with_the_same_code_as_successor_is_409(api: ApiTestApp) -> None:
    """A same-code replacement would otherwise retire and re-bind the same
    code in one request - `_row_to_binding` would then resolve both list
    entries to the one active row, reporting the successor twice and never
    surfacing the retirement the caller asked for (issue #219 review)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-self-replace")
    _bind(api, business_key, token)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={"successor": {"code": CODE_A, "fsn": FSN_A}, "reason": _REASON},
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-08")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_replace_whose_successor_code_is_bound_elsewhere_rolls_back_the_whole_request(
    api: ApiTestApp,
) -> None:
    """The module's headline design claim: retire -> create -> link all
    commit together or none do. Here `create_binding` fails on the third
    step's own conflict (the successor code is already active on a
    different entry), so the predecessor on `first_entry` must still be
    `active` and no audit event from this request may have landed."""
    token = _admin_token(api, subject="sub-replace-rollback")
    first_entry = _seed_entry(api, preferred_term="Full blood count")
    second_entry = _seed_entry(api, preferred_term="Urine microscopy")
    _bind(api, first_entry, token)
    _bind(api, second_entry, token, code=CODE_B, fsn=FSN_B)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{first_entry}/bindings/{CODE_A}/replacement",
        token=token,
        json={"successor": {"code": CODE_B, "fsn": FSN_B}, "reason": _REASON},
    )

    assert response.status_code == 409, response.text
    assert _audit_event_count(api) == before

    # Not the public GET route: `_seed_entry` leaves entries `draft`
    # (`create_entry`'s own default), and that route only serves `active`
    # ones (FR-20) - querying `api.session` directly (the same session the
    # rolled-back request itself ran against) is what actually proves the
    # write, not just the response, disappeared.
    entry_id = api.session.execute(
        select(CatalogueEntry.id).where(CatalogueEntry.business_key == first_entry)
    ).scalar_one()
    (row,) = queries.load_bindings(api.session, (entry_id,))
    assert row.code == CODE_A
    assert row.status == "active"


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_rebinding_a_retired_code_then_retiring_it_again_reads_back_the_right_row(
    api: ApiTestApp,
) -> None:
    """`(entry_id, code)` is unique only among *active* bindings - a code
    bound, retired, and bound again leaves two *retired* rows sharing one
    code once it is retired the second time. A code-keyed re-read could
    return either; keying on the just-written row's own `id` (issue #219
    review) must return the second retirement's own reason, not the
    first's."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-rebind-retire")
    _bind(api, business_key, token)
    api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "First retirement."},
    )
    _bind(api, business_key, token)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Second retirement, must be what comes back."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["retirement_reason"] == "Second retirement, must be what comes back."


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)

    response = _bind(api, business_key, token=None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_without_the_permission_is_403_with_no_challenge(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = api.token(subject="sub-no-permission")

    response = _bind(api, business_key, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-admin-no-mfa", with_mfa=False)

    response = _bind(api, business_key, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_conflict_response_names_no_internal_identifier(api: ApiTestApp) -> None:
    """`CodeBindingCodeAlreadyBoundError`'s exception message names the
    other entry's internal UUID (for the log); the response body must not
    (NFR-04/NFR-26) - the same convention every other handler in
    `nptc.api.errors` follows."""
    token = _admin_token(api, subject="sub-conflict-body")
    first_entry = _seed_entry(api, preferred_term="Full blood count")
    second_entry = _seed_entry(api, preferred_term="Urine microscopy")
    _bind(api, first_entry, token)

    response = _bind(api, second_entry, token)

    uuid_pattern = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    assert uuid_pattern.search(response.text) is None, response.text
