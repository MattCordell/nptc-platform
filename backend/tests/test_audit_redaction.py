"""Offline unit tests for nptc.audit.policy (issue #37, NFR-26).

No container, no network - pure policy construction/resolution plus a walk
over every mapped model actually registered on `nptc.db.base.Base`. See
`test_audit_write_path_guard.py` for the complementary call-path guard and
`test_audit_diffing.py` for `diff_snapshots`'s own runtime re-check of
these same rules against a hand-built snapshot.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nptc.audit.diffing import ChangeKind, diff_snapshots
from nptc.audit.policy import (
    AuditFieldPolicy,
    AuditPolicyError,
    DeniedAuditFieldError,
    MissingAuditPolicyError,
    policy_for,
)
from nptc.db.base import Base
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


@pytest.mark.req("NFR-26")
def test_every_mapped_model_resolves_a_policy_or_is_explicitly_exempt() -> None:
    """The guard that actually bites: a future model (#46's
    `catalogue_entry`) cannot land without classifying every column, and
    cannot be silently left unclassified either."""
    for mapper in Base.registry.mappers:
        model = mapper.class_
        try:
            policy_for(model)
        except MissingAuditPolicyError:
            reason = getattr(model, "__audit_exempt_reason__", None)
            assert isinstance(reason, str) and reason.strip(), (
                f"{model.__name__} has no audit policy and no "
                "__audit_exempt_reason__ - every mapped model must classify itself"
            )


@pytest.mark.req("NFR-26")
def test_every_declared_field_is_a_real_column() -> None:
    for mapper in Base.registry.mappers:
        model = mapper.class_
        try:
            policy = policy_for(model)
        except MissingAuditPolicyError:
            continue
        declared = policy.auditable | policy.withheld
        assert declared <= policy.known


@pytest.mark.req("NFR-26")
def test_user_policy_withholds_identifying_fields() -> None:
    policy = policy_for(User)

    assert policy.auditable == frozenset({"status", "closed_at"})
    assert policy.withheld == frozenset({"username", "display_name", "organisation"})


@pytest.mark.req("NFR-26")
def test_user_identity_policy_withholds_pii_and_oidc_subject() -> None:
    policy = policy_for(UserIdentity)

    assert policy.auditable == frozenset({"email_verified"})
    assert policy.withheld == frozenset({"issuer", "subject", "email"})


@pytest.mark.req("NFR-26")
def test_audit_event_is_exempt_not_merely_undeclared() -> None:
    with pytest.raises(MissingAuditPolicyError):
        policy_for(AuditEvent)

    assert AuditEvent.__audit_exempt_reason__.strip()


@pytest.mark.req("NFR-26")
def test_a_field_outside_the_policy_never_reaches_the_diff() -> None:
    """AC-4: a column nobody declared auditable or withheld cannot be
    smuggled into a diff via a hand-built snapshot."""
    policy = policy_for(User)

    with pytest.raises(AuditPolicyError):
        diff_snapshots(
            policy=policy,
            before={"id": "1"},
            after={"id": "2"},
            kind=ChangeKind.UPDATED,
        )


@pytest.mark.req("NFR-26")
@pytest.mark.parametrize(
    "denied_name",
    [
        "password_hash",
        "client_secret",
        "api_key",
        "access_token",
        "totp_secret",
        "session_id",
    ],
)
def test_a_policy_listing_a_denied_name_fails_at_construction(denied_name: str) -> None:
    """AC-4's principal failure mode: a credential-shaped name must be
    refused the moment a policy tries to declare it, not merely omitted by
    convention."""
    with pytest.raises(DeniedAuditFieldError):
        AuditFieldPolicy(
            entity_type="widget",
            auditable=frozenset({denied_name}),
            withheld=frozenset(),
            known=frozenset({denied_name}),
        )


@pytest.mark.req("NFR-26")
def test_a_policy_cannot_claim_a_reserved_underscore_name() -> None:
    with pytest.raises(AuditPolicyError):
        AuditFieldPolicy(
            entity_type="widget",
            auditable=frozenset({"_redacted"}),
            withheld=frozenset(),
            known=frozenset({"_redacted"}),
        )


@pytest.mark.req("NFR-26")
def test_a_policy_cannot_declare_a_field_both_auditable_and_withheld() -> None:
    with pytest.raises(AuditPolicyError):
        AuditFieldPolicy(
            entity_type="widget",
            auditable=frozenset({"status"}),
            withheld=frozenset({"status"}),
            known=frozenset({"status"}),
        )


@pytest.mark.req("NFR-26")
def test_a_policy_cannot_declare_an_unknown_field() -> None:
    with pytest.raises(AuditPolicyError):
        AuditFieldPolicy(
            entity_type="widget",
            auditable=frozenset({"typo_field"}),
            withheld=frozenset(),
            known=frozenset({"status"}),
        )


@pytest.mark.req("NFR-26")
def test_a_hand_built_snapshot_with_a_denied_key_is_refused() -> None:
    policy = policy_for(User)

    with pytest.raises(DeniedAuditFieldError):
        diff_snapshots(
            policy=policy,
            before={"password_hash": "x"},
            after={"password_hash": "y"},
            kind=ChangeKind.UPDATED,
        )


@pytest.mark.req("NFR-26")
def test_policy_for_refuses_a_declared_field_missing_active_history() -> None:
    """Without `active_history=True`, SQLAlchemy cannot recover a prior
    value for an attribute reassigned before it was ever loaded -
    `diff_instance`'s `load_history()` call would silently report
    `before=None` instead of the true prior value. `policy_for` refuses to
    resolve a policy over a column missing this, rather than let that
    silent gap ship."""

    class _NoActiveHistoryBase(DeclarativeBase):
        pass

    class _Gadget(_NoActiveHistoryBase):
        __tablename__ = "gadget"
        __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status"})
        __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()

        id: Mapped[int] = mapped_column(primary_key=True)
        status: Mapped[str] = mapped_column(Text, nullable=False)

    with pytest.raises(AuditPolicyError):
        policy_for(_Gadget)
