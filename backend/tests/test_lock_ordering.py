"""Regression tests for issue #281: every catalogue-entry writer acquires
the audit append lock (`nptc.audit.writer.acquire_append_lock`) before it
can take either a `catalogue_entry` row lock or the collision-key advisory
lock (`nptc.catalogue.collisions.assert_no_error_collisions`).

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
  body, which can reach a collision-key lock via `add_designation`/`amend_
  designation` (issue #300's designation routes). Before this issue,
  `save_entry`/`create_entry` took the collision lock first and the append
  lock only via `record_change`, afterwards - the reverse order, against a
  different lock pair.

**Why these tests pin the interleaving with a monkeypatched delay, unlike
`test_catalogue_collisions.py`'s own barrier-only concurrency tests.**
Both cycles here involve two call paths with genuinely different amounts
of work before each one reaches its first lock - unlike, say, `add_
synonyms`' own opposite-lock-order test, where both racing calls run the
*same* function and so reach each lock at nearly the same relative time.
Verified empirically (against a deliberately reverted pre-#281 checkout):
a `threading.Barrier`-only version of each test below - releasing both
threads together and trusting real scheduling jitter to produce contention
- passed cleanly dozens of times in a row even against the unfixed code,
because the faster side simply finishes before the slower side ever
attempts the contended lock. A short, deterministic delay - injected via
`monkeypatch` into the exact `acquire_append_lock` call each production
code path already makes, so the delay only pins *when* a real
`pg_advisory_xact_lock`/row-lock attempt happens, never *whether* one
does - reliably forces the actual overlap, and was confirmed (same
reverted checkout) to reproduce a genuine Postgres `40P01` deadlock. Both
tests below use two genuine Postgres sessions/threads (`app_engine`,
matching `test_catalogue_collisions.py`'s own "FR-05: concurrency"
pattern) - a single session exercising the same code path twice cannot
reproduce a cross-transaction lock cycle at all.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

import nptc.catalogue.entries as entries_mod
import nptc.catalogue.property_values as property_values_mod
from nptc.audit.writer import AuditContext
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.designations import add_designation
from nptc.catalogue.entries import EntryChanges, create_entry, entry_child_write, save_entry
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

#: How long the thread that already holds the contended lock sleeps before
#: proceeding - long enough, in practice, for the other thread's own
#: (much shorter) preamble to reach and take the *other* lock in the pair,
#: on ordinary CI/dev hardware. Not tied to any production timing - purely
#: a test-harness knob to force the overlap deterministically (see the
#: module docstring).
_FORCED_OVERLAP_DELAY_SECONDS = 0.2


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
    pristine_audit_event: None,
    app_engine: Engine,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
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

    `save_property_values` now takes the append lock before that flush, so
    both writers agree on the order regardless of which runs first:
    whichever acquires the append lock first has not yet taken any row
    lock the other could be waiting on, so there is nothing left to cycle
    on - the two can only ever serialise, never deadlock. The bulk writer
    (forced, via the monkeypatch below, to hold the append lock across the
    singular writer's whole attempt - see the module docstring) simply
    finishes first; the singular writer then either applies cleanly or
    hits a genuine, expected version conflict against the entry bulk just
    changed - either is the correct outcome of real contention, not the
    deadlock this test rules out.
    """
    original_acquire_append_lock = property_values_mod.acquire_append_lock
    bulk_holds_append_lock = threading.Event()

    def _acquire_and_hold_for_bulk(session: Session) -> None:
        original_acquire_append_lock(session)
        if threading.current_thread().name == "bulk":
            bulk_holds_append_lock.set()
            time.sleep(_FORCED_OVERLAP_DELAY_SECONDS)

    monkeypatch.setattr(property_values_mod, "acquire_append_lock", _acquire_and_hold_for_bulk)

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

    results: dict[str, str] = {}
    errors: dict[str, BaseException] = {}

    def _bulk() -> None:
        session = Session(app_engine)
        try:
            registry = _registry(session)
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
            # Waits for the bulk writer to already hold the append lock,
            # rather than racing to start at the same time - see the module
            # docstring: this test targets *whether a genuine hold-and-wait
            # cycle can form*, not who wins an unforced footrace.
            bulk_holds_append_lock.wait(timeout=5)
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
    pristine_audit_event: None,
    app_engine: Engine,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
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
    `save_entry` now takes the append lock before its own collision check
    (this issue's fix), so both paths agree on the order: whichever gets
    the append lock first is the only one that can reach the collision
    lock at all - real contention on the collision key itself still
    resolves as a genuine FR-05 collision for whichever side loses (both
    are, after all, trying to record the same term), never as a deadlock.
    """
    original_acquire_append_lock = entries_mod.acquire_append_lock
    add_synonym_holds_append_lock = threading.Event()

    def _acquire_and_hold_for_add_synonym(session: Session) -> None:
        original_acquire_append_lock(session)
        if threading.current_thread().name == "add_synonym":
            add_synonym_holds_append_lock.set()
            time.sleep(_FORCED_OVERLAP_DELAY_SECONDS)

    monkeypatch.setattr(entries_mod, "acquire_append_lock", _acquire_and_hold_for_add_synonym)

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

    results: dict[str, str] = {}
    errors: dict[str, BaseException] = {}

    def _rename() -> None:
        session = Session(app_engine)
        try:
            # Waits for the designation-add thread to already hold the
            # append lock, rather than racing to start at the same time -
            # see the module docstring.
            add_synonym_holds_append_lock.wait(timeout=5)
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
    thread_add.start()
    thread_rename.start()
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
