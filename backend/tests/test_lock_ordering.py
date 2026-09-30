"""Regression tests for issue #281: every catalogue-entry writer acquires
the audit append lock (`nptc.audit.writer.acquire_append_lock`) before it
can take either a `catalogue_entry` row lock or the collision-key advisory
lock (`nptc.catalogue.collisions.assert_no_error_collisions`) - as the
first session-touching statement of the function, before any other
operation that reads or writes the database. Only `_SESSION_FREE_PRECHECKS`
(validation that never sees a session) may run ahead of it, and a writer
that takes a `reason` must validate it there, so a rejected request takes
no lock. (Round-2 review: a narrower placement, taking the lock only once
some earlier precondition had already passed, still left an ORM `select()`
or two in between - each of which autoflushes any already-pending
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

A third test, `test_acquire_append_lock_precedes_every_session_touching_statement`,
is a pure-`ast` guard proving the invariant the two concurrency tests above
rely on: it does not itself prove absence of a deadlock (that is what the
concurrency tests are for), but it does mean a future change that moves
`acquire_append_lock` later in one of the functions it checks - which
would make the concurrency tests above unreliable again, not merely
slower, since their determinism depends on both racing sides reaching the
same lock first - fails immediately and specifically, rather than only
occasionally as a flaky concurrency test.

**That guard's own checklist is derived from source, not hand-maintained**
(round-2 review: a hardcoded tuple of "the five functions this issue
happens to touch" would silently miss a *new* writer added later to one of
these modules - the same class of gap the issue itself is about, just
moved into the regression test). See `_derive_required_functions`'s own
docstring for the scan and its scope.
"""

from __future__ import annotations

import ast
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

import nptc.catalogue.designations as designations_module
import nptc.catalogue.entries as entries_module
import nptc.catalogue.property_values as property_values_module
from nptc.audit.writer import AUDIT_APPEND_LOCK_KEY, AuditContext
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.designations import (
    add_designation,
    add_synonyms,
    amend_designation,
    reinstate_designation,
    retire_designation,
)
from nptc.catalogue.entries import (
    EntryChanges,
    create_entry,
    entry_child_write,
    save_entries,
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
from nptc.db.models.designation import Designation
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc_shared.terminology.stub import StubTerminologyClient

#: The three files issue #281's two lock-ordering cycles concern - every
#: function whose body calls `assert_no_error_collisions` (the collision
#: lock), mutates `row_version`, or calls `bump_entry_row_version`/
#: `record_change`/`record_snapshot_change` (which can flush a pending
#: `catalogue_entry` row-version mutation) lives in one of these.
#:
#: Not every module under `nptc.catalogue`: `bindings.py` (`code_binding`)
#: and `local_codes.py` (`local_code`/`local_code_system`) each write a
#: different table with no collision-key lock of their own - neither ever
#: calls `assert_no_error_collisions` - and `bindings.py`'s own row-lock-
#: vs-append-lock ordering was already closed by issue #60's
#: `entry_child_write`, which this scan's own `entries.py` file already
#: covers. Extending this same derived-guard treatment to those two
#: modules would be a reasonable follow-up, but it is a different module's
#: own review history, not this issue's.
_SCAN_FILES: tuple[Path, ...] = (
    Path(entries_module.__file__),
    Path(property_values_module.__file__),
    Path(designations_module.__file__),
)

#: `bump_entry_row_version` itself directly does `entry.row_version += 1`,
#: which is exactly what `_assigns_row_version` looks for - but it is a
#: single-purpose private helper `entry_child_write` calls *after* already
#: acquiring the append lock (see its own docstring: "Called from inside
#: `entry_child_write` on a clean exit"), never a top-level writer callable
#: on its own, so requiring it to also acquire the lock itself would be
#: requiring a redundant call inside code that is already inside the lock's
#: scope by construction - the one documented, named exemption this guard
#: allows, matching `test_sql_parameterisation.py`'s own convention for a
#: justified carve-out rather than a silent gap.
_EXEMPT_FUNCTIONS = frozenset({"bump_entry_row_version"})

#: A direct call to any of these, anywhere in a function's body, means that
#: function can take a lock `acquire_append_lock` must precede -
#: `assert_no_error_collisions`'s own collision-key lock, or (via
#: `bump_entry_row_version`, or `record_change`/`record_snapshot_change`'s
#: own internal flush) the implicit `catalogue_entry` row lock.
_DIRECT_TRIGGER_CALL_NAMES = frozenset(
    {
        "assert_no_error_collisions",
        "record_change",
        "record_snapshot_change",
        "bump_entry_row_version",
    }
)


def _call_names(func_def: ast.FunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(func_def)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _assigns_row_version(func_def: ast.FunctionDef) -> bool:
    """Whether `func_def`'s body directly sets some `<object>.row_version`
    - `entry.row_version += 1` inside `bump_entry_row_version`, and
    `save_property_values`'s identical inline bump, are what this catches;
    a *call* to `bump_entry_row_version` is caught by `_call_names`
    instead, not this."""
    for node in ast.walk(func_def):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Attribute) and t.attr == "row_version" for t in targets):
            return True
    return False


#: Calls a writer may make before `acquire_append_lock` because none of them
#: reads or writes the database: a request rejected by one of them then takes
#: no lock. Adding a name here is a review decision - confirm the function
#: never touches a session, directly or through an argument.
_SESSION_FREE_PRECHECKS = frozenset(
    {"validate_changelog_note", "clean_term", "validate_language_tag"}
)


def _precheck_call_name(stmt: ast.stmt) -> str | None:
    """The allowed precheck `stmt` calls, or `None` if it is anything else.

    Accepts `name = check(...)`, a bare `check(...)`, and `if reason is not
    None: check(...)` (`entry_child_write`'s optional note) - each only when
    no argument names `session`."""
    if isinstance(stmt, ast.If):
        test = stmt.test
        guarded_by_reason = (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "reason"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
        )
        if not guarded_by_reason or stmt.orelse or len(stmt.body) != 1:
            return None
        return _precheck_call_name(stmt.body[0])
    if isinstance(stmt, ast.Assign | ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
    else:
        return None
    if not (isinstance(call.func, ast.Name) and call.func.id in _SESSION_FREE_PRECHECKS):
        return None
    if any(isinstance(n, ast.Name) and n.id == "session" for n in ast.walk(call)):
        return None
    return call.func.id


def _is_acquire_append_lock_call(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "acquire_append_lock"
    )


def _pre_lock_prechecks(func_def: ast.FunctionDef) -> list[str] | None:
    """The `_SESSION_FREE_PRECHECKS` `func_def` runs before its bare
    `acquire_append_lock(<something>)` call, after skipping a leading
    docstring; `None` if any other statement precedes the lock, or the lock
    is never taken - syntactic, like `test_datatype_dispatch.py`'s own
    guard: it checks the called name, not which module a given
    `acquire_append_lock` was imported from."""
    body = func_def.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    prechecks: list[str] = []
    for stmt in body:
        if _is_acquire_append_lock_call(stmt):
            return prechecks
        name = _precheck_call_name(stmt)
        if name is None:
            return None
        prechecks.append(name)
    return None


def _acquires_lock_before_session_use(func_def: ast.FunctionDef) -> bool:
    return _pre_lock_prechecks(func_def) is not None


def _validates_reason_before_lock(func_def: ast.FunctionDef) -> bool:
    """A writer taking a `reason` must validate it ahead of the lock, so a
    rejected note takes no lock. Vacuously true without a `reason`
    parameter."""
    takes_reason = any(arg.arg == "reason" for arg in func_def.args.kwonlyargs + func_def.args.args)
    if not takes_reason:
        return True
    prechecks = _pre_lock_prechecks(func_def)
    return prechecks is not None and "validate_changelog_note" in prechecks


def _derive_required_functions() -> dict[str, ast.FunctionDef]:
    """Every function, across `_SCAN_FILES`, that must acquire the append
    lock before anything else - derived from source rather than hand-
    listed, so a new writer added to one of these three modules is caught
    automatically rather than silently sitting outside a maintained
    allowlist (round-2 review).

    Two passes: a function is in the **base** set if `_DIRECT_TRIGGER_
    CALL_NAMES`/`_assigns_row_version` finds it taking a contended lock
    itself; a **closure** pass then repeatedly adds any function that
    calls one already in the set - `save_property_values_for_entries`,
    `save_entries` and `add_synonyms` are each a per-entry/per-term loop
    around a base-set function and so join through this pass, not the
    first one. Every function this scan finds - base or closure - is
    required to itself satisfy `_acquires_lock_before_session_use`,
    never merely "the call it delegates to satisfies it": that keeps this
    check a flat, one-shape rule (see `add_synonyms`'s and `save_entries`'
    own docstrings for why each still calls it directly despite every
    per-item call already re-asserting the same lock) rather than one that
    also has to reason about whether a delegator's own preamble could
    autoflush before it ever reaches its first delegate call - the exact
    class of gap round-2 review found in the narrower "before the row/
    collision lock" placement this issue's fix moved away from.
    """
    func_defs: dict[str, ast.FunctionDef] = {}
    for path in _SCAN_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_defs[node.name] = node

    required = {
        name
        for name, func_def in func_defs.items()
        if _call_names(func_def) & _DIRECT_TRIGGER_CALL_NAMES or _assigns_row_version(func_def)
    }

    changed = True
    while changed:
        changed = False
        for name, func_def in func_defs.items():
            if name not in required and _call_names(func_def) & required:
                required.add(name)
                changed = True

    return {name: func_defs[name] for name in required - _EXEMPT_FUNCTIONS}


def test_acquire_append_lock_precedes_every_session_touching_statement() -> None:
    """Enforces the invariant `test_bulk_and_singular_property_writes_do_
    not_deadlock` and `test_save_entry_and_entry_child_write_do_not_
    deadlock_on_the_same_collision_key` below both rely on for their own
    determinism (see the module docstring): every function `_derive_
    required_functions` finds must call `acquire_append_lock` before any
    statement that could touch the session, not merely "early" or "before
    its own collision/row lock". A placement that is still correct by that
    looser reading (e.g. after a no-op precondition check) would make those
    two tests flaky rather than reliably red, which is a worse failure mode
    than this guard failing loudly and immediately. Only
    `_SESSION_FREE_PRECHECKS` may run first."""
    violations = sorted(
        name
        for name, func_def in _derive_required_functions().items()
        if not _acquires_lock_before_session_use(func_def)
    )
    assert violations == []


def test_reason_is_validated_before_the_append_lock() -> None:
    """The converse of the guard above: forbidding session use before the
    lock does not force validation ahead of it, so a writer could move
    `validate_changelog_note` back below the lock and still pass."""
    violations = sorted(
        name
        for name, func_def in _derive_required_functions().items()
        if not _validates_reason_before_lock(func_def)
    )
    assert violations == []


def _parse_function(source: str) -> ast.FunctionDef:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


@pytest.mark.req("NFR-08")
def test_guard_accepts_session_free_prechecks_before_the_lock() -> None:
    func_def = _parse_function(
        "def write(session, reason):\n"
        '    """Docstring."""\n'
        "    validated_reason = validate_changelog_note(reason)\n"
        "    acquire_append_lock(session)\n"
    )
    assert _acquires_lock_before_session_use(func_def)


@pytest.mark.req("NFR-08")
@pytest.mark.parametrize(
    "body",
    [
        "    session.execute(query)\n    acquire_append_lock(session)\n",
        "    entry = load_entry_for_update(session, key)\n    acquire_append_lock(session)\n",
        "    value = clean_term(session.scalar(query))\n    acquire_append_lock(session)\n",
        "    other = some_helper(reason)\n    acquire_append_lock(session)\n",
        "    validated_reason = validate_changelog_note(reason)\n",
    ],
    ids=[
        "session-call",
        "session-argument-helper",
        "precheck-reading-session",
        "unlisted-call",
        "lock-never-taken",
    ],
)
def test_guard_rejects_session_use_before_the_lock(body: str) -> None:
    assert not _acquires_lock_before_session_use(_parse_function(f"def write(session):\n{body}"))


@pytest.mark.req("NFR-08")
def test_guard_accepts_a_reason_guarded_precheck_before_the_lock() -> None:
    func_def = _parse_function(
        "def write(session, *, reason=None):\n"
        "    if reason is not None:\n"
        "        validate_changelog_note(reason)\n"
        "    acquire_append_lock(session)\n"
    )
    assert _validates_reason_before_lock(func_def)


@pytest.mark.req("NFR-08")
@pytest.mark.parametrize(
    "body",
    [
        "    acquire_append_lock(session)\n    validated = validate_changelog_note(reason)\n",
        "    acquire_append_lock(session)\n",
        "    cleaned = clean_term(term)\n    acquire_append_lock(session)\n",
    ],
    ids=["validated-after-the-lock", "never-validated", "other-precheck-only"],
)
def test_guard_requires_reason_validation_before_the_lock(body: str) -> None:
    func_def = _parse_function(f"def write(session, term, reason):\n{body}")
    assert not _validates_reason_before_lock(func_def)


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


# --- Rejected input takes no lock ------------------------------


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _captured_lock_statements(connection: Connection, action: Callable[[], object]) -> list[str]:
    """Runs `action` and returns the audit append lock statements it issued.

    Matched on the append lock's own key, because
    `assert_no_error_collisions` takes a second `pg_advisory_xact_lock`
    keyed per term."""
    statements: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if (
            "pg_advisory_xact_lock" in statement
            and isinstance(parameters, dict)
            and parameters.get("key") == AUDIT_APPEND_LOCK_KEY
        ):
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", _record)
    try:
        action()
    finally:
        event.remove(connection, "before_cursor_execute", _record)
    return statements


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_a_valid_note_takes_the_append_lock(app_session: Session, app_db: Connection) -> None:
    """Control for the test below: proves the capture sees the append lock
    at all, so an empty result there cannot be a listener that never fires.
    `create_entry` also takes the collision lock, which the capture must not
    count."""
    statements = _captured_lock_statements(
        app_db,
        lambda: create_entry(
            app_session,
            AuditContext.system(),
            preferred_term=f"Lock control entry {uuid.uuid4()}",
            reason="Created to prove the lock statement is captured",
        ),
    )
    assert len(statements) >= 1


def _call_add_designation(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return add_designation(
        session, AuditContext.system(), entry=entry, term="Synonym", use="synonym", reason=reason
    )


def _call_add_synonyms(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return add_synonyms(
        session, AuditContext.system(), entry=entry, terms=["Synonym"], reason=reason
    )


def _call_create_entry(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return create_entry(
        session, AuditContext.system(), preferred_term="Never created", reason=reason
    )


def _call_save_entry(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return save_entry(
        session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Never saved"),
        reason=reason,
    )


def _call_save_property_values(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return save_property_values(
        session,
        AuditContext.system(),
        entry=entry,
        property_key="any_property",
        values=_inputs("value"),
        reason=reason,
        registry=_registry(session),
        expected_row_version=entry.row_version,
    )


def _call_save_property_values_for_entries(
    session: Session, entry: CatalogueEntry, reason: str
) -> object:
    return save_property_values_for_entries(
        session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(
                business_key=entry.business_key, expected_row_version=entry.row_version
            )
        ],
        property_key="any_property",
        values=_inputs("value"),
        reason=reason,
        registry=_registry(session),
    )


def _unsaved_designation(entry: CatalogueEntry) -> Designation:
    """Never flushed: the note check must reject before the writer reads it."""
    return Designation(entry_id=entry.id, term="Synonym", use="synonym", language="en")


def _call_amend_designation(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return amend_designation(
        session,
        AuditContext.system(),
        entry=entry,
        designation=_unsaved_designation(entry),
        new_term="Amended",
        reason=reason,
    )


def _call_retire_designation(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return retire_designation(
        session, AuditContext.system(), designation=_unsaved_designation(entry), reason=reason
    )


def _call_reinstate_designation(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return reinstate_designation(
        session,
        AuditContext.system(),
        entry=entry,
        designation=_unsaved_designation(entry),
        reason=reason,
    )


def _call_save_entries(session: Session, entry: CatalogueEntry, reason: str) -> object:
    return save_entries(
        session,
        AuditContext.system(),
        updates=[(entry.business_key, entry.row_version, EntryChanges(preferred_term="Never"))],
        reason=reason,
    )


def _call_entry_child_write(session: Session, entry: CatalogueEntry, reason: str) -> object:
    with entry_child_write(session, entry, entry.row_version, reason=reason):
        pass
    return None


@pytest.mark.req("FR-37")
@pytest.mark.integration
@pytest.mark.parametrize(
    "writer",
    [
        _call_add_designation,
        _call_add_synonyms,
        _call_amend_designation,
        _call_create_entry,
        _call_entry_child_write,
        _call_reinstate_designation,
        _call_retire_designation,
        _call_save_entries,
        _call_save_entry,
        _call_save_property_values,
        _call_save_property_values_for_entries,
    ],
    ids=lambda writer: writer.__name__.removeprefix("_call_"),
)
def test_a_rejected_changelog_note_takes_no_append_lock(
    writer: Callable[[Session, CatalogueEntry, str], object],
    app_session: Session,
    app_db: Connection,
) -> None:
    """A request the note check rejects must not queue on the global audit
    lock: it will never write, so holding the lock only delays writers that
    will. Asserts on the statements this test itself issues,
    never on the state of the audit table."""
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term=f"Rejected note entry {uuid.uuid4()}",
        reason="Created for the rejected-note lock test",
    )

    def _rejected() -> None:
        with pytest.raises(ChangelogNoteError):
            writer(app_session, entry, "")

    assert _captured_lock_statements(app_db, _rejected) == []
