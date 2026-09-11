"""The `designation` service layer (issue #47, FR-04, FR-24, FR-37, FR-85).

`clean_term`/`preferred_term_length`/`TermCleaningError` are re-exported
from `nptc.catalogue.term_hygiene` for convenience - see that module's own
docstring for why the audit-free pieces had to move there rather than
live here: `nptc.db.models.designation.Designation` (and
`nptc.db.models.catalogue_entry.CatalogueEntry`) need them for their own
`@validates`/`length` hooks, and this module imports `nptc.audit.recording`
(which reaches back into `nptc.db.models` through `nptc.audit.writer`), so
a model importing *this* module directly would be circular.

FR-05 error-severity collision detection runs here, before every row is
constructed (`nptc.catalogue.collisions.assert_no_error_collisions`) - a
rejected save leaves no audit event, matching every other precondition
check in this package. Warning-severity ("the same synonym on multiple
entries") is deliberately **not** checked here: it never blocks a save,
so it is a query a caller (#149's edit screen) asks of
`nptc.catalogue.collisions.warning_collisions`, not a precondition this
module enforces.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.term_hygiene import (
    TermCleaningError,
    clean_term,
    preferred_term_length,
    validate_language_tag,
)
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

__all__ = [
    "DesignationAlreadyRetiredError",
    "DesignationNotFoundError",
    "DesignationNotRetiredError",
    "DuplicateActiveTermError",
    "PreferredDesignationAlreadyActiveError",
    "TermCleaningError",
    "add_designation",
    "add_synonyms",
    "amend_designation",
    "clean_term",
    "find_active_designation",
    "find_retired_designation",
    "load_active_designation",
    "load_retired_designation",
    "preferred_term_length",
    "reinstate_designation",
    "retire_designation",
]

#: `ix_designation_no_duplicate_active_term`/`ix_designation_one_active_
#: preferred_per_entry_language`'s own literal names (`Index(...)` was
#: given an explicit name directly, so `NAMING_CONVENTION`'s "ix" pattern
#: never applies to either - see `nptc.db.models.designation`). Matched
#: against `unique_violation_constraint(exc)` the same way `nptc.catalogue.
#: bindings.create_binding` matches its own two constraint names, so a lost
#: race at flush becomes the same typed domain error the pre-insert check
#: below would have raised had it run a moment later.
_NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT = "ix_designation_no_duplicate_active_term"
_ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT = (
    "ix_designation_one_active_preferred_per_entry_language"
)

if TYPE_CHECKING:
    # Import-time only - see the module docstring for why a runtime import
    # of `nptc.db.models.designation` here would be circular.
    # `from __future__ import annotations` makes every annotation below a
    # lazy string, so the type checker sees this without the interpreter
    # ever needing to resolve it at import time.
    from nptc.db.models.designation import Designation


class DesignationAlreadyRetiredError(ValueError):
    """Raised by `retire_designation` when `designation` is already
    `retired` - retiring twice would otherwise silently write a second
    `designation.retired` audit event with no actual state change, which
    reads as a real edit to anyone reviewing the audit log even though
    nothing changed.

    `http_status: ClassVar[int] = 409` - the same convention every other
    error class this issue adds carries (`TermCleaningError`,
    `DesignationLanguageError`, `ChangelogNoteError`), so
    `nptc.api.errors.register_exception_handlers` has a status to read
    rather than falling through to an unhandled 500 once #149/#150 give
    this a caller. 409, not 422: the request is well-formed, it just
    conflicts with the resource's current state - the same reasoning
    `EntryVersionConflictError` already applies."""

    http_status: ClassVar[int] = 409


class DesignationNotRetiredError(ValueError):
    """Raised by `reinstate_designation` (issue #313) when the designation
    passed to it is not currently retired - reinstating an active row would
    otherwise reach `record_change` with an empty diff (`status` unchanged),
    which raises the internal `AuditNoOpError` rather than a clean domain
    error, exactly the failure mode `retire_designation`'s own
    already-retired guard exists to avoid.

    The route-level case this also covers: a term retired and then
    re-added creates a **new** active row sharing the retired row's
    `term_key` (the scenario #313's own issue body describes) - the
    original stays retired, but the *address* `(entry_id, term_key,
    language)` now already has an active designation, so there is nothing
    to reinstate for that address either. `catalogue_designations.py`'s
    route checks this before ever resolving a retired row to act on, and
    raises this same error, so both cases - a caller passing an active row
    directly, and an address that already resolves to one - answer with
    the same type.

    409, not 422: matching `DesignationAlreadyRetiredError`'s own
    reasoning - the request is well-formed, it just conflicts with the
    resource's current state."""

    http_status: ClassVar[int] = 409


class DesignationNotFoundError(LookupError):
    """Raised by `load_active_designation` when no *active* designation on
    `entry_id` matches `term`/`language` - the same `LookupError` +
    `http_status` convention `nptc.catalogue.bindings.
    CodeBindingNotFoundError` uses, so `nptc.api.errors.
    register_exception_handlers` has a status to read rather than falling
    through to an unhandled 500 once #224 gives this a caller.

    404, not 409: a term that was retired, or never added, is simply not
    addressable this way any more - not a conflicting state (matching
    `CodeBindingNotFoundError`'s own reasoning)."""

    http_status: ClassVar[int] = 404


class DuplicateActiveTermError(ValueError):
    """Raised when `add_designation`/`amend_designation` would produce a
    second active designation sharing `(entry_id, term_key, language)` -
    the same comparison-key fold `ix_designation_no_duplicate_active_term`
    (issue #49) enforces at the database. 409, not 422: the request is
    well-formed, it just conflicts with a term this entry already holds
    (the same reasoning `EntryVersionConflictError` already applies)."""

    http_status: ClassVar[int] = 409


class PreferredDesignationAlreadyActiveError(ValueError):
    """Raised when `add_designation` would give one entry a second active
    `use='preferred'` designation in the same language -
    `ix_designation_one_active_preferred_per_entry_language` (issue #47) is
    the database invariant this mirrors. Note the catalogue's own en-AU
    preferred term is never a `designation` row at all (ADR-0022,
    `ck_designation_no_en_au_preferred`); this only ever fires for a
    non-en-AU preferred variant."""

    http_status: ClassVar[int] = 409


def find_active_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation | None:
    """`load_active_designation` without the refusal: `None` where that
    function would raise `DesignationNotFoundError`. See its docstring for
    the lookup itself - this is the same query, and that is the one both
    callers share.

    Exists because a caller can legitimately need "is there one?" as a
    *question* rather than as a precondition. Issue #227's
    dispatch inside `POST .../designations/amendment` is the case: the
    catalogue's own en-AU preferred term lives on
    `catalogue_entry.preferred_term`, never a `designation` row (ADR-0022),
    so that route has to distinguish "no such designation, but this is the
    entry's own preferred term" from "no such designation at all". Doing
    that by catching `DesignationNotFoundError` would put a `try`/`except`
    in a route body, which `nptc.api.routers.catalogue_designations`' own
    module docstring forbids.
    """
    from nptc.db.models.designation import Designation as _Designation
    from nptc.db.models.designation import DesignationStatus

    key = collision_key(clean_term(term))
    canonical_language = validate_language_tag(language)
    return session.execute(
        select(_Designation).where(
            _Designation.entry_id == entry_id,
            _Designation.term_key == key,
            _Designation.language == canonical_language,
            _Designation.status == str(DesignationStatus.ACTIVE),
        )
    ).scalar_one_or_none()


def load_active_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation:
    """Resolves an active designation from its public address:
    `(entry_id, term, language)` - the shape a caller addressing a
    designation by term in a request body (never a path segment or an
    internal id, since a term can contain `/`) actually has on hand.

    Looked up by *comparison key*, not the raw term:
    `ix_designation_no_duplicate_active_term` (issue #49) is itself keyed
    on `term_key`, so a caller naming a case/punctuation variant of the
    stored term still resolves the same row - matching what the collision
    check itself would consider a duplicate.

    `use` is deliberately not a filter parameter: the index above has no
    `use` column, so `(entry_id, term_key, language)` already identifies
    at most one *active* row regardless of use.

    `language` is canonicalised before the query (`nptc.catalogue.
    term_hygiene.validate_language_tag`) - a stored designation's own
    `language` column was canonicalised the same way by `Designation`'s
    `@validates` hook when it was written, so a caller naming `en-au`
    still resolves a row stored as `en-AU` (issue #224 review finding 2).

    The query itself is `find_active_designation`'s (issue #227), so the two
    can never disagree about what "the active designation for this address"
    means; all this adds is the refusal."""
    designation = find_active_designation(session, entry_id=entry_id, term=term, language=language)
    if designation is None:
        # Canonicalised once, into a local, rather than inline in the
        # f-string: the miss path would otherwise run `validate_language_tag`
        # a second time over a value `find_active_designation` has already
        # canonicalised (issue #227 review).
        canonical_language = validate_language_tag(language)
        raise DesignationNotFoundError(
            f"entry {entry_id} has no active designation for term {term!r} "
            f"in language {canonical_language!r}"
        )
    return designation


def find_retired_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation | None:
    """The retired-row equivalent of `find_active_designation` (issue #313).
    `find_active_designation`/`load_active_designation` are ACTIVE-only by
    construction (their query hardcodes `status == 'active'`), so they
    cannot resolve the row a reinstatement needs to act on - this is a
    sibling, not a variant, of that query.

    More than one retired row can share `(entry_id, term_key, language)`:
    a term added, retired, and re-added twice over leaves that many rows
    behind, since `ix_designation_no_duplicate_active_term` is
    active-only. `retired_at DESC` picks the most recently retired first
    (issue #313's plan settled this as the answer an editor would expect -
    two retired rows sharing every field the API exposes are otherwise
    indistinguishable to them). `created_at DESC` is the tiebreaker for two
    retirements sharing one transaction (Postgres `now()` is transaction
    time, so a bulk retirement ties on `retired_at` exactly) - `id` is a
    UUID and so carries no chronological meaning at all, unlike
    `get_entry_by_code`'s own `business_key`, which is why this tiebreaker
    is `created_at`, not `id` (issue #322 review). `id ASC` remains the
    final tiebreaker after that, for the residual case of two rows sharing
    both timestamps: two identical requests must never disagree.

    `created_at`'s own `server_default` is also transaction time, so it
    only breaks a tie between rows created in *different* transactions; a
    full add/retire cycle repeated twice within one transaction still ties
    on both columns and falls through to `id ASC` (issue #322 review,
    follow-up)."""
    from nptc.db.models.designation import Designation as _Designation
    from nptc.db.models.designation import DesignationStatus

    key = collision_key(clean_term(term))
    canonical_language = validate_language_tag(language)
    return session.execute(
        select(_Designation)
        .where(
            _Designation.entry_id == entry_id,
            _Designation.term_key == key,
            _Designation.language == canonical_language,
            _Designation.status == str(DesignationStatus.RETIRED),
        )
        .order_by(
            _Designation.retired_at.desc(),
            _Designation.created_at.desc(),
            _Designation.id.asc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def load_retired_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation:
    """Resolves the most-recently-retired designation from its public
    address, or raises `DesignationNotFoundError` (404) - the same 404
    `load_active_designation` raises for its own miss case, reused rather
    than a new class: a term that was never retired on this entry is
    simply not addressable this way, not a conflicting state (issue #313).

    Deliberately does not also check for an *active* designation sharing
    this address - that is a different outcome (`DesignationNotRetiredError`,
    409, not 404) and the caller (`catalogue_designations.py`'s route)
    checks it first, before ever calling this function, so a stale-looking
    404 is never returned for an address that actually already has an
    active row."""
    designation = find_retired_designation(session, entry_id=entry_id, term=term, language=language)
    if designation is None:
        canonical_language = validate_language_tag(language)
        raise DesignationNotFoundError(
            f"entry {entry_id} has no retired designation for term {term!r} "
            f"in language {canonical_language!r}"
        )
    return designation


def add_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    term: str,
    use: str = "synonym",
    language: str = DEFAULT_LANGUAGE,
    reason: str,
) -> Designation:
    """Adds one designation row to `entry`. `term` is cleaned by
    `Designation`'s own `@validates` hook, but cleaned again here first so
    FR-05's error-severity collision check
    (`nptc.catalogue.collisions.assert_no_error_collisions`) compares the
    same value that will actually be stored; `reason` is validated here,
    before the row is even constructed, so a rejected note (or a rejected
    collision) leaves nothing behind to roll back (matching `save_entry`'s
    precondition-before-mutation posture for FR-38).

    The error-severity collision check above narrows the race against a
    *different* entry's designations but cannot close a race against
    *this* entry's own: two concurrent adds of the same term on one entry
    both pass it and only one wins at insert. `record_change(kind=CREATED)`
    flushes the session itself (see its own docstring) - that flush is
    what actually hits the database and is where the loser's
    `IntegrityError` surfaces, so it is what this translates into
    `DuplicateActiveTermError`/`PreferredDesignationAlreadyActiveError`
    (issue #224), rather than reaching the caller as an unmapped 500.

    `entry_id` is read into a local before the flush, not re-read from
    `entry.id` inside the `except` block: a failed flush leaves every
    instance the session tracks expired, so touching an ORM attribute
    afterwards - even one already loaded - triggers a reload against a
    session that is not yet rolled back, raising `PendingRollbackError`
    in place of the domain error this is meant to raise (issue #224
    review).

    `language` is canonicalised before the collision check runs, not only
    by `Designation`'s own `@validates` hook when the row is constructed
    below: `assert_no_error_collisions`'s `language == DEFAULT_LANGUAGE`
    branching would otherwise silently take the wrong path for a
    caller-supplied `en-au` (issue #224 review finding 2).

    **`acquire_append_lock` runs as the literal first statement (issue #281
    round-2 review).** Every existing caller today reaches this function
    already wrapped in `nptc.catalogue.entries.entry_child_write` (issue
    #60/#300), which takes the same lock first - so this call is a cheap,
    safe re-assertion of a lock already held, not a second acquisition.
    Without it, a caller invoking `add_designation` directly (bypassing
    `entry_child_write`) would reopen the exact collision-lock-vs-append-
    lock cycle issue #281 closes elsewhere: this function's own collision
    check takes `assert_no_error_collisions`'s advisory lock, and reaching
    the append lock only afterwards, via `record_change`, is the reverse
    order a concurrent `entry_child_write`-wrapped caller uses. Making the
    invariant hold at this function's own boundary, rather than relying on
    every caller to wrap it correctly, is what closes that gap for good."""
    acquire_append_lock(session)

    from nptc.db.models.designation import Designation

    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(term)
    canonical_language = validate_language_tag(language)
    assert_no_error_collisions(
        session, entry=entry, term=cleaned_term, language=canonical_language, use=use
    )
    entry_id = entry.id
    designation = Designation(
        entry_id=entry_id, term=cleaned_term, use=use, language=canonical_language
    )
    session.add(designation)
    try:
        record_change(
            session,
            ctx,
            action="designation.created",
            instance=designation,
            kind=ChangeKind.CREATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {canonical_language!r}"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT:
            raise PreferredDesignationAlreadyActiveError(
                f"entry {entry_id} already has an active preferred designation "
                f"in language {canonical_language!r}"
            ) from exc
        raise
    return designation


def add_synonyms(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    terms: Sequence[str],
    language: str = DEFAULT_LANGUAGE,
    reason: str,
) -> list[Designation]:
    """Adds each of `terms` as its own synonym row (FR-04) - the same
    changelog note covers the whole batch, validated once up front rather
    than once per row, since they are one edit from the caller's point of
    view.

    Deduplicates by *collision key* before inserting, not merely by the
    cleaned term: `ix_designation_no_duplicate_active_term` (issue #49) is
    itself keyed on `term_key`, so two terms that collapse to the same
    comparison key after `collision_key` (a case or punctuation variant,
    not only a whitespace one - e.g. `"ADA2"` and `"ada2"`) are one
    synonym, not two - inserting both would violate that index at flush
    with an unhelpful `IntegrityError`, from a batch the caller reasonably
    thinks is well-formed. FR-04's whole premise is cleaning up doubled-
    delimiter/whitespace-variant cells; this extends the same posture to
    the stronger FR-05 comparison fold.

    **Inserted in comparison-key order, not caller order.** Each call to
    `add_designation` below acquires `nptc.catalogue.collisions.
    assert_no_error_collisions`'s `pg_advisory_xact_lock` and holds it
    until this transaction commits - so a batch of N terms holds up to N
    locks at once. Two concurrent batches sharing two keys, acquired in
    opposite order (transaction A saving `["ADA2", "17-OHP"]`, transaction
    B saving `["17-OHP", "ADA2"]`), would otherwise each hold one lock and
    wait on the other - a genuine deadlock (Postgres `40P01`), not merely
    contention, and one this codebase has no handler for. Sorting the
    deduplicated terms by their own comparison key first makes acquisition
    order the same for every caller regardless of the order terms were
    submitted in, so two batches can only ever block on each other, never
    deadlock. The returned list is therefore ordered by comparison key,
    not by the order `terms` was given in - #149's caller should not rely
    on positional correspondence between `terms` and the return value.

    `acquire_append_lock` runs as the literal first statement, for the same
    reason `retire_designation` now does (issue #281 round-3 review): every
    `add_designation` call below already re-asserts the same lock on this
    function's behalf, so this call is itself a cheap, safe re-assertion,
    not a second acquisition - kept anyway so this function's own first
    statement satisfies the uniform invariant `test_lock_ordering.py`'s
    derived guard checks, the same as every other writer in this module,
    rather than relying on a reader (or that guard) reasoning transitively
    through the loop below to see that nothing unsafe happens before the
    first `add_designation` call."""
    acquire_append_lock(session)
    validated_reason = validate_changelog_note(reason)
    seen: set[str] = set()
    deduplicated: list[tuple[str, str]] = []
    for term in terms:
        cleaned = clean_term(term)
        key = collision_key(cleaned)
        if key not in seen:
            seen.add(key)
            deduplicated.append((key, cleaned))
    deduplicated.sort(key=lambda pair: pair[0])
    return [
        add_designation(
            session,
            ctx,
            entry=entry,
            term=cleaned,
            use="synonym",
            language=language,
            reason=validated_reason,
        )
        for _key, cleaned in deduplicated
    ]


def retire_designation(
    session: Session,
    ctx: AuditContext,
    *,
    designation: Designation,
    reason: str,
) -> Designation:
    """Retires `designation` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_DESIGNATION_DELETE_SQL` makes this a privilege-
    level guarantee, matching `CatalogueEntry.status`'s own precedent for
    deprecation-not-deletion). Raises `DesignationAlreadyRetiredError`
    rather than silently no-opping - see that class's own docstring.

    `acquire_append_lock` runs as the literal first statement, matching
    every other writer in this module that reaches `record_change` (issue
    #281 round-3 review): this function takes no *other* lock today, so
    the ordering has no live deadlock to close yet, but a uniform "the
    append lock is always this function's first statement" invariant,
    with no per-function exceptions to reason about, is what
    `test_lock_ordering.py`'s own derived guard checks - and is cheaper to
    keep true everywhere than to justify a carve-out for the one function
    that happens not to need it today.

    Sets `retired_at` (issue #313, mirroring `nptc.catalogue.bindings.
    retire_binding`'s own precedent for its own table's retirement
    timestamp, FR-17-style) - `func.now()`, the **database's** clock, so
    `retired_at` orders correctly across every app instance's writes, not
    just this process's own."""
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(f"designation {designation.id} is already retired")

    validated_reason = validate_changelog_note(reason)
    designation.status = str(DesignationStatus.RETIRED)
    designation.retired_at = func.now()
    record_change(
        session,
        ctx,
        action="designation.retired",
        instance=designation,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return designation


def reinstate_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    designation: Designation,
    reason: str,
) -> Designation:
    """Reinstates `designation` via a `status` transition back to active
    (issue #313) - the row keeps its `id`, so `nptc.catalogue.history.
    load_history` reads one continuous record across create, retire and
    reinstate, rather than the orphaned-history-plus-unrelated-new-row
    result re-adding the term today produces (see the module docstring's
    context on this issue).

    Shaped like `add_designation`, not `retire_designation`: retirement can
    never violate a partial unique index (retiring never creates a second
    active row), but reinstating can violate either
    `ix_designation_no_duplicate_active_term` or `ix_designation_one_
    active_preferred_per_entry_language` the same way adding a fresh row
    can, so this runs the same FR-05 error-severity collision check
    (`assert_no_error_collisions`) and the same `IntegrityError`
    translation block `add_designation` does, rather than `retire_
    designation`'s simpler shape.

    `entry` is required (unlike `retire_designation`, which never needs
    one) purely to give `assert_no_error_collisions` the entry to exclude
    from its own comparison - the same reason `amend_designation` takes it.

    **Guards on `designation.status` before mutating, exactly as `retire_
    designation` guards on already-retired.** Reinstating an already-active
    row would otherwise reach `record_change` with an empty diff and raise
    the internal `AuditNoOpError` in place of a clean domain error - see
    `DesignationNotRetiredError`'s own docstring for why the caller
    (`catalogue_designations.py`'s route) also checks this at the address
    level, before ever resolving a row to pass in here, so this guard is
    reached only if a future direct caller skips that check.

    `entry_id`/`cleaned_term`/`canonical_language` are captured into locals
    before the flush below, not re-read from the ORM instances inside
    `except` - a failed flush leaves every instance the session tracks
    expired, so touching an already-loaded attribute afterwards triggers a
    reload against a session that is not yet rolled back, raising
    `PendingRollbackError` in place of the domain error this is meant to
    raise (matching `add_designation`'s own precedent, issue #224 review).

    `acquire_append_lock` runs as the literal first statement, for the same
    reason every other writer in this module does (issue #281 round-3
    review)."""
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.ACTIVE):
        raise DesignationNotRetiredError(f"designation {designation.id} is already active")

    validated_reason = validate_changelog_note(reason)
    entry_id = entry.id
    cleaned_term = designation.term
    canonical_language = designation.language
    assert_no_error_collisions(
        session, entry=entry, term=cleaned_term, language=canonical_language, use=designation.use
    )
    designation.status = str(DesignationStatus.ACTIVE)
    designation.retired_at = None
    try:
        record_change(
            session,
            ctx,
            action="designation.reinstated",
            instance=designation,
            kind=ChangeKind.UPDATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {canonical_language!r}"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT:
            raise PreferredDesignationAlreadyActiveError(
                f"entry {entry_id} already has an active preferred designation "
                f"in language {canonical_language!r}"
            ) from exc
        raise
    return designation


def amend_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    designation: Designation,
    new_term: str,
    reason: str,
) -> Designation:
    """Edits `designation.term` in place, re-running FR-05's error-severity
    collision check against the new value first - the same precondition-
    before-mutation posture `add_designation` uses. Chosen over retire-and-
    re-add (issue #224) so the row keeps its identity (`id`) and the audit
    log shows one `designation.amended` edit, not a retirement paired with
    an unrelated-looking creation.

    `entry_id` is immutable (`Designation`'s own `@validates` hook), so
    this can never reparent a designation - only ever change the term it
    holds. `entry` is required, not derived from `designation.entry_id`,
    so the collision check can exclude this entry's own other designations
    the same way `add_designation` does - the caller (already having
    resolved both via `load_entry_for_update`/`load_active_designation`)
    has them both on hand.

    `acquire_append_lock` runs as the literal first statement, for the same
    reason `add_designation` now does (issue #281 round-2 review): makes
    the append-lock-before-collision-lock invariant hold at this function's
    own boundary rather than depending on every caller wrapping it in
    `entry_child_write` correctly."""
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(
            f"designation {designation.id} is retired and cannot be amended"
        )

    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(new_term)
    if cleaned_term == designation.term:
        # A no-op edit: nothing to check the term against itself for, and
        # a same-value "edit" audit event would misrepresent that nothing
        # changed - the same reasoning `retire_designation`'s guard against
        # a double retirement applies, just without needing its own
        # exception type (submitting the term a designation already holds
        # is not a caller mistake worth surfacing).
        return designation

    # Captured into locals before the flush below, not re-read from the
    # ORM instances inside `except`: a failed flush leaves every instance
    # the session tracks expired, so touching an already-loaded attribute
    # afterwards triggers a reload against a session that is not yet
    # rolled back, raising `PendingRollbackError` in place of the domain
    # error this is meant to raise (issue #224 review).
    entry_id = entry.id
    designation_language = designation.language
    assert_no_error_collisions(
        session,
        entry=entry,
        term=cleaned_term,
        language=designation_language,
        use=designation.use,
    )
    designation.term = cleaned_term
    try:
        record_change(
            session,
            ctx,
            action="designation.amended",
            instance=designation,
            kind=ChangeKind.UPDATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {designation_language!r}"
            ) from exc
        raise
    return designation
