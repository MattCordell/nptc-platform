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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.linking import may_auto_link
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity

#: Bounded retries for the username-collision fallback in `_create_user`
#: below - large enough that a real collision streak is astronomically
#: unlikely, small enough that a genuine bug (e.g. a broken random source)
#: fails fast instead of spinning.
_MAX_USERNAME_ATTEMPTS = 5


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


def _find_candidate_user_ids(
    session: Session, email: str | None, trusted_issuers: frozenset[str]
) -> list[uuid.UUID]:
    """Users with a verified email matching `email`, asserted by an
    identity whose *own* issuer is trusted - not merely an issuer trusted
    by the *incoming* claim. Without that second check here, a first
    registration through any untrusted issuer could plant a verified email
    that a later, genuinely trusted login would then auto-link into (the
    PRD's stated failure mode: "anyone who can mint a token asserting an
    administrator's email inherits that administrator's privileges" -
    minting it once, on day one, through an untrusted issuer, is exactly
    such a token).

    Returns every distinct matching user, not just one: more than one
    match means the auto-link target is ambiguous, and the caller must
    treat that as `MANUAL_LINK_REQUIRED` rather than guess via query plan
    order.
    """
    if not email or not trusted_issuers:
        return []
    stmt = (
        select(UserIdentity.user_id)
        .join(User, User.id == UserIdentity.user_id)
        .where(
            UserIdentity.email == email,
            UserIdentity.email_verified.is_(True),
            UserIdentity.issuer.in_(trusted_issuers),
            User.status != UserStatus.CLOSED,
        )
        .distinct()
    )
    return list(session.execute(stmt).scalars().all())


_USERNAME_UNIQUE_CONSTRAINT = "uq_app_user_username"
_UNIQUE_VIOLATION_SQLSTATE = "23505"


def _fallback_username(claims: OidcIdentityClaims, suffix: str | None = None) -> str:
    # Deliberately never derived from `claims.email`: `username` is one of
    # the four fields `UserRef` exposes externally, and a user who never
    # chose a handle should not have one silently minted from an address
    # they supplied only for verification (NFR-26/NFR-35 posture). A blank
    # `preferred_username` (whitespace-only, still truthy) is treated the
    # same as a missing one - `app_user.username` carries no CHECK against
    # blank content the way `user_identity.issuer`/`subject` do.
    base = claims.preferred_username.strip() if claims.preferred_username else ""
    base = base or f"user-{uuid.uuid4().hex[:12]}"
    return base if suffix is None else f"{base}-{suffix}"


def _is_username_collision(exc: IntegrityError) -> bool:
    """True only for the specific, retryable collision `_create_user`
    exists to recover from: a duplicate `app_user.username`. A blank
    `subject` (`ck_user_identity_subject_not_blank`) or a duplicate
    `(issuer, subject)` from a concurrent first login
    (`uq_user_identity_issuer`) are different failures with different
    causes - retrying with a new username suffix cannot fix either, and
    reporting them as a username-allocation failure would misdirect
    whoever reads the eventual error."""
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    return sqlstate == _UNIQUE_VIOLATION_SQLSTATE and constraint_name == _USERNAME_UNIQUE_CONSTRAINT


def _create_user(session: Session, claims: OidcIdentityClaims) -> User:
    """Creates the `app_user` (plus its first `user_identity` row) for a
    subject seen for the first time.

    Retried, with a randomised username suffix, on a `uq_app_user_username`
    collision specifically - see `_is_username_collision` - rather than
    letting an ordinary, unremarkable IdP input (a `preferred_username`
    someone else already holds, or no `preferred_username`/`display_name`
    claim at all - both of which real IdPs routinely omit or duplicate)
    surface as a raw `IntegrityError` on first login. Any other constraint
    violation is re-raised immediately: retrying under a different
    username can't fix a blank subject or a racing duplicate `(iss, sub)`.
    Each attempt runs inside its own `SAVEPOINT` so a failed attempt aborts
    only that attempt, not the caller's whole transaction.
    """
    display_name = claims.display_name or claims.preferred_username
    suffix: str | None = None
    for _attempt in range(_MAX_USERNAME_ATTEMPTS):
        username = _fallback_username(claims, suffix)
        try:
            with session.begin_nested():
                user = User(
                    username=username,
                    display_name=display_name or username,
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
                session.flush()
        except IntegrityError as exc:
            if not _is_username_collision(exc):
                raise
            suffix = uuid.uuid4().hex[:8]
            continue
        return user
    raise RuntimeError(
        f"could not allocate a unique username after {_MAX_USERNAME_ATTEMPTS} attempts"
    )


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
        # Safe to refresh unconditionally, regardless of whether
        # claims.issuer is itself trusted: this identity's own issuer is
        # exactly what `_find_candidate_user_ids` checks before any other
        # user can ever auto-link against it, so an untrusted issuer
        # asserting `email_verified=True` here does not create a usable
        # auto-link target.
        existing.email = claims.email
        existing.email_verified = claims.email_verified
        if claims.display_name is not None:
            user.display_name = claims.display_name
        return Resolution(outcome=LinkOutcome.EXISTING, user=user)

    candidate_user_ids = _find_candidate_user_ids(session, claims.email, trusted_issuers)
    if not candidate_user_ids:
        user = _create_user(session, claims)
        return Resolution(outcome=LinkOutcome.CREATED, user=user)

    if len(candidate_user_ids) > 1 or not may_auto_link(claims, trusted_issuers):
        return Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)

    candidate_user_id = candidate_user_ids[0]
    session.add(
        UserIdentity(
            user_id=candidate_user_id,
            issuer=claims.issuer,
            subject=claims.subject,
            email=claims.email,
            email_verified=claims.email_verified,
        )
    )
    user = session.get(User, candidate_user_id)
    if user is None:
        # FK-guaranteed not to happen: candidate_user_id came from a join
        # against app_user in the same transaction. Raising makes that
        # guarantee load-bearing rather than silently handing #43 a
        # `Resolution(AUTO_LINKED, user=None)` its own type says can't occur.
        raise AssertionError(f"candidate user {candidate_user_id} vanished mid-resolution")
    return Resolution(outcome=LinkOutcome.AUTO_LINKED, user=user)


def close_account(session: Session, user_id: uuid.UUID) -> None:
    """Pseudonymises the user and removes every linked identity (NFR-17).

    Never deletes the ``app_user`` row - the privilege grants in migration
    0003 make that structurally impossible even if this function tried.
    Idempotent: closing an already-closed account is a no-op.

    Deliberately does **not** emit an audit event, despite this being a
    state-changing write NFR-08 would otherwise require one for: there is
    no audit writer yet, and ``audit_event`` itself already accepts rows
    (see ``backend/tests/test_auth_account_closure.py``'s direct insert),
    so this is a real gap, not a merely theoretical one. **Tracked by
    issue #36** (the ``audit_event`` hash-chain writer) - when #36 lands,
    this call site is the pickup point for wiring in the actual event.
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
