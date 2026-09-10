"""Regression tests for issue #281: every catalogue-entry writer acquires
the audit append lock (`nptc.audit.writer.acquire_append_lock`) before it
can take either a `catalogue_entry` row lock or the collision-key advisory
lock (`nptc.catalogue.collisions.assert_no_error_collisions`) - as the
*literal first statement* of the function, before any other operation
(round-2 review: a narrower placement, taking the lock only once some
earlier precondition had already passed, still left an ORM `select()` or
two in between - each of which autoflushes any already-pending
`catalogue_entry` mutation by default, so a later-placed lock could still
be beaten by an earlier row lock depending on what the caller's session
already had pending).

Two independent lock-ordering cycles are closed by this issue - see its own
follow-up comments and ADR-0035's addendum:

- **Row lock vs. append lock**: a bulk write (`save_property_values_for_
  entries`) already acquires the append lock before its per-entry loop
  (issue #265). Before this issue, the singular writer (`save_property_
  values`) only acquired it via `record_snapshot_change`, *after* the
  `row_version` bump/flush that takes the implicit row lock - the reverse
  order.
- **Collision lock vs. append lock**: `nptc.catalogue.entries.entry_child_
  write` (issue #60) already takes the append lock before its wrapped
  body. Before this issue, `save_entry`/`create_entry`, and `add_
  designation`/`amend_designation` themselves, took the collision-key lock
  first and the append lock only via `record_change` - the reverse order,
  against a different lock pair. `add_designation`/`amend_designation` now
  take the append lock as their own first statement too, rather than
  relying on every caller wrapping them in `entry_child_write` correctly.

**Why a plain `threading.Barrier` is enough here, unlike an earlier version
of these tests.** Because the append lock is now unconditionally each
function's first statement, both sides of each race attempt the *same*
lock before touching anything else - there is no longer any preamble for
natural scheduling jitter to race through first. Releasing both threads
together and letting real Postgres contention resolve it is sufficient:
whichever thread's `acquire_append_lock` call wins holds it for its whole
operation, and the other blocks immediately, before it could have taken
any other lock to cycle against. (An earlier version of this fix placed
the lock later - after the row-version/no-op precondition, just before the
flush - and a barrier-only test against *that* code passed cleanly dozens
of times even without the fix, because the two racing call paths' earlier
preambles differed enough in length that the faster side simply finished
before the slower side ever reached the contended lock. That is what
prompted moving the lock to the literal first statement everywhere, rather
than only pinning the test's timing around a narrower placement.)

Both concurrency tests below use two genuine Postgres sessions/threads
(`app_engine`, matching `test_catalogue_collisions.py`'s own "FR-05:
concurrency" pattern) - a single session exercising the same code path
twice cannot reproduce a cross-transaction lock cycle at all. Neither
makes a whole-table assertion; `pristine_audit_event` is requested purely
to clean up the `audit_event` rows each test's real, committed writes
leave behind (see that fixture's own docstring) - not because either
assertion here depends on the table being empty.

A third test, `test_acquire_append_lock_is_the_literal_first_statement`,
is a pure-`ast` guard proving the invariant the two concurrency tests above
rely on: it does not itself prove absence of a deadlock (that is what the
concurrency tests are for), but it does mean a future change that moves
`acquire_append_lock` later in one of these five functions - which would
make the concurrency tests above unreliable again, not merely slower, since
their determinism depends on both racing sides reaching the same lock
first - fails immediately and specifically, rather than only occasionally
as a flaky concurrency test.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import threading
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nptc.audit.writer import AuditContext
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.designations import add_designation, amend_designation
from nptc.catalogue.entries import (
    EntryChanges,
    create_entry,
    entry_child_write,
    save_entry,
)
from nptc.catalogue.errors import EntryVersionConflictError
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import (
    EntryPropertyTarget,
    PropertyValueInput,
    save_property_values,
    save_property_values_for_entries,
)
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc_shared.terminology.stub import StubTerminologyClient

#: Every function this issue requires to acquire the append lock as its own
#: literal first statement (round-2 review) - `save_property_values_for_
#: entries` and `entry_child_write` already did before this issue and are
#: not re-checked here, since neither was ever the gap.
_MUST_ACQUIRE_APPEND_LOCK_FIRST: tuple[Callable[..., object], ...] = (
    create_entry,
    save_entry,
    save_property_values,
    add_designation,
    amend_designation,
)


def _first_statement_is_acquire_append_lock(func: Callable[..., object]) -> bool:
    """Whether `func`'s body, after skipping a leading docstring, starts
    with a bare `acquire_append_lock(<something>)` call - syntactic, like
    `test_datatype_dispatch.py`'s own guard, not a check against the
    imported name's identity (which module a given `acquire_append_lock`
    was imported from does not matter here)."""
    source = textwrap.dedent(inspect.getsource(func))
    module = ast.parse(source)
    (func_def,) = module.body
    assert isinstance(func_def, ast.FunctionDef)
    body = func_def.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body:
        return False
    first = body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Name)
        and first.value.func.id == "acquire_append_lock"
    )


def test_acquire_append_lock_is_the_literal_first_statement() -> None:
    """Enforces the invariant `test_bulk_and_singular_property_writes_do_
    not_deadlock` and `test_save_entry_and_entry_child_write_do_not_
    deadlock_on_the_same_collision_key` below both rely on for their own
    determinism (see the module docstring): each of these five functions
    must call `acquire_append_lock` before anything else, not merely
    "early" or "before its own collision/row lock". A placement that is
    still correct by that looser reading (e.g. after a no-op precondition
    check) would make those two tests flaky rather than reliably red, which
    is a worse failure mode than this guard failing loudly and immediately.
    """
    violations = [
        func.__qualname__
        for func in _MUST_ACQUIRE_APPEND_LOCK_FIRST
        if not _first_statement_is_acquire_append_lock(func)
    ]
    assert violations == []


def _inputs(*values: object) -> list[PropertyValueInput]:
    return [PropertyValueInput(value=value) for value in values]


def _registry(session: Session) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=StubTerminologyClient(),
                local_code_lookup=DatabaseLocalCodeLookup(session),
            )
        )
    )


def _new_string_property(session: Session, *, key: str) -> PropertyDefinition:
    definition = PropertyDefinition(
        key=key,
        label=key.replace("_", " ").title(),
        datatype="string",
        cardinality="0..*",
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={},
    )
    session.add(definition)
    session.flush()
    return definition


# --- Row lock vs. append lock (bulk vs. singular property writes) ----------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_bulk_and_singular_property_writes_do_not_deadlock(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> None:
    """The residual deadlock issue #281 closes: before this fix,
    `save_property_values` (the singular writer) only acquired the append
    lock inside `record_snapshot_change`, *after* the `row_version`
    bump/flush that takes the entry's implicit row lock - the reverse of
    the order `save_property_values_for_entries` (the bulk writer) already
    used for its own loop (issue #265). A bulk write already holding the
    append lock could block waiting for a row a concurrent singular writer
    already held, while that writer waited for the same append lock -
    Postgres's `40P01`, an occasional 500 under contention rather than the
    two writers simply serialising on whichever lock either takes first.

    `save_property_values` now takes the append lock as its own first
    statement, so both writers agree on the order regardless of which runs
    first: whichever wins holds the append lock for its whole operation,
    and the other blocks immediately, before it could have taken any row
    lock to cycle against. Whichever side loses the race then either
    applies cleanly (if it reads the entry before the winner's write) or
    hits a genuine, expected version conflict against the entry the winner
    just changed - either is the correct outcome of real contention, not
    the deadlock this test rules out.
    """
    property_key = f"race_property_{uuid.uuid4().hex}"
    setup_session = Session(app_engine)
    entry_b = create_entry(
        setup_session,
        AuditContext.system(),
        preferred_term=f"Bulk-vs-singular deadlock entry {uuid.uuid4()}",
        reason="Entry for issue #281 bulk-vs-singular deadlock test",
    )
    _new_string_property(setup_session, key=property_key)
    setup_session.commit()
    entry_b_id = entry_b.id
    entry_b_key, entry_b_version = entry_b.business_key, entry_b.row_version
    setup_session.close()

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: dict[str, BaseException] = {}

    def _bulk() -> None:
        session = Session(app_engine)
        try:
            registry = _registry(session)
            barrier.wait(timeout=5)
            save_property_values_for_entries(
                session,
                AuditContext.system(),
                targets=[
                    EntryPropertyTarget(
                        business_key=entry_b_key, expected_row_version=entry_b_version
                    )
                ],
                property_key=property_key,
                values=_inputs("bulk value"),
                reason="Bulk write racing a concurrent singular write (issue #281)",
                registry=registry,
            )
            session.commit()
            results["bulk"] = "ok"
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors["bulk"] = exc
        finally:
            session.close()

    def _singular() -> None:
        session = Session(app_engine)
        try:
            registry = _registry(session)
            entry_b_local = session.get_one(CatalogueEntry, entry_b_id)
            barrier.wait(timeout=5)
            save_property_values(
                session,
                AuditContext.system(),
                entry=entry_b_local,
                property_key=property_key,
                values=_inputs("singular value"),
                reason="Singular write racing a concurrent bulk write (issue #281)",
                registry=registry,
                expected_row_version=entry_b_version,
            )
            session.commit()
            results["singular"] = "ok"
        except (EntryVersionConflictError, StaleDataError):
            session.rollback()
            results["singular"] = "conflict"
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors["singular"] = exc
        finally:
            session.close()

    thread_bulk = threading.Thread(target=_bulk, name="bulk")
    thread_singular = threading.Thread(target=_singular, name="singular")
    thread_bulk.start()
    thread_singular.start()
    thread_bulk.join(timeout=15)
    thread_singular.join(timeout=15)

    try:
        assert not thread_bulk.is_alive(), "bulk writer did not finish - looks like a deadlock"
        assert not thread_singular.is_alive(), (
            "singular writer did not finish - looks like a deadlock"
        )
        if errors:
            _first_key, first_exc = next(iter(errors.items()))
            raise AssertionError(
                f"unexpected exception(s) in concurrent threads: {errors}"
            ) from first_exc
        # Bulk never raises on a per-target conflict (it reports one
        # instead), so it always reports "ok" here. The singular writer
        # reports "conflict" only if bulk's write to entry_b landed first.
        assert results.get("bulk") == "ok"
        assert results.get("singular") in {"ok", "conflict"}
    finally:
        with owner_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM property_value WHERE property_key = :key"),
                {"key": property_key},
            )
            connection.execute(
                text("DELETE FROM property_definition WHERE key = :key"), {"key": property_key}
            )
            connection.execute(
                text("DELETE FROM catalogue_entry WHERE id = :b"), {"b": str(entry_b_id)}
            )


# --- Collision lock vs. append lock -----------------------------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_save_entry_and_entry_child_write_do_not_deadlock_on_the_same_collision_key(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> None:
    """The second lock-ordering cycle issue #281 closes (see its
    2026-09-09 follow-up comment and ADR-0035's addendum). `save_entry`
    used to take `assert_no_error_collisions`'s collision-key advisory lock
    before the audit append lock, while a router wrapping a designation
    write in `entry_child_write` (issue #300) takes the append lock first
    and the collision lock second, inside the wrapped body - the reverse
    order, against the same pair of locks (append, collision) `save_entry`
    now also uses.

    A `save_entry` rename racing an `entry_child_write`-wrapped `add_
    designation` call on the *same* collision key is the same `40P01`
    cycle as the row-lock case, just against a different lock pair.
    `save_entry` and `add_designation` both now take the append lock as
    their own first statement, so both paths agree on the order:
    whichever wins holds it for its whole operation, and the other blocks
    immediately, before it could have taken the collision lock to cycle
    against. Real contention on the collision key itself still resolves as
    a genuine FR-05 collision for whichever side loses (both are, after
    all, trying to record the same term), never as a deadlock.
    """
    racing_term = f"Race collision-lock term {uuid.uuid4()}"
    setup_session = Session(app_engine)
    entry_x = create_entry(
        setup_session,
        AuditContext.system(),
        preferred_term=f"Rename target entry {uuid.uuid4()}",
        reason="Entry to rename for issue #281 collision-lock deadlock test",
    )
    entry_y = create_entry(
        setup_session,
        AuditContext.system(),
        preferred_term=f"Synonym target entry {uuid.uuid4()}",
        reason="Entry to add a synonym to for issue #281 collision-lock deadlock test",
    )
    setup_session.commit()
    entry_x_id, entry_x_key, entry_x_version = (
        entry_x.id,
        entry_x.business_key,
        entry_x.row_version,
    )
    entry_y_id, entry_y_version = entry_y.id, entry_y.row_version
    setup_session.close()

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: dict[str, BaseException] = {}

    def _rename() -> None:
        session = Session(app_engine)
        try:
            barrier.wait(timeout=5)
            save_entry(
                session,
                AuditContext.system(),
                business_key=entry_x_key,
                expected_row_version=entry_x_version,
                changes=EntryChanges(preferred_term=racing_term),
                reason="Renaming preferred term, racing a concurrent designation add",
            )
            session.commit()
            results["rename"] = "ok"
        except DesignationCollisionError:
            session.rollback()
            results["rename"] = "collision"
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors["rename"] = exc
        finally:
            session.close()

    def _add_synonym() -> None:
        session = Session(app_engine)
        try:
            entry_y_local = session.get_one(CatalogueEntry, entry_y_id)
            barrier.wait(timeout=5)
            with entry_child_write(session, entry_y_local, entry_y_version):
                add_designation(
                    session,
                    AuditContext.system(),
                    entry=entry_y_local,
                    term=racing_term,
                    use="synonym",
                    reason="Adding a synonym via entry_child_write, racing a concurrent rename",
                )
            session.commit()
            results["add_synonym"] = "ok"
        except DesignationCollisionError:
            session.rollback()
            results["add_synonym"] = "collision"
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors["add_synonym"] = exc
        finally:
            session.close()

    thread_rename = threading.Thread(target=_rename, name="rename")
    thread_add = threading.Thread(target=_add_synonym, name="add_synonym")
    thread_rename.start()
    thread_add.start()
    thread_rename.join(timeout=15)
    thread_add.join(timeout=15)

    try:
        assert not thread_rename.is_alive(), "rename did not finish - looks like a deadlock"
        assert not thread_add.is_alive(), "add_synonym did not finish - looks like a deadlock"
        if errors:
            _first_key, first_exc = next(iter(errors.items()))
            raise AssertionError(
                f"unexpected exception(s) in concurrent threads: {errors}"
            ) from first_exc
        assert sorted(results.values()) == ["collision", "ok"]
    finally:
        with owner_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM designation WHERE entry_id IN (:x, :y)"),
                {"x": str(entry_x_id), "y": str(entry_y_id)},
            )
            connection.execute(
                text("DELETE FROM catalogue_entry WHERE id IN (:x, :y)"),
                {"x": str(entry_x_id), "y": str(entry_y_id)},
            )
