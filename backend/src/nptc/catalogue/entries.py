"""The `catalogue_entry` service layer: `business_key` minting and the FR-38
optimistic-locking write path.

**No Core `sqlalchemy.update()`.** A Core-style update bypasses
`version_id_col`, so every concurrent editor's lock would be decoration
(ADR-0012). Every write here loads the mapped instance and lets the ORM issue
`UPDATE ... WHERE row_version = ...`. `test_sql_parameterisation.py`'s AST
guard enforces this for `catalogue_entry`.

**Two layers of conflict detection.** `save_entry` first checks
`expected_row_version` against the freshly loaded row before mutating
anything (`assert_entry_row_version`). That layer can build a useful
`ConflictReport`, because the caller's stale view and the current row are both
in hand. `version_id_col` is the backstop for two callers who both pass that
check and then interleave before the flush. It surfaces as `StaleDataError`
inside a `session.begin_nested()` savepoint, so only this entry's write rolls
back, which `save_entries`' one-savepoint-per-entry loop needs.
`entry_child_write` applies both layers to a table that keeps no
`row_version` of its own.

**A rejected save writes no audit event.** Layer one raises before
`record_change` is called. Layer two raises inside `append_audit_event`'s
flush, before it builds an `AuditEvent`. Each path has its own test in
`test_catalogue_optimistic_locking.py`; one passing proves nothing about the
other.

**FR-37.** `reason` is required and validated by `validate_changelog_note`
before the row is touched. The seeded-import path (ADR-0010) supplies
`SEED_IMPORT_NOTE`, which passes that validation rather than bypassing it.

Every writer takes the audit append lock before any session-touching
statement; see ADR-0035 and `test_lock_ordering.py`.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import policy_for
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.errors import (
    ConflictReport,
    EntryNotFoundError,
    EntryVersionConflictError,
    FieldConflict,
)
from nptc.catalogue.term_hygiene import clean_term, exceeds_maximum_length
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.designation import DesignationUse
from nptc.db.models.user import User
from nptc_shared.language import DEFAULT_LANGUAGE

#: The single Python source of truth for the FR-03 format. `CatalogueEntry`'s
#: CHECK constraint and migration 0006's sequence default mirror it; they are
#: not generated from it, because `test_sql_parameterisation.py` bans SQL
#: built from runtime data.
BUSINESS_KEY_PREFIX: Final[str] = "NPTC-"
BUSINESS_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^NPTC-([0-9]{6,})$")

#: Must match migration 0006_catalogue_entry.py (via `nptc.db.roles`).
BUSINESS_KEY_SEQUENCE_NAME: Final[str] = "catalogue_entry_business_key_seq"


def format_business_key(sequence_value: int) -> str:
    """`NPTC-` plus a 6-digit zero-padded sequence value (FR-03), for
    example `NPTC-000247`. Past 999999 the key widens; the database CHECK
    accepts it."""
    return f"{BUSINESS_KEY_PREFIX}{sequence_value:06d}"


def allocate_business_key(session: Session) -> str:
    """Mints the next `business_key` from the dedicated Postgres sequence.
    The one mint point for a genuinely new (non-seeded) entry - see
    `nptc.db.models.catalogue_entry`'s module docstring for why this lives
    in Python rather than a column `server_default`."""
    next_value: int = session.execute(
        text("SELECT nextval(:seq)"), {"seq": BUSINESS_KEY_SEQUENCE_NAME}
    ).scalar_one()
    return format_business_key(int(next_value))


def advance_sequence_past(session: Session, business_key: str) -> None:
    """Reconciles the minting sequence with a seeded baseline (ADR-0010):
    call once with the highest seeded key, so the next `allocate_business_key`
    mints a strictly greater one.

    A single statement, not read-then-compare. A fresh sequence reports
    `last_value = 1` before anything is dispensed, and only `is_called`
    separates that from "1 was issued", so reading `last_value` would skip the
    first reconciliation against `NPTC-000001`. `nextval() - 1` gives the highest
    dispensed value whatever `is_called` says, and one `setval` closes the
    read-then-write race. The cost is one skipped sequence value per call, which
    FR-03 allows: it forbids reuse, not gaps."""
    match = BUSINESS_KEY_PATTERN.match(business_key)
    if match is None:
        raise ValueError(f"{business_key!r} does not match the NPTC business_key format")
    numeric_value = int(match.group(1))

    session.execute(
        text("SELECT setval(:seq, GREATEST(nextval(:seq) - 1, :value), true)"),
        {"seq": BUSINESS_KEY_SEQUENCE_NAME, "value": numeric_value},
    )


@dataclass(frozen=True)
class EntryChanges:
    """Fields a save may change. `business_key` has deliberately no field
    here at all - FR-03 immutability is expressed by the absence, not by a
    runtime rejection of a value this dataclass would otherwise accept."""

    preferred_term: str | None = None
    status: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            name: value
            for name, value in (
                ("preferred_term", self.preferred_term),
                ("status", self.status),
            )
            if value is not None
        }


_logger = logging.getLogger(__name__)


_LOGGED_KEY_LIMIT = 10


def _is_over_maximum_length(entry: CatalogueEntry, maximum: int) -> bool:
    return exceeds_maximum_length(entry.length, maximum)


def _log_over_maximum_length(entry: CatalogueEntry, maximum: int) -> None:
    """FR-86: logs the business key and length, never the term text."""
    _logger.warning(
        "preferred term over the configured maximum length: business_key=%s length=%d maximum=%d",
        entry.business_key,
        entry.length,
        maximum,
    )


def create_entry(
    session: Session,
    ctx: AuditContext,
    *,
    preferred_term: str,
    reason: str,
    status: CatalogueEntryStatus | str = CatalogueEntryStatus.DRAFT,
    business_key: str | None = None,
    max_preferred_term_length: int | None = None,
) -> CatalogueEntry:
    """Creates a new entry. `business_key` is minted via
    `allocate_business_key` unless the caller supplies one, as the
    seeded-import path (ADR-0010) does. Every bulk loader must come through
    here: a Core `insert()` or `COPY` skips `clean_term`, so the FR-87
    report's `char_length` would disagree with the length FR-85 publishes.

    Order of checks: `validate_changelog_note` (FR-37) and `clean_term` first,
    because neither touches the session and a rejected note or term takes no
    lock. Then `acquire_append_lock`, then FR-05's `assert_no_error_collisions`,
    then minting, so a rejected collision consumes no sequence value. The append
    lock precedes the collision check because that check takes its own advisory
    lock, and the reverse order is a lock-ordering cycle (ADR-0035). The
    seeded-import path has no exemption: a baseline with a genuine
    error-severity collision cannot be created until it is resolved editorially
    (PRD Section 6.3).
    """
    validated_reason = validate_changelog_note(reason)
    cleaned_preferred_term = clean_term(preferred_term)
    acquire_append_lock(session)
    assert_no_error_collisions(
        session,
        entry=None,
        term=cleaned_preferred_term,
        language=DEFAULT_LANGUAGE,
        use=str(DesignationUse.PREFERRED),
    )
    resolved_key = business_key if business_key is not None else allocate_business_key(session)
    entry = CatalogueEntry(
        business_key=resolved_key,
        preferred_term=cleaned_preferred_term,
        status=str(status),
    )
    session.add(entry)
    record_change(
        session,
        ctx,
        action="catalogue_entry.created",
        instance=entry,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    if max_preferred_term_length is not None and _is_over_maximum_length(
        entry, max_preferred_term_length
    ):
        _log_over_maximum_length(entry, max_preferred_term_length)
    return entry


def load_entry_for_update(session: Session, business_key: str) -> CatalogueEntry:
    """One entry by `business_key`, any status. Unlike
    `nptc.catalogue.queries.get_entry` (the public read path, filtered to
    `PUBLIC_STATUSES`), an editing surface must reach a `draft` entry before it
    can become `active`. The length report counts every status for the same
    reason; see the `nptc.catalogue.length_report` module docstring.

    Public so write and admin read routes resolve the entry as `save_entry`
    does. A plain `select()` with no `.with_for_update()`: the admin `GET` route
    calls it too, and a row lock here would make every admin read hold it for the
    request. A write that needs `SELECT ... FOR UPDATE` adds it at its own call
    site.
    """
    entry = session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == business_key)
    ).scalar_one_or_none()
    if entry is None:
        raise EntryNotFoundError(f"no catalogue_entry with business_key={business_key!r}")
    return entry


def _latest_change_attribution(
    session: Session, entry_id: uuid.UUID
) -> tuple[str | None, datetime | None]:
    """`(changed_by, changed_at)` for the most recent audit event against
    this entry. Ordered by `sequence`, the chain's canonical ordering (as in
    `append_audit_event`'s tail read), not `occurred_at`: two events in one
    transaction can share a timestamp.

    `changed_by` is `app_user.display_name`, never the internal UUID
    (NFR-04/NFR-26). It is `None` for a system change or an actor pseudonymised
    on closure; NFR-17 clears `display_name`, not the row, so the join always
    succeeds."""
    row = session.execute(
        select(User.display_name, AuditEvent.occurred_at)
        .select_from(AuditEvent)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(
            AuditEvent.entity_type == CatalogueEntry.__tablename__,
            AuditEvent.entity_id == str(entry_id),
        )
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        return None, None
    display_name, occurred_at = row
    return display_name, occurred_at


def _build_conflict_report(
    entry: CatalogueEntry,
    *,
    expected_row_version: int,
    changes: EntryChanges,
    changed_by: str | None,
    changed_at: datetime | None,
) -> ConflictReport:
    auditable = policy_for(CatalogueEntry).auditable
    submitted = changes.as_dict()
    conflicts = tuple(
        FieldConflict(field=name, submitted=value, current=getattr(entry, name))
        for name, value in submitted.items()
        if name in auditable and getattr(entry, name) != value
    )
    return ConflictReport(
        business_key=entry.business_key,
        expected_row_version=expected_row_version,
        current_row_version=entry.row_version,
        conflicts=conflicts,
        changed_by=changed_by,
        changed_at=changed_at,
    )


def _would_change(entry: CatalogueEntry, changes: EntryChanges) -> bool:
    """Whether applying `changes` to `entry` would alter anything.

    `preferred_term` is compared cleaned, because that is what is stored:
    `CatalogueEntry`'s `@validates` hook runs `clean_term`, so a term differing
    only by a normalisable space (PRD Appendix A.1) is no change, and treating
    it as one would audit a change that did not happen.

    `clean_term` may raise `TermCleaningError` (FR-63). It is not caught: the
    term is unstorable, and refusing it here, before the savepoint, is the
    answer the caller would get a few lines later.
    """
    submitted = changes.as_dict()
    if "preferred_term" in submitted:
        submitted["preferred_term"] = clean_term(str(submitted["preferred_term"]))
    return any(getattr(entry, name) != value for name, value in submitted.items())


def _has_pending_audit_changes(entry: CatalogueEntry) -> bool:
    """Whether `entry` holds an unflushed change to a field `record_change`
    would audit, made directly on the loaded instance rather than through
    `EntryChanges`.

    `_would_change` cannot see one: it compares against current attribute
    values, which a direct mutation already moved. Without this check a caller
    who set `entry.status` by hand, then passed a matching `EntryChanges`, would
    look like a no-op and have the mutation flushed with no audit event (NFR-08).

    Uses `load_history().has_changes()` (ADR-0018), not `sa_inspect(entry).modified`.
    `modified` is raised by any assignment, including one writing the value
    already there, and the `@validates("preferred_term")` hook assigns
    `preferred_term_key` as well. Gating on it would send an identical
    re-assignment into `record_change`, which raises `AuditNoOpError` on the
    empty diff.

    Scoped to the fields `diff_instance` iterates, so this and the diff it
    predicts cannot disagree.

    This gives a loud failure, not a saveable pre-mutated instance: opening the
    savepoint flushes the pending change and clears the history `record_change`
    reads, so the caller gets `AuditNoOpError`. Short-circuiting would instead
    write the mutation with no audit row (NFR-08). `save_entry` is the sole
    sanctioned mutator of the instance it loads.

    Reachable only with autoflush suppressed. Normally `load_entry_for_update`'s
    `SELECT` flushes the pending mutation first, which bumps `row_version` and
    makes the save a version conflict.
    """
    policy = policy_for(CatalogueEntry)
    state = sa_inspect(entry)
    return any(
        state.attrs[name].load_history().has_changes()
        for name in policy.auditable | policy.withheld
    )


def assert_entry_row_version(
    session: Session,
    entry: CatalogueEntry,
    expected_row_version: int,
    *,
    changes: EntryChanges | None = None,
) -> None:
    """FR-38's first layer on its own: raises `EntryVersionConflictError`
    with a full `ConflictReport` if `entry.row_version` has moved past
    `expected_row_version`, and does nothing otherwise.

    Public so a write to something attached to an entry (a designation) takes
    the same lock against the same counter as `save_entry`, without needing an
    `EntryChanges`. `catalogue_entry.row_version` is the one optimistic lock a
    caller tracks per entry, covering `property_value` and `designation` rows as
    well as its own columns.

    `changes` only populates `ConflictReport.conflicts`. A caller with none to
    declare omits it and gets `conflicts=()`: still rejected, because the
    version is the contract, and still carrying `current_row_version`,
    `changed_by` and `changed_at`.

    Layer two, the `StaleDataError` backstop for the load-to-flush race, stays in
    `save_entry` and `entry_child_write`; see the module docstring.
    """
    if entry.row_version == expected_row_version:
        return
    changed_by, changed_at = _latest_change_attribution(session, entry.id)
    raise EntryVersionConflictError(
        _build_conflict_report(
            entry,
            expected_row_version=expected_row_version,
            changes=changes if changes is not None else EntryChanges(),
            changed_by=changed_by,
            changed_at=changed_at,
        )
    )


def bump_entry_row_version(entry: CatalogueEntry) -> None:
    """Advances `entry.row_version` by one, so the next writer's
    `expected_row_version` must be the value this write produced.

    An ORM assignment, never a Core `update()`, for the reason in the module
    docstring. Called from `entry_child_write` on a clean exit.
    `save_property_values` does not use it: its own increment is conditional on
    its no-op short-circuit, which `entry_child_write` does not share."""
    entry.row_version += 1


@contextmanager
def entry_child_write(
    session: Session,
    entry: CatalogueEntry,
    expected_row_version: int,
    *,
    reason: str | None = None,
) -> Iterator[None]:
    """Wraps a write to a table that hangs off `entry` but keeps no
    `row_version` of its own (`code_binding`, `designation`), so it shares
    `catalogue_entry.row_version` as its one optimistic lock, as
    `save_property_values` does for `property_value`.

    **Both FR-38 layers.** `assert_entry_row_version` is layer one. It cannot see
    two callers who load the same version and interleave before the flush that
    enforces `version_id_col`. So the yielded writes and
    `bump_entry_row_version` run inside one `session.begin_nested()` savepoint,
    flushed before it commits. A `StaleDataError` at that flush becomes
    `EntryVersionConflictError` with `conflicts=()`, since this caller has no
    `EntryChanges`. Without it that race reached the caller as a 500, not a 409.
    One savepoint also lets the replace-binding route's three-step body share
    a single `with` and roll back together, leaving no partial replacement and
    no audit event. A caller needs no `session.flush()` after the block.

    `reason`, when given, is validated before the lock, so a rejected note takes
    no lock. `None` skips the check for a caller whose body validates its own.

    **Lock order.** `acquire_append_lock` is the first session-touching
    statement, before the savepoint and so before any `catalogue_entry` row lock.
    Setting `entry.row_version` does not touch the database; the
    `UPDATE ... WHERE row_version = ...` at this block's flush does, always after
    the append lock (ADR-0035).

    **The conflict handler re-loads by `business_key`, never refreshes `entry`
    in place.** `session.refresh(entry)` on a row another transaction deleted
    raises `ObjectDeletedError` inside the handler for it, which would reach the
    caller as an unmapped 500. `business_key` is captured before the savepoint
    opens. `load_entry_for_update` then finds the current row or raises the
    domain `EntryNotFoundError`, mapped to 404 and not caught here, as in
    `save_entry`. No hard-delete path exists today; this keeps both handlers
    failing the same way if one is added.

    **Any other exception from the body still rolls the savepoint back.** A
    domain error from the body is re-raised unchanged, but leaving the savepoint
    open would make "no partial write" depend on the caller's teardown. A caller
    that catches a per-entry error and keeps using the session needs the
    guarantee to be local to this block.
    """
    if reason is not None:
        validate_changelog_note(reason)
    acquire_append_lock(session)
    assert_entry_row_version(session, entry, expected_row_version)
    business_key = entry.business_key
    savepoint = session.begin_nested()
    try:
        yield
        bump_entry_row_version(entry)
        session.flush()
    except StaleDataError, ObjectDeletedError:
        savepoint.rollback()
        session.expire(entry)
        refreshed = load_entry_for_update(session, business_key)
        changed_by, changed_at = _latest_change_attribution(session, refreshed.id)
        raise EntryVersionConflictError(
            _build_conflict_report(
                refreshed,
                expected_row_version=expected_row_version,
                changes=EntryChanges(),
                changed_by=changed_by,
                changed_at=changed_at,
            )
        ) from None
    except BaseException:
        savepoint.rollback()
        raise
    else:
        savepoint.commit()


def save_entry(
    session: Session,
    ctx: AuditContext,
    *,
    business_key: str,
    expected_row_version: int,
    changes: EntryChanges,
    reason: str,
    max_preferred_term_length: int | None = None,
    over_maximum_keys: list[str] | None = None,
) -> CatalogueEntry:
    """Applies `changes` to the entry identified by `business_key`,
    enforcing FR-38 optimistic locking. Raises `EntryVersionConflictError`
    (never a silent overwrite) if `expected_row_version` is stale, whether
    caught by the precondition check or by the `version_id_col` backstop; see
    the module docstring for why neither leaves an audit event.

    A preferred term that changed to one over `max_preferred_term_length` is
    logged, or its business key appended to `over_maximum_keys` when a batch
    caller wants one record for the lot.

    `reason` (FR-37) is validated first; it never touches the session, so a
    rejected note takes no lock. `acquire_append_lock` then runs before the entry
    load, the collision check and the savepoint whose flush issues the
    row-locking UPDATE, as `entry_child_write` does (ADR-0035)."""
    validated_reason = validate_changelog_note(reason)
    # Before `load_entry_for_update`: that ORM `select()` autoflushes any
    # pending `catalogue_entry` mutation, which would take a row lock before
    # the append lock. Unconditional, so a no-op resubmission takes the lock
    # too; the guarantee then holds at this function's boundary instead of
    # depending on every caller entering with a clean session.
    acquire_append_lock(session)
    entry = load_entry_for_update(session, business_key)

    assert_entry_row_version(session, entry, expected_row_version, changes=changes)

    # A no-op save returns before anything is touched, as in
    # `save_property_values` and `amend_designation`. It sits after the
    # row-version check on purpose: a stale caller whose values happen to match
    # is still refused, because the version is the contract
    # (`test_a_stale_save_that_would_have_matched_still_reports_zero_conflicts`).
    # Without it `record_change` raises `AuditNoOpError` on the empty diff, a
    # 500 for an editor who re-saved a form unchanged. A no-op must not move
    # `row_version`, which would invalidate a concurrent editor's current token.
    if not _would_change(entry, changes) and not _has_pending_audit_changes(entry):
        return entry

    if changes.preferred_term is not None:
        # FR-05: after the row_version precondition (a stale caller should
        # see the version conflict, not a collision with data it never saw),
        # and before the savepoint so a rejected collision never reaches
        # `record_change`.
        assert_no_error_collisions(
            session,
            entry=entry,
            term=clean_term(changes.preferred_term),
            language=DEFAULT_LANGUAGE,
            use=str(DesignationUse.PREFERRED),
        )

    # The savepoint opens before any attribute changes: opening one autoflushes
    # pending state, and a flush after the setattr calls would write the
    # mutation and clear the attribute history `record_change` reads, turning
    # every save into a spurious `AuditNoOpError`.
    previous_term = entry.preferred_term
    savepoint = session.begin_nested()
    try:
        for name, value in changes.as_dict().items():
            setattr(entry, name, value)

        record_change(
            session,
            ctx,
            action="catalogue_entry.updated",
            instance=entry,
            kind=ChangeKind.UPDATED,
            reason=validated_reason,
        )
        # Committed inside the try so the guarantee is local: it relies on
        # `record_change` raising if anything above failed, not on
        # `append_audit_event` always flushing before it returns.
        savepoint.commit()
    except StaleDataError, ObjectDeletedError:
        savepoint.rollback()
        session.expire(entry)
        refreshed = load_entry_for_update(session, business_key)
        changed_by, changed_at = _latest_change_attribution(session, refreshed.id)
        raise EntryVersionConflictError(
            _build_conflict_report(
                refreshed,
                expected_row_version=expected_row_version,
                changes=changes,
                changed_by=changed_by,
                changed_at=changed_at,
            )
        ) from None

    if (
        max_preferred_term_length is not None
        and entry.preferred_term != previous_term
        and _is_over_maximum_length(entry, max_preferred_term_length)
    ):
        if over_maximum_keys is None:
            _log_over_maximum_length(entry, max_preferred_term_length)
        else:
            over_maximum_keys.append(entry.business_key)
    return entry


def save_entries(
    session: Session,
    ctx: AuditContext,
    *,
    updates: Sequence[tuple[str, int, EntryChanges]],
    reason: str,
    max_preferred_term_length: int | None = None,
) -> list[CatalogueEntry]:
    """Applies a batch of `(business_key, expected_row_version, changes)`
    updates, one `save_entry` call and one savepoint per entry (FR-39's "one
    audit event per affected entry"). This is the seam for bulk writes to
    `EntryChanges`' own columns. It is not the seam for bulk reclassify, which
    sets a coded registry property and goes through
    `nptc.catalogue.property_values.save_property_values_for_entries` (ADR-0035).

    `reason` is validated first, so a rejected note takes no lock, even for an
    empty `updates`. `acquire_append_lock` then runs before any session-touching
    statement. Each `save_entry` call re-asserts the lock, so this is redundant
    but keeps the invariant `test_lock_ordering.py` checks uniform across
    writers."""
    validate_changelog_note(reason)
    acquire_append_lock(session)
    over_maximum_keys: list[str] = []
    saved = [
        save_entry(
            session,
            ctx,
            business_key=business_key,
            expected_row_version=expected_row_version,
            changes=changes,
            reason=reason,
            max_preferred_term_length=max_preferred_term_length,
            over_maximum_keys=over_maximum_keys,
        )
        for business_key, expected_row_version, changes in updates
    ]
    if over_maximum_keys:
        shown = ", ".join(over_maximum_keys[:_LOGGED_KEY_LIMIT])
        if len(over_maximum_keys) > _LOGGED_KEY_LIMIT:
            shown += f", ... ({len(over_maximum_keys) - _LOGGED_KEY_LIMIT} more)"
        _logger.warning(
            "%d preferred terms over the configured maximum length: maximum=%d business_keys=%s",
            len(over_maximum_keys),
            max_preferred_term_length,
            shown,
        )
    return saved
