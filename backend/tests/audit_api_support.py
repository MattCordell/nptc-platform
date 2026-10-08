"""Shared HTTP-test helpers for `nptc.api.routers.audit` (issue #286).

Not a `test_*.py` module - loaded by file path via each caller's own
`_load` helper, the same convention `audit_support.py` and the other
`*_support.py` modules already use (`backend/tests` has no `__init__.py`,
and pytest runs with `--import-mode=importlib`). Shared between
`test_api_audit_search.py` and `test_api_audit_export.py` rather than one
loading the other as a support module: a `test_*.py` file is also
collected and executed by pytest in its own right, so importing one from
another by file path (as this module's own callers do for *this* file)
would register a second, colliding entry under the same name in
`sys.modules` - exactly what the `_support.py` naming convention exists to
avoid.
"""

from __future__ import annotations

import uuid
from typing import Any

from nptc.audit.writer import AuditContext, append_audit_event
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User


def create_active_user(api: Any, username: str) -> User:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    api.session.add(user)
    api.session.flush()
    return user


def seed_event(
    api: Any,
    *,
    actor_user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str = "1",
    action: str = "test.action",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    event = append_audit_event(
        api.session,
        AuditContext(
            actor_user_id=actor_user_id,
            actor_ip=None,
            user_agent=None,
            correlation_id=uuid.uuid4(),
        )
        if actor_user_id is not None
        else AuditContext.system(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        reason=reason,
    )
    api.session.flush()
    return event
