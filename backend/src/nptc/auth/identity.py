"""Resolving an OIDC identity to an internal `app_user`, and account
closure (issue #42).

Every function here takes a ``sqlalchemy.orm.Session`` as an explicit
argument - there is no engine, no sessionmaker and no FastAPI app in this
module or anywhere else in this issue's scope (#41/#43/#44 own those).
Tests bind a ``Session(bind=app_db)`` to the existing testcontainers
fixture connection (the standard join-an-external-transaction pattern), so
the fixture's rollback-per-test semantics are untouched.

Outcomes are returned as a result object, never raised as control-flow
exceptions, so #43 can map each one to an HTTP response without a
try/except ladder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.linking import may_auto_link
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity


class LinkOutcome(StrEnum):
    EXISTING = "existing"
    CREATED = "created"
    AUTO_LINKED = "auto_linked"
    MANUAL_LINK_REQUIRED = "manual_link_required"


@dataclass(frozen=True)
class Resolution:
    outcome: LinkOutcome
    #: None only for MANUAL_LINK_REQUIRED - there is deliberately no user
    #: to hand back until a human resolves the conflict.
    user: User | None


def _find_identity(session: Session, issuer: str, subject: str) -> UserIdentity | None:
    stmt = select(UserIdentity).where(
        UserIdentity.issuer == issuer, UserIdentity.subject == subject
    )
    return session.execute(stmt).scalar_one_or_none()


def _find_auto_link_candidate(session: Session, email: str | None) -> UserIdentity | None:
    if not email:
        return None
    stmt = (
        select(UserIdentity)
        .join(User, User.id == UserIdentity.user_id)
        .where(
            UserIdentity.email == email,
            UserIdentity.email_verified.is_(True),
            User.status != UserStatus.CLOSED,
        )
    )
    return session.execute(stmt).scalars().first()


def resolve_user_for_claims(
    session: Session,
    claims: OidcIdentityClaims,
    *,
    trusted_issuers: frozenset[str],
) -> Resolution:
    existing = _find_identity(session, claims.issuer, claims.subject)
    if existing is not None:
        user = session.get(User, existing.user_id)
        if user is None or user.status == UserStatus.CLOSED:
            # Defence in depth: closure normally deletes the identity row
            # outright, so this should not be reachable in practice.
            return Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)
        existing.email = claims.email
        existing.email_verified = claims.email_verified
        if claims.display_name is not None:
            user.display_name = claims.display_name
        return Resolution(outcome=LinkOutcome.EXISTING, user=user)

    candidate = _find_auto_link_candidate(session, claims.email)
    if candidate is None:
        user = User(
            username=claims.preferred_username,
            display_name=claims.display_name,
            organisation=None,
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                issuer=claims.issuer,
                subject=claims.subject,
                email=claims.email,
                email_verified=claims.email_verified,
            )
        )
        return Resolution(outcome=LinkOutcome.CREATED, user=user)

    if not may_auto_link(claims, trusted_issuers):
        return Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)

    session.add(
        UserIdentity(
            user_id=candidate.user_id,
            issuer=claims.issuer,
            subject=claims.subject,
            email=claims.email,
            email_verified=claims.email_verified,
        )
    )
    user = session.get(User, candidate.user_id)
    return Resolution(outcome=LinkOutcome.AUTO_LINKED, user=user)


def close_account(session: Session, user_id: uuid.UUID) -> None:
    """Pseudonymises the user and removes every linked identity (NFR-17).

    Never deletes the ``app_user`` row - the privilege grants in migration
    0003 make that structurally impossible even if this function tried.
    Idempotent: closing an already-closed account is a no-op. Deliberately
    does **not** emit an audit event - there is no audit writer until #36;
    the caller's own docstring/PR body should say so rather than this
    function stubbing one out.
    """
    user = session.get(User, user_id)
    if user is None or user.status == UserStatus.CLOSED:
        return

    session.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
    user.username = None
    user.display_name = None
    user.organisation = None
    user.status = UserStatus.CLOSED
    user.closed_at = datetime.now(UTC)


class UserRef(BaseModel):
    """The NFR-04 serialisation boundary: what any API response or export
    is allowed to say about a user. No ``id`` field, ever - the internal
    UUID must never escape past this type. #43/#142/#143 route through
    this structurally instead of relying on reviewer memory that the UUID
    must not leak.
    """

    model_config = ConfigDict(frozen=True)

    username: str | None
    display_name: str | None
    organisation: str | None
    status: str

    @classmethod
    def from_user(cls, user: User) -> UserRef:
        return cls(
            username=user.username,
            display_name=user.display_name,
            organisation=user.organisation,
            status=user.status,
        )
