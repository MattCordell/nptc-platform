"""Shared audit_event refusal helpers (issue #35).

Not a test module itself (no ``test_`` prefix, so pytest never collects
it) - imported by ``test_db_audit_privileges.py`` and
``test_db_round_trip.py`` the same way those files import ``conftest.py``'s
helpers: by file path, since ``backend/tests`` has no ``__init__.py`` and
pytest runs with ``--import-mode=importlib``.

The same rationale as ``nptc.db.roles``'s own docstring applies here: one
definition of "what counts as a refused write", imported by both the
fresh-database tests and the post-round-trip tests, so the grant asserted
in each case is literally the same assertion rather than two copies that
could drift apart.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ProgrammingError

from nptc.audit.hashing import GENESIS_HASH

INSUFFICIENT_PRIVILEGE = "42501"

#: A fixed, valid-shape (64 lowercase hex) placeholder pair for prev_hash/
#: entry_hash - these are privilege-refusal tests (NFR-09), not hash-chain
#: tests, so the literal need not satisfy the real digest, only the
#: NOT NULL + CHECK constraints. GENESIS_HASH is reused for prev_hash;
#: entry_hash is distinct from it and from test_auth_account_closure.py's
#: own placeholder so a shared cluster/table never sees a UNIQUE collision.
_PLACEHOLDER_ENTRY_HASH = "f" * 64

_INSERT_ONE_ROW = text(
    "INSERT INTO audit_event "
    "(correlation_id, action, entity_type, entity_id, prev_hash, entry_hash) "
    "VALUES (:correlation_id, :action, :entity_type, :entity_id, :prev_hash, :entry_hash) "
    "RETURNING id, sequence"
)


def insert_one_row(connection: Connection) -> None:
    connection.execute(
        _INSERT_ONE_ROW,
        {
            "correlation_id": str(uuid.uuid4()),
            "action": "test.action",
            "entity_type": "test_entity",
            "entity_id": "1",
            "prev_hash": GENESIS_HASH,
            "entry_hash": _PLACEHOLDER_ENTRY_HASH,
        },
    )


def assert_refused(connection: Connection, statement: str) -> None:
    """A privilege error aborts the surrounding transaction, so callers must
    give this the *last* statement they intend to run against `connection`."""
    with pytest.raises(ProgrammingError) as exc_info:
        connection.execute(text(statement))

    assert exc_info.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
