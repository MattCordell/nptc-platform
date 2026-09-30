"""Resolving an OIDC identity to an internal `app_user`, and account closure
(NFR-05, NFR-17).

Every function here takes a ``sqlalchemy.orm.Session`` explicitly; this module
owns no engine or sessionmaker. Outcomes are returned as a result object, never
raised as control-flow exceptions, so the caller maps each one to an HTTP
response without a try/except ladder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.grants import (
    assert_not_last_administrator,
    grant_role_unchecked,
    revoke_all_roles_unchecked,
)
from nptc.auth.linking import may_auto_link
from nptc.auth.permissions import Role
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity

#: Bounded so a genuine bug (e.g. a broken random source) fails fast instead of
#: spinning in `_create_user`'s username-collision fallback.
_MAX_USERNAME_ATTEMPTS = 5


class LinkOutcome(StrEnum):
    EXISTING = "existing"
    CREATED = "created"
    AUTO_LINKED = "auto_linked"
    MANUAL_LINK_REQUIRED = "manual_link_required"


@dataclass(frozen=True)
class Resolution:
    outcome: LinkOutcome
    #: None only for MANUAL_LINK_REQUIRED: there is no user to hand back until
    #: a human resolves the conflict.
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
    identity whose *own* issuer is trusted, not merely an issuer trusted by
    the *incoming* claim. Without that second check, a first registration
    through an untrusted issuer could plant a verified email that a later,
    genuinely trusted login would auto-link into: exactly the failure mode
    NFR-05 exists to prevent, minted once on day one.

    Returns every distinct matching user. More than one match means the
    auto-link target is ambiguous, which the caller treats as
    `MANUAL_LINK_REQUIRED` instead of guessing by query plan order.
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


def _fallback_username(claims: OidcIdentityClaims, suffix: str | None = None) -> str:
    # Never derived from `claims.email`: `username` is one of the four fields
    # `UserRef` exposes, and a user who never chose a handle should not have one
    # minted from an address supplied only for verification (NFR-26, NFR-35). A
    # whitespace-only `preferred_username` counts as missing, because
    # `app_user.username` has no CHECK against blank content.
    base = claims.preferred_username.strip() if claims.preferred_username else ""
    base = base or f"user-{uuid.uuid4().hex[:12]}"
    return base if suffix is None else f"{base}-{suffix}"


def _is_username_collision(exc: IntegrityError) -> bool:
    """True only for the retryable collision `_create_user` recovers from:
    a duplicate `app_user.username`. A blank `subject`
    (`ck_user_identity_subject_not_blank`) or a duplicate `(issuer, subject)`
    from a concurrent first login (`uq_user_identity_issuer`) cannot be fixed
    by a new username suffix, and reporting them as a username-allocation
    failure would misdirect whoever reads the error."""
    return unique_violation_constraint(exc) == _USERNAME_UNIQUE_CONSTRAINT


def _create_user(session: Session, claims: OidcIdentityClaims, *, audit: AuditContext) -> User:
    """Creates the `app_user` (plus its first `user_identity` row) for a
    subject seen for the first time.

    Retries with a randomised username suffix on a `uq_app_user_username`
    collision (see `_is_username_collision`), because real IdPs routinely omit
    or duplicate `preferred_username` and that must not surface as a raw
    `IntegrityError` on first login. Any other constraint violation is
    re-raised at once. Each attempt runs inside its own `SAVEPOINT`, so a
    failed attempt aborts only itself, including the `user_identity.created`
    audit event: a rolled-back retry never leaves a record of an insert that
    did not happen.
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
                identity = UserIdentity(
                    user_id=user.id,
                    issuer=claims.issuer,
                    subject=claims.subject,
                    email=claims.email,
                    email_verified=claims.email_verified,
                )
                session.add(identity)
                # record_change(kind=CREATED) flushes the session, so it must run
                # before anything else flushes `identity` out of `session.new`.
                record_change(
                    session,
                    audit,
                    action="user_identity.created",
                    instance=identity,
                    kind=ChangeKind.CREATED,
                )
                # PRD Section 4.3: a new user is Provisional, as a real
                # user_role row (ADR-0019). It sits inside the SAVEPOINT so a
                # collision retry leaves no orphan user_role.granted event.
                grant_role_unchecked(
                    session,
                    target_user_id=user.id,
                    role=Role.PROVISIONAL,
                    granted_by_user_id=None,
                    audit=audit,
                )
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
    audit: AuditContext,
) -> Resolution:
    existing = _find_identity(session, claims.issuer, claims.subject)
    if existing is not None:
        user = session.get(User, existing.user_id)
        if user is None or user.status == UserStatus.CLOSED:
            # Defence in depth: closure deletes the identity row outright, so
            # this should not be reachable.
            return Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)
        # Safe to refresh whether or not claims.issuer is trusted:
        # `_find_candidate_user_ids` checks this identity's own issuer before
        # any other user can auto-link against it, so an untrusted issuer
        # asserting email_verified=True creates no usable auto-link target.
        identity_changed = (
            existing.email != claims.email or existing.email_verified != claims.email_verified
        )
        name_changed = claims.display_name is not None and user.display_name != claims.display_name
        existing.email = claims.email
        existing.email_verified = claims.email_verified
        if identity_changed:
            # record_change refuses an empty diff (AuditNoOpError), so an
            # ordinary repeat login must skip it.
            record_change(
                session,
                audit,
                action="user_identity.refreshed",
                instance=existing,
                kind=ChangeKind.UPDATED,
            )
        # Assign `user.display_name` after the identity's record_change, not
        # before: append_audit_event flushes the session, and a flush discards
        # the attribute history diff_instance needs, so this event's own diff
        # would be empty (AuditNoOpError). close_account follows the same order.
        if name_changed:
            user.display_name = claims.display_name
            record_change(
                session,
                audit,
                action="user.renamed",
                instance=user,
                kind=ChangeKind.UPDATED,
            )
        return Resolution(outcome=LinkOutcome.EXISTING, user=user)

    candidate_user_ids = _find_candidate_user_ids(session, claims.email, trusted_issuers)
    if not candidate_user_ids:
        user = _create_user(session, claims, audit=audit)
        return Resolution(outcome=LinkOutcome.CREATED, user=user)

    if len(candidate_user_ids) > 1 or not may_auto_link(claims, trusted_issuers):
        return Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)

    candidate_user_id = candidate_user_ids[0]
    identity = UserIdentity(
        user_id=candidate_user_id,
        issuer=claims.issuer,
        subject=claims.subject,
        email=claims.email,
        email_verified=claims.email_verified,
    )
    session.add(identity)
    record_change(
        session,
        audit,
        action="user_identity.created",
        instance=identity,
        kind=ChangeKind.CREATED,
    )
    user = session.get(User, candidate_user_id)
    if user is None:
        # Not reachable: candidate_user_id came from a join against app_user in
        # this transaction. Raising keeps `Resolution(AUTO_LINKED, user=None)`,
        # which the type says cannot occur, from reaching the caller.
        raise AssertionError(f"candidate user {candidate_user_id} vanished mid-resolution")
    return Resolution(outcome=LinkOutcome.AUTO_LINKED, user=user)


def close_account(session: Session, user_id: uuid.UUID, audit: AuditContext) -> None:
    """Pseudonymises the user and removes every linked identity (NFR-17).

    Never deletes the ``app_user`` row: the privilege grants in migration 0003
    make that structurally impossible. Idempotent: closing an already-closed
    account is a no-op and emits no audit event. The early return happens
    *before* the audit layer because ``nptc.audit.recording.record_change``
    refuses an empty diff, so a genuinely idempotent path must not rely on the
    diff coming back empty.

    Emits one ``user_identity.deleted`` event per linked identity, then a
    single ``user.closed`` event (NFR-08, NFR-10; see
    ``docs/adr/0018-field-level-audit-diffing.md``). The events never carry
    the identifying values (NFR-26, NFR-35): each model's
    ``__audit_withheld_fields__`` guarantees that, not this function.
    ``audit_event`` is INSERT/SELECT-only for the app role (NFR-09), so
    anything written into ``before`` is permanent.

    Identities and grants are deleted one row at a time through the ORM, not a
    bulk ``DELETE``, so ``record_change`` can read each row's attribute history
    before it disappears.

    **FR-01**: closure is a role-removal path even though it never calls
    ``nptc.auth.grants.revoke_role``, so ``assert_not_last_administrator`` runs
    first. Otherwise closing your own account would bypass "the system MUST
    prevent removal of the last remaining administrator". Every ``user_role``
    grant is then revoked: a closed user has no identities left to
    authenticate with, so a lingering grant would be unreachable but not gone,
    which is the state FR-01's guard must not be fooled by.
    """
    user = session.get(User, user_id)
    if user is None or user.status == UserStatus.CLOSED:
        return

    assert_not_last_administrator(session, removing_user_id=user_id)

    identities = (
        session.execute(select(UserIdentity).where(UserIdentity.user_id == user_id)).scalars().all()
    )
    for identity in identities:
        record_change(
            session,
            audit,
            action="user_identity.deleted",
            instance=identity,
            kind=ChangeKind.DELETED,
        )
        session.delete(identity)

    revoke_all_roles_unchecked(session, target_user_id=user_id, audit=audit)

    user.username = None
    user.display_name = None
    user.organisation = None
    user.status = UserStatus.CLOSED
    user.closed_at = datetime.now(UTC)

    record_change(
        session,
        audit,
        action="user.closed",
        instance=user,
        kind=ChangeKind.UPDATED,
    )


class UserRef(BaseModel):
    """The NFR-04 serialisation boundary: what any API response or export
    is allowed to say about a user. There is no ``id`` field, ever: the
    internal UUID must not escape past this type, so a route that returns a
    ``UserRef`` cannot leak it.
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
