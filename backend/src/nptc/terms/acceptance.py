"""Reading and appending a user's terms acceptances (NFR-45, NFR-08, ADR-0043).

Every function takes a `Session` explicitly and commits nothing: the request transaction owns
that, so the acceptance row and its audit event commit together.

A user has accepted the current version only when the *latest* row names exactly that version.
This is equality, not order, so a version rolled back to an earlier one asks for acceptance
again, and a user's earlier rows are history that never counts.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.db.models.terms_acceptance import TermsAcceptance
from nptc.terms.errors import TermsVersionStaleError

#: The audit action for a new acceptance row (NFR-08).
ACCEPTANCE_CREATED_ACTION = "terms_acceptance.created"


def latest_acceptance(session: Session, user_id: uuid.UUID) -> TermsAcceptance | None:
    """The user's most recent acceptance, or `None` if they never accepted anything."""
    return session.execute(
        select(TermsAcceptance)
        .where(TermsAcceptance.user_id == user_id)
        .order_by(TermsAcceptance.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def has_accepted(session: Session, user_id: uuid.UUID, *, current_version: str) -> bool:
    latest = latest_acceptance(session, user_id)
    return latest is not None and latest.version == current_version


def accept_terms(
    session: Session,
    audit: AuditContext,
    *,
    user_id: uuid.UUID,
    version: str,
    current_version: str,
) -> TermsAcceptance:
    """Records that `user_id` accepted `version`, which must be the current version.

    Raises `TermsVersionStaleError` for any other version: the user was shown a text that is no
    longer current, and recording it as the current one would claim they read a text they never
    saw (NFR-47). Accepting a version the user's latest row already names returns that row and
    writes nothing, so a repeated request leaves one row and one audit event, including when two
    requests arrive together: the audit append lock is taken before the read, as the catalogue
    writers do (ADR-0035), so the second request waits and then sees the first one's row.
    """
    if version != current_version:
        raise TermsVersionStaleError(submitted_version=version, current_version=current_version)

    acquire_append_lock(session)
    latest = latest_acceptance(session, user_id)
    if latest is not None and latest.version == version:
        return latest

    acceptance = TermsAcceptance(user_id=user_id, version=version)
    session.add(acceptance)
    record_change(
        session,
        audit,
        action=ACCEPTANCE_CREATED_ACTION,
        instance=acceptance,
        kind=ChangeKind.CREATED,
    )
    return acceptance
