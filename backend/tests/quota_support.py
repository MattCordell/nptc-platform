"""Seeds stored submissions for the quota tests (FR-43).

Not a test module itself (no ``test_`` prefix, so pytest never collects it); loaded by file path
through each caller's own ``_load`` helper, like ``audit_support.py``.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import Submission, SubmissionKind


def add_submissions(
    session: Session,
    user_id: uuid.UUID,
    count: int,
    *,
    kind: SubmissionKind = SubmissionKind.NEW_TEST,
    age: timedelta = timedelta(0),
) -> None:
    """Adds `count` stored submissions for `user_id`, each created `age` before the database's
    now. The age is taken from the database clock, never the test machine's, because the quota
    window is measured there."""
    created_at = session.execute(select(func.now())).scalar_one() - age
    entry_id = None
    if kind is SubmissionKind.AMENDMENT:
        entry = CatalogueEntry(
            business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
            preferred_term=f"Quota entry {uuid.uuid4()}",
            status="active",
        )
        session.add(entry)
        session.flush()
        entry_id = entry.id
    is_new_test = kind is SubmissionKind.NEW_TEST
    for _ in range(count):
        session.add(
            Submission(
                kind=kind.value,
                preferred_term=f"Quota submission {uuid.uuid4()}",
                submitter_id=user_id,
                entry_id=entry_id,
                reference_url="https://example.org/evidence" if is_new_test else None,
                reference_checked_at=created_at if is_new_test else None,
                reference_status=200 if is_new_test else None,
                created_at=created_at,
            )
        )
    session.flush()
