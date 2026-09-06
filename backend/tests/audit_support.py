"""Shared audit-event lookup helper (issue #61 review round 2).

Not a test module itself (no ``test_`` prefix, so pytest never collects it) -
loaded by file path via each caller's own ``_load`` helper, the same
convention ``audit_privilege_support.py`` and the other ``*_support.py``
modules already use, since ``backend/tests`` has no ``__init__.py`` and
pytest runs with ``--import-mode=importlib``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.db.models.audit import AuditEvent


def latest_audit_event(
    session: Session, *, entity_type: str, entity_id: uuid.UUID | str
) -> AuditEvent:
    """The most recent `AuditEvent` for one entity - scoped by `entity_type`
    and `entity_id`, never a whole-table read, so this cannot pick up
    another test's row in the shared session-scoped container (issue #190,
    CLAUDE.md's own testing convention).

    `entity_id` accepts a `UUID` or a `str` since callers hold it either way
    - an ORM instance's own `.id` (a `UUID`), or one just read back via
    `session.execute(select(...)).scalar_one()` (whatever that column's
    Python type is) - and `AuditEvent.entity_id` is itself always a string
    column (FR-06's string-end-to-end rule extends to every identifier this
    module writes, not only SCTIDs), so the comparison always coerces to
    `str` here rather than asking every caller to remember to.
    """
    return session.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == str(entity_id))
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).scalar_one()
