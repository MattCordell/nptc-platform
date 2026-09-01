"""The `catalogue_entry` service layer: business_key minting and the FR-38
optimistic-locking write path (issue #46).

**Why `save_entry`/`save_entries` never build a Core `sqlalchemy.update()`
statement.** ADR-0012 already names the hazard for `property_definition`:
a Core-style bulk update goes through the ORM `Session` but bypasses
`version_id_col` enforcement entirely, silently turning every concurrent
editor's optimistic lock into decoration. Every write here loads the
mapped instance and lets the ORM's own `UPDATE ... WHERE row_version = ...`
do the real work - `backend/tests/test_sql_parameterisation.py`'s AST guard
enforces this statically for `catalogue_entry` the same way it does for
SQL string construction.

**Two layers of conflict detection, not one.** `save_entry` first checks
`expected_row_version` against the freshly loaded row *before* mutating
anything (`assert_entry_row_version`, public since issue #227 so a write
against something *attached to* an entry can take the same lock without
inventing a second counter) - this is the path that can build a useful
`ConflictReport`, because both the caller's stale view and the current row
are in hand uncorrupted. `version_id_col` is the backstop for the genuine race: two
callers who both pass that first check and then interleave between load
and flush. That second case surfaces as SQLAlchemy's own `StaleDataError`
at `session.flush()` time, wrapped inside a `session.begin_nested()`
savepoint so only this entry's attempted write rolls back - not the whole
request, which matters for `save_entries`' multi-entry, one-savepoint-per-
entry loop that #63's bulk reclassify is meant to call.

**Why no audit event is ever written for a rejected save.** The first
layer raises before `nptc.audit.recording.record_change` is ever called.
The second layer's `StaleDataError` is raised by `session.flush()` *inside*
`nptc.audit.writer.append_audit_event`'s own flush step, which runs before
that function ever constructs or adds an `AuditEvent` row - so the
savepoint rollback discards only the attempted (and never-persisted)
`UPDATE`, and no audit row is ever built in the first place. Both paths
have their own test (`backend/tests/test_catalogue_optimistic_locking.py`);
one passing proves nothing about the other.

**FR-37 (issue #47): `reason` is a required argument, not an optional one.**
Every write path here validates it via
`nptc.catalogue.changelog.validate_changelog_note` *before* touching the
row - the same precondition-before-mutation posture the row-version check
above already uses - so a rejected note leaves neither a partial write nor
an audit event behind. There is no exemption: the ADR-0010 seeded-import
path supplies `nptc.catalogue.changelog.SEED_IMPORT_NOTE`, which passes
validation on its own merits rather than bypassing it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
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
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.errors import (
    ConflictReport,
    EntryNotFoundError,
    EntryVersionConflictError,
    FieldConflict,
)
from nptc.catalogue.term_hygiene import clean_term
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.designation import DesignationUse
from nptc.db.models.user import User
from nptc_shared.language import DEFAULT_LANGUAGE

#: The single Python source of truth for the FR-03 format - shared by
#: `format_business_key`, `advance_sequence_past`, and mirrored (never
#: generated from this constant, per test_sql_parameterisation.py's ban on
#: SQL built from runtime data) by `CatalogueEntry`'s own CHECK constraint
#: and migration 0006's sequence-backed default.
BUSINESS_KEY_PREFIX: Final[str] = "NPTC-"
BUSINESS_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^NPTC-([0-9]{6,})$")

#: Fixed name, referenced identically here and in migration
#: 0006_catalogue_entry.py (via `nptc.db.roles`) - a single source of truth
#: for the one identifier that must match on both sides.
BUSINESS_KEY_SEQUENCE_NAME: Final[str] = "catalogue_entry_business_key_seq"


def format_business_key(sequence_value: int) -> str:
    """`NPTC-` plus a 6-digit zero-padded sequence value (FR-03), for
    example `NPTC-000247`. Not capped at 6 digits: `BUSINESS_KEY_PATTERN`
    (and the database CHECK it mirrors) accept more, so a catalogue that
    outgrows six digits needs no migration - only a wider zero-pad here
    would need to change, and even that is cosmetic once past 999999."""
    return f"{BUSINESS_KEY_PREFIX}{sequence_value:06d}"


def allocate_business_key(session: Session) -> str:
    """Mints the next `business_key` from the dedicated Postgres sequence.
    The one mint point for a genuinely new (non-seeded) entry - see
    `nptc.db.models.catalogue_entry`'s module docstring for why this lives
    in Python rather than a column `server_default`."""
    next_value = session.execute(
        text("SELECT nextval(:seq)"), {"seq": BUSINESS_KEY_SEQUENCE_NAME}
    ).scalar_one()
    return format_business_key(int(next_value))


def advance_sequence_past(session: Session, business_key: str) -> None:
    """Reconciles the backend's minting sequence with a seeded baseline
    (ADR-0010): the P0 transform mints its own `business_key`s
    deterministically and positionally, so after a seeded import this must
    be called once with the *highest* seeded key to guarantee the next
    `allocate_business_key` call mints a key strictly greater than every
    seeded one.

    Deliberately a single atomic statement, not a read-then-compare: a
    freshly created sequence reports `last_value = 1` even though nothing
    has ever been dispensed from it (`is_called` is what actually
    distinguishes "never called" from "1 was issued", and comparing
    against `last_value` alone treats the two identically) - reading
    `last_value` directly would therefore silently no-op the very first
    reconciliation against a seeded baseline as small as `NPTC-000001`,
    and the next `allocate_business_key` call would reissue that exact
    key. Calling `nextval()` first and subtracting 1 gives the correct
    "highest value already dispensed" figure regardless of `is_called`,
    and folding the read and the write into one `setval` call also closes
    the read-then-write race a separate read statement would otherwise
    leave open against a concurrent reconciliation. The cost is one
    consumed (and permanently skipped) sequence value per call - harmless,
    since FR-03 only requires `business_key` is never *reused*, not that
    the sequence itself never has gaps."""
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
    specimen_unconstrained: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            name: value
            for name, value in (
                ("preferred_term", self.preferred_term),
                ("status", self.status),
                ("specimen_unconstrained", self.specimen_unconstrained),
            )
            if value is not None
        }


def create_entry(
    session: Session,
    ctx: AuditContext,
    *,
    preferred_term: str,
    reason: str,
    status: CatalogueEntryStatus | str = CatalogueEntryStatus.DRAFT,
    specimen_unconstrained: bool = False,
    business_key: str | None = None,
) -> CatalogueEntry:
    """Creates a new entry. `business_key` is minted via
    `allocate_business_key` unless the caller supplies one explicitly - the
    seeded-import path (ADR-0010) supplies its own, positionally-derived
    key rather than minting.

    `reason` (FR-37) is validated before anything is added to the session -
    see the module docstring. FR-05's error-severity collision check
    (`nptc.catalogue.collisions.assert_no_error_collisions`) runs
    immediately after, before `business_key` is even minted - a rejected
    collision must not consume a sequence value. There is no exemption for
    the seeded-import path: PRD Section 6.3's own consequence is that a
    baseline carrying a genuine error-severity collision cannot be created
    until it is resolved editorially."""
    validated_reason = validate_changelog_note(reason)
    cleaned_preferred_term = clean_term(preferred_term)
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
        specimen_unconstrained=specimen_unconstrained,
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
    return entry


def load_entry_for_update(session: Session, business_key: str) -> CatalogueEntry:
    """One entry by `business_key`, any status - unlike
    `nptc.catalogue.queries.get_entry`, which is the *public* read path and
    filters to `PUBLIC_STATUSES` on purpose (an unpublished entry must stay
    invisible to an anonymous caller). An editing surface needs the entry
    regardless of status - a draft has to be editable before it can ever
    become `active` - so this loader carries no status filter at all.

    Public (not `_load_for_update`) so a write route elsewhere in the
    `nptc.catalogue`/`nptc.api` write surface (issue #219) can resolve the
    same entry this module's own `save_entry`/`save_entries` do, rather than
    re-querying `CatalogueEntry` by hand.

    A plain `select()`, deliberately - no `.with_for_update()`. That stays
    true even though the name suggests a write-path-only helper: issue
    #228's `catalogue_admin` read route also resolves the entry through
    this function (the same status-unfiltered lookup a write needs, wanted
    here for a `GET`), so adding a row lock here to serve some future write
    caller would silently make every admin *read* hold that lock for the
    request's lifetime too. A write that genuinely needs `SELECT ... FOR
    UPDATE` should add it at its own call site, not here.
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
    this entry - ordered by `sequence`, the chain's own canonical ordering
    (matching `nptc.audit.writer.append_audit_event`'s own tail read),
    not `occurred_at`: two events in the same transaction share a
    `clock_timestamp()`-derived value closely enough that ordering by it
    alone leaves a tie-break undefined.

    `changed_by` is resolved to `app_user.display_name` - never the
    internal UUID (NFR-04/NFR-26) - and is `None` for a system-initiated
    change or an actor account since pseudonymised on closure (NFR-17
    clears `display_name`, not the row itself, so the join always
    succeeds; only the name is ever missing)."""
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
    """Whether applying `changes` to `entry` would actually alter anything.

    `preferred_term` is compared *cleaned*, because that is what would be
    stored: `CatalogueEntry`'s own `@validates` hook runs `clean_term` on
    assignment, so a submitted term differing only by a normalisable space
    (PRD Appendix A.1) is the same string once stored, and treating it as a
    change would write an audit event saying nothing changed.

    `clean_term` can raise `TermCleaningError` on a term with no single
    correct repair (FR-63). That is deliberately not caught: the caller
    submitted an unstorable term, and refusing it here - before the
    savepoint, like every other precondition in this module - is the same
    answer they would have got a few lines later.
    """
    submitted = changes.as_dict()
    if "preferred_term" in submitted:
        submitted["preferred_term"] = clean_term(str(submitted["preferred_term"]))
    return any(getattr(entry, name) != value for name, value in submitted.items())


def _has_pending_audit_changes(entry: CatalogueEntry) -> bool:
    """Whether `entry` already carries an unflushed change to a field
    `record_change` would audit - a mutation made by the caller directly on
    the loaded instance, rather than declared through `EntryChanges`.

    `_would_change` alone cannot see one. It compares against the entry's
    *current* attribute values, which a direct mutation has already moved,
    so a caller who set `entry.status` by hand and then passed a
    coincidentally-matching `EntryChanges` would look like a no-op and have
    that mutation flushed with no audit event (NFR-08).

    **Net history, not `sa_inspect(entry).modified`** (issue #227 review).
    `modified` is a set-*event* flag: SQLAlchemy raises it on any
    assignment, including one that writes the value already there - and
    `CatalogueEntry`'s own `@validates("preferred_term")` hook assigns
    `preferred_term_key` as well, so one assignment trips it twice. Gating
    on it would send an identical re-assignment straight back to
    `record_change`, into the empty diff and unmapped `AuditNoOpError` this
    short-circuit exists to prevent. `load_history().has_changes()` is the
    net question, and returns `False` for a same-value assignment (verified:
    such a value lands in the history's `unchanged`, not `added`/`deleted`).

    Scoped to the fields the audit policy actually diffs, matching
    `nptc.audit.diffing.diff_instance`'s own iteration, so this and the diff
    it is predicting cannot disagree about which fields count.

    What this buys is a *loud* failure rather than a silent one. It does not
    make a pre-mutated instance saveable: `save_entry` cannot build a
    correct diff for one, because opening its savepoint flushes the pending
    change and clears the history `record_change` reads, so the caller gets
    `AuditNoOpError` - which names exactly that case and calls it a bug.
    Short-circuiting instead would return successfully having written the
    mutation with no audit row at all, and that is the NFR-08 failure worth
    preventing. `save_entry` is the sole sanctioned mutator of the instance
    it loads; this is what enforces it rather than assuming it.

    Reachable only with autoflush suppressed: normally
    `load_entry_for_update`'s own `SELECT` flushes a pending mutation before
    `save_entry` reaches this point, which bumps `row_version` and makes the
    save a version conflict instead.
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
    """FR-38's first layer, on its own: raises `EntryVersionConflictError`
    with a full `ConflictReport` if `entry.row_version` has moved past
    `expected_row_version`, and does nothing at all otherwise.

    Extracted from `save_entry` (issue #227) so a write that changes
    something *attached to* an entry can take the same lock the entry's own
    writes take, against the same counter, without going through
    `save_entry` - which would insist on an `EntryChanges` it has nothing to
    put in. `nptc.catalogue.property_values.save_property_values` already
    established that `catalogue_entry.row_version` is the one optimistic
    lock a caller tracks per entry, covering more than `catalogue_entry`'s
    own columns; this is that argument applied to `designation`.

    `changes` is only ever used to populate `ConflictReport.conflicts` -
    the fields the caller submitted whose stored value has since moved. A
    caller with no entry-level changes to declare (the designation case)
    omits it and gets `conflicts=()`, which is exactly the
    non-overlapping-field conflict `ConflictReport`'s own docstring
    describes: still rejected, because the version is the contract
    regardless, and still carrying `current_row_version`/`changed_by`/
    `changed_at` so the caller is never left with nothing to show.

    This is layer *one* only. `save_entry` keeps its own `StaleDataError`
    backstop for the genuine load-to-flush race, which no precondition
    check can see - see the module docstring.
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


def save_entry(
    session: Session,
    ctx: AuditContext,
    *,
    business_key: str,
    expected_row_version: int,
    changes: EntryChanges,
    reason: str,
) -> CatalogueEntry:
    """Applies `changes` to the entry identified by `business_key`,
    enforcing FR-38 optimistic locking. Raises `EntryVersionConflictError`
    (never a silent overwrite) if `expected_row_version` is stale, whether
    caught by the explicit precondition check below or by the
    `version_id_col` backstop for a genuine concurrent race - see the
    module docstring for why neither path ever leaves an audit event
    behind.

    `reason` (FR-37) is validated before the entry is even loaded, so a
    rejected note never reaches the row-version check at all."""
    validated_reason = validate_changelog_note(reason)
    entry = load_entry_for_update(session, business_key)

    assert_entry_row_version(session, entry, expected_row_version, changes=changes)

    # A no-op save (the same values resubmitted) returns before anything is
    # touched - `nptc.catalogue.property_values.save_property_values` makes
    # the same check for the same reasons, and `nptc.catalogue.designations.
    # amend_designation` does for a term resubmitted unchanged.
    #
    # Deliberately *after* the row-version check above, not before: a stale
    # caller whose submitted values happen to coincide with the current ones
    # is still refused, because the version is the contract regardless
    # (`test_catalogue_optimistic_locking.py::test_a_stale_save_that_would_
    # have_matched_still_reports_zero_conflicts`).
    #
    # Without this, `record_change` raises `AuditNoOpError` on the empty
    # diff - an unmapped error, so an editor re-saving a form without having
    # changed the term got a 500 (found reviewing issue #227's own new
    # route, where a `preferred_term` differing only by a normalisable space
    # cleans to the stored value and reaches exactly this path). A no-op
    # resubmission is not a caller mistake, and `row_version` must not move
    # for one: doing so would invalidate a concurrent editor's still-current
    # token for no actual change.
    if not _would_change(entry, changes) and not _has_pending_audit_changes(entry):
        return entry

    if changes.preferred_term is not None:
        # FR-05: checked after the row_version precondition (a stale
        # caller should see the version conflict, not a collision against
        # data it never actually saw) and before the savepoint opens - a
        # rejected collision must never reach `record_change`.
        assert_no_error_collisions(
            session,
            entry=entry,
            term=clean_term(changes.preferred_term),
            language=DEFAULT_LANGUAGE,
            use=str(DesignationUse.PREFERRED),
        )

    # The savepoint must open *before* any attribute is mutated: opening
    # one autoflushes any already-pending state, and if that happened
    # after the setattr calls below it would flush the mutation straight
    # to the database and clear SQLAlchemy's attribute history before
    # record_change ever gets to read it - turning every save into a
    # spurious AuditNoOpError regardless of whether it actually conflicts.
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
        # Committing the savepoint here, inside the try, makes the
        # guarantee local to this function: it relies only on
        # record_change/append_audit_event raising if anything above
        # failed, not on the separate assumption that append_audit_event
        # always flushes before returning (true today, but a detail of
        # that module rather than a contract this one should depend on).
        savepoint.commit()
    except (StaleDataError, ObjectDeletedError):
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

    return entry


def save_entries(
    session: Session,
    ctx: AuditContext,
    *,
    updates: Sequence[tuple[str, int, EntryChanges]],
    reason: str,
) -> list[CatalogueEntry]:
    """Applies a batch of `(business_key, expected_row_version, changes)`
    updates, one `save_entry` call - and one savepoint - per entry (FR-39's
    "one audit event per affected entry"). This is the seam #63's bulk
    reclassify is meant to call instead of a Core bulk `update()`: it
    exists now, ahead of that issue, specifically so there is a correct
    path to reach for rather than one to invent under deadline."""
    return [
        save_entry(
            session,
            ctx,
            business_key=business_key,
            expected_row_version=expected_row_version,
            changes=changes,
            reason=reason,
        )
        for business_key, expected_row_version, changes in updates
    ]
