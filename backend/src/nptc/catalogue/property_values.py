"""Validates and writes a property's values as a whole (FR-09, FR-10, FR-88,
FR-89).

**Outside `nptc.registry`.** This module imports `nptc.db` and `nptc.audit`,
which that package's leaf rule forbids it to import (ADR-0013 SS2). The
registry supplies the pure decision (`nptc.registry.schema.validate_values`);
this module is the one write path that acts on it.

**Validate everything, then mutate, then flush once.** `save_property_values`
adds or deletes no row until every incoming value has been checked, so a
rejected write leaves no `PropertyValue` row and no partial state.

**Whole-property replace, not a diff.** A write supplies the complete list of
values. Existing rows for `(entry, property_key)` are deleted and the supplied
values inserted at ordinals `0..n-1`. This applies `property_value`'s "every
write MUST replace the whole attribute" at the row-set level, and it keeps
cardinality's upper bound (ADR-0012, `validate_values`) meaningful: a caller
cannot bypass it by adding one row at a time. ADR-0035 records why the bulk
seam does not diff either.

**FR-89 (ADR-0044).** The specimen root `123038009` means "any specimen", so it stands
alone in the `specimen` value set. That is a rule about one property's whole set, not about an
entry, so `_validate_specimen_root_alone` runs with the other whole-request checks in the
preflight, before any entry is loaded. Every other property's validation is generic.

**FR-10's binding-strength override lives here, not in `CodeHandler`.**
`CodeHandler.validate` never sees a `justification`: that is the sibling
column `property_value.justification`, not part of the JSONB `value` a handler
validates. It therefore always reports an out-of-value-set code as
`not-in-value-set`. `_apply_binding_strength` drops that issue for `example`
(advisory) and for `extensible` when the matching
`PropertyValueInput.justification` is non-blank. For `extensible` without one,
it rewords the issue to name the missing justification. `required` is never
overridden.

**`save_property_values_for_entries` is the bulk seam (FR-39)**, not
`nptc.catalogue.entries.save_entries`: discipline and every other coded
property are `property_value` rows, not `catalogue_entry` columns. It checks
`reason` and the shared `values` once before its loop (ADR-0035).

**Lock order.** Every writer here takes the audit append lock before any
session-touching statement, so no `catalogue_entry` row lock precedes it. Only
session-free validation (the changelog note) runs ahead of it, so a rejected
request takes no lock. ADR-0035 records the deadlock cycles this closes, and
`test_lock_ordering.py` pins it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal

from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import AuditFieldPolicy
from nptc.audit.recording import record_batch_summary, record_snapshot_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.entries import assert_entry_row_version, load_entry_for_update
from nptc.catalogue.errors import ConflictReport, EntryNotFoundError, EntryVersionConflictError
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_definition import PropertyDefinition, PropertyStatus
from nptc.db.models.property_value import PropertyValue
from nptc.db.property_specs import spec_for
from nptc.registry.definitions import DeprecatedPropertyWriteError
from nptc.registry.handlers import DatatypeRegistry, PropertyDefinitionSpec, ValidationIssue
from nptc.registry.schema import validate_constraints, validate_values
from nptc_shared.terminology.models import SPECIMEN_ROOT_CODE

__all__ = [
    "BulkPropertyOutcome",
    "EntryPropertyTarget",
    "PropertyDefinitionNotFoundError",
    "PropertyValidationError",
    "PropertyValueInput",
    "PropertyWriteIssue",
    "save_property_values",
    "save_property_values_for_entries",
    "tally_bulk_outcomes",
]

#: `property_value.bulk_set`'s `entity_type`, distinct from `property_value_set`
#: (the per-entry events): the diff-free header has a different `entity_id`
#: grammar, and a history query scoped to `property_value_set` must not pick it
#: up (ADR-0035).
_BULK_ENTITY_TYPE: Final[str] = "property_value_bulk"

#: The one issue code eligible for a strength override. `strength` governs
#: FR-10's value-set check, not whether a value is valid in general.
_NOT_IN_VALUE_SET = "not-in-value-set"

#: The one property this module knows by name (FR-89); see the module docstring.
_SPECIMEN_KEY = "specimen"

_SPECIMEN_ROOT_CONFLICT_MESSAGE: Final[str] = (
    f"the specimen root ({SPECIMEN_ROOT_CODE}) means any specimen and must be the only specimen "
    "value on an entry - remove it, or remove the named specimens"
)

#: A synthetic policy for the whole-set audit snapshot, not
#: `policy_for(PropertyValue)`, which classifies per-row columns. A save replaces
#: a row set, so the diffed unit is the property's value list before and after
#: (ADR-0040).
_PROPERTY_VALUES_AUDIT_POLICY = AuditFieldPolicy(
    entity_type="property_value_set",
    auditable=frozenset({"values"}),
    withheld=frozenset(),
    ignored=frozenset(),
    known=frozenset({"values"}),
)


class PropertyDefinitionNotFoundError(LookupError):
    """Raised when no `property_definition` matches the given key: a caller
    error (an unknown or mistyped property), not a bad value."""

    http_status: ClassVar[int] = 404


@dataclass(frozen=True)
class PropertyValueInput:
    """One value to save with its own `justification`. FR-10's
    extensible-strength case needs both, and they are sibling columns on
    `property_value`, not one JSONB document."""

    value: Any
    justification: str | None = None


@dataclass(frozen=True)
class PropertyWriteIssue:
    """One field-level problem with an attempted write, worded as PRD SS17.2.5
    requires: what was wrong and what to do about it, with no stack trace, schema
    dump or HTTP status. `ordinal` is `None` for a cardinality issue on the
    property as a whole."""

    property_key: str
    label: str
    code: str
    message: str
    ordinal: int | None = None


@dataclass(eq=False)
class PropertyValidationError(ValueError):
    """Raised by `save_property_values` when supplied values fail validation.
    Carries every issue found (`validate_values` never stops at the first), so a
    caller can show one message per bad value.

    **Not `frozen=True`.** FastAPI's sync-dependency-to-thread bridge
    (`contextmanager_in_threadpool`, used for `get_session`) reassigns
    `exc.__traceback__` when it hands a raised exception across the thread
    boundary. A frozen dataclass's `__setattr__` refuses that, which turned every
    422 into an unhandled `FrozenInstanceError` (500).

    **`eq=False`.** A dataclass without `frozen=True` still generates `__eq__`,
    which sets `__hash__` to `None`: instances become unhashable, and two unrelated
    raises with the same `issues` compare equal. `eq=False` keeps `object`'s
    identity-based `__eq__` and `__hash__`.
    """

    issues: tuple[PropertyWriteIssue, ...] = field(default_factory=tuple)
    http_status: ClassVar[int] = 422

    def __post_init__(self) -> None:
        # `Exception.__init__` does not run for this dataclass, so `args` would
        # stay `()` and `repr()` or `logging.exception` would show no issues.
        self.args = (str(self),)

    def __str__(self) -> str:
        return f"{len(self.issues)} property value issue(s): " + "; ".join(
            f"{issue.property_key}: {issue.message}" for issue in self.issues
        )


def _validate_specimen_root_alone(
    property_key: str, values: Sequence[Any]
) -> Sequence[PropertyWriteIssue]:
    """FR-89: the specimen root means "any specimen", so it cannot sit beside a named
    specimen. The issue names the root's `ordinal`."""
    if property_key != _SPECIMEN_KEY:
        return ()
    codes = [
        value["code"] if isinstance(value, dict) and isinstance(value.get("code"), str) else None
        for value in values
    ]
    named = [code for code in codes if code is not None and code != SPECIMEN_ROOT_CODE]
    if SPECIMEN_ROOT_CODE not in codes or not named:
        return ()
    return (
        PropertyWriteIssue(
            property_key=property_key,
            label="Specimen",
            code="specimen-root-conflict",
            message=_SPECIMEN_ROOT_CONFLICT_MESSAGE,
            ordinal=codes.index(SPECIMEN_ROOT_CODE),
        ),
    )


def _apply_binding_strength(
    issues: Sequence[ValidationIssue],
    spec: PropertyDefinitionSpec,
    inputs: Sequence[PropertyValueInput],
) -> Sequence[ValidationIssue]:
    """Drops or rewords a `not-in-value-set` issue per FR-10's strength rule; the
    module docstring explains why this is not in `CodeHandler`. Every other issue
    code passes through untouched."""
    strength = spec.binding.strength if spec.binding is not None else None
    if strength not in ("extensible", "example"):
        return issues  # required (or no binding at all): never overridden

    kept: list[ValidationIssue] = []
    for issue in issues:
        if issue.code != _NOT_IN_VALUE_SET:
            kept.append(issue)
            continue
        if strength == "example":
            # Advisory only - FHIR's weakest strength constrains nothing.
            continue
        ordinal = int(issue.path) if issue.path is not None else None
        justification = (
            inputs[ordinal].justification
            if ordinal is not None and 0 <= ordinal < len(inputs)
            else None
        )
        if justification is not None and justification.strip():
            continue  # extensible, with a recorded justification: accepted
        kept.append(
            ValidationIssue(
                code="justification-required",
                message=(
                    f"{issue.message} - this property accepts an out-of-value-set code "
                    "if you record why (a justification), which is missing here"
                ),
                path=issue.path,
            )
        )
    return kept


@dataclass(frozen=True)
class _PropertyWritePreflight:
    """The whole-request part of a write: everything derivable from the property
    definition and the shared `values` alone, with no dependency on any entry's
    state."""

    definition: PropertyDefinition
    write_issues: tuple[PropertyWriteIssue, ...]


def _load_active_property_definition(session: Session, property_key: str) -> PropertyDefinition:
    """Raises `PropertyDefinitionNotFoundError` (404) for an unknown key and
    `DeprecatedPropertyWriteError` (FR-11) for one no longer accepting new
    values - both whole-request checks, run once per write regardless of
    how many entries it targets."""
    definition = session.execute(
        select(PropertyDefinition).where(PropertyDefinition.key == property_key)
    ).scalar_one_or_none()
    if definition is None:
        raise PropertyDefinitionNotFoundError(f"no property_definition with key {property_key!r}")
    if definition.status == PropertyStatus.DEPRECATED:
        # FR-11: checked before validation, so a deprecated property never
        # reaches a cardinality or binding check whose outcome would be moot.
        raise DeprecatedPropertyWriteError(property_key)
    return definition


def _preflight_property_write(
    definition: PropertyDefinition,
    values: Sequence[PropertyValueInput],
    registry: DatatypeRegistry,
) -> _PropertyWritePreflight:
    """Validates `values` against `definition`'s spec, independent of any
    entry: schema shape, cardinality, FR-10's binding-strength override and FR-89's
    specimen root. Never raises: a bad value is a `PropertyWriteIssue`, not an
    exception, so a caller decides whether to raise `PropertyValidationError`."""
    spec = spec_for(definition)
    handler = registry.get(definition.datatype)
    # A malformed `constraints` document is a defect in the definition, not
    # something the caller could avoid. Checked before any value, so a bad
    # definition never fails open (see `CodeHandler.validate`'s fallback).
    validate_constraints(spec, handler)
    raw_values = tuple(item.value for item in values)

    schema_issues = validate_values(raw_values, spec, handler, row_version=definition.row_version)
    schema_issues = _apply_binding_strength(schema_issues, spec, values)
    write_issues = (
        *(
            PropertyWriteIssue(
                property_key=definition.key,
                label=definition.label,
                code=issue.code,
                message=issue.message,
                ordinal=int(issue.path) if issue.path is not None else None,
            )
            for issue in schema_issues
        ),
        *_validate_specimen_root_alone(definition.key, raw_values),
    )
    return _PropertyWritePreflight(definition=definition, write_issues=write_issues)


def save_property_values(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    property_key: str,
    values: Sequence[PropertyValueInput],
    reason: str,
    registry: DatatypeRegistry,
    expected_row_version: int,
) -> Sequence[PropertyValue]:
    """Replaces every `property_value` row for `(entry, property_key)` with
    `values`, validated as a whole set before any row is touched.

    Raises `PropertyDefinitionNotFoundError` for an unknown key,
    `nptc.catalogue.changelog.ChangelogNoteError` for a rejected `reason`,
    `EntryVersionConflictError` (FR-38) if `expected_row_version` no longer
    matches `entry.row_version`, and `PropertyValidationError` (never a bare
    `IntegrityError`) for a value or cardinality problem. Each is raised before
    `session.add` or `session.delete`, so a rejected write leaves neither a
    partial write nor an audit event (FR-37).

    `expected_row_version` guards this write although it only touches
    `property_value` rows. With no per-row version of its own, two editors saving
    the same property would otherwise clobber each other, which FR-38 forbids.
    `entry.row_version` is checked and bumped as the one lock per entry; see
    `assert_entry_row_version`.

    Returns the newly inserted rows, ordered by ordinal.

    `reason` is validated first, then `acquire_append_lock` runs before any
    session-touching statement, unconditionally. Placing it after the no-op
    short-circuit would leave the identity flush,
    `_load_active_property_definition`'s `select()` and the `existing` query ahead
    of it, and each autoflushes pending `catalogue_entry` state and so can take a
    row lock first (ADR-0035). This gives up "a no-op takes no lock" for a
    guarantee that does not depend on caller discipline.
    """
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)

    # A new, unflushed `entry` has no identity: `entry.id` would match no existing
    # rows and insert `PropertyValue` rows with a NULL `entry_id`. `create_binding`
    # has the same guard.
    if not sa_inspect(entry).identity:
        session.flush()

    if entry.row_version != expected_row_version:
        # `changed_by` and `changed_at` stay unpopulated: the attribution lookup
        # is private to `nptc.catalogue.entries`, and `ConflictReport` treats
        # both as optional.
        raise EntryVersionConflictError(
            ConflictReport(
                business_key=entry.business_key,
                expected_row_version=expected_row_version,
                current_row_version=entry.row_version,
            )
        )

    definition = _load_active_property_definition(session, property_key)
    preflight = _preflight_property_write(definition, values, registry)
    if preflight.write_issues:
        raise PropertyValidationError(preflight.write_issues)

    existing = (
        session.execute(
            select(PropertyValue)
            .where(
                PropertyValue.entry_id == entry.id,
                PropertyValue.property_key == property_key,
            )
            .order_by(PropertyValue.ordinal)
        )
        .scalars()
        .all()
    )
    before_payload: Mapping[str, object] = {"values": [_value_payload(row) for row in existing]}
    intended_after_payload: Mapping[str, object] = {
        "values": [
            {"ordinal": ordinal, "value": item.value, "justification": item.justification}
            for ordinal, item in enumerate(values)
        ]
    }

    # A no-op write is detected before any row is touched; comparing after the
    # DELETE/INSERT would leave a pointless write in the transaction. It also
    # keeps a no-op from bumping `entry.row_version`, which would invalidate a
    # concurrent editor's still-current version.
    if before_payload == intended_after_payload:
        return existing

    if existing:
        session.execute(
            delete(PropertyValue).where(
                PropertyValue.entry_id == entry.id,
                PropertyValue.property_key == property_key,
            )
        )

    inserted = [
        PropertyValue(
            entry_id=entry.id,
            property_key=property_key,
            ordinal=ordinal,
            value=item.value,
            justification=item.justification,
        )
        for ordinal, item in enumerate(values)
    ]
    for row in inserted:
        session.add(row)

    # Bumps the shared per-entry lock (see the docstring), so a concurrent
    # `save_property_values` or `save_entry` sees a stale `expected_row_version`.
    entry.row_version += 1
    session.flush()

    after_payload: Mapping[str, object] = {"values": [_value_payload(row) for row in inserted]}

    record_snapshot_change(
        session,
        ctx,
        action="property_value.set",
        entity_type="property_value_set",
        entity_id=f"{entry.id}:{property_key}",
        policy=_PROPERTY_VALUES_AUDIT_POLICY,
        before=before_payload if existing else None,
        after=after_payload if inserted else None,
        kind=_change_kind(existing=bool(existing), inserted=bool(inserted)),
        reason=validated_reason,
    )

    return inserted


@dataclass(frozen=True)
class EntryPropertyTarget:
    """One `(business_key, expected_row_version)` pair selected for a bulk write
    (FR-39). The version is the one the entry held when selected, not resolved
    server-side, which would leave a conflict check nothing to lock against."""

    business_key: str
    expected_row_version: int


#: See `BulkPropertyOutcome` for what each value means.
_BulkOutcomeStatus = Literal["applied", "unchanged", "conflict", "not-found"]


@dataclass(frozen=True)
class BulkPropertyOutcome:
    """One target's result from `save_property_values_for_entries`, at
    index `i` in `targets` order - a caller zips its own request against
    this to see which entry got which outcome.

    `row_version` is the value *after* the attempt: bumped for `applied`,
    unchanged for `unchanged`, the entry's actual current version for
    `conflict` (so the caller has what it needs to retry), and `None` for
    `not-found` (there is no entry to report a version for). `conflict`
    carries the domain `ConflictReport` - never a wire model, so this
    module stays free of any `nptc.api` import - and is populated only
    when `status == "conflict"`."""

    business_key: str
    status: _BulkOutcomeStatus
    row_version: int | None
    conflict: ConflictReport | None = None


def _not_found_outcome(business_key: str) -> BulkPropertyOutcome:
    return BulkPropertyOutcome(business_key=business_key, status="not-found", row_version=None)


def _conflict_outcome(business_key: str, report: ConflictReport) -> BulkPropertyOutcome:
    return BulkPropertyOutcome(
        business_key=business_key,
        status="conflict",
        row_version=report.current_row_version,
        conflict=report,
    )


def save_property_values_for_entries(
    session: Session,
    ctx: AuditContext,
    *,
    targets: Sequence[EntryPropertyTarget],
    property_key: str,
    values: Sequence[PropertyValueInput],
    reason: str,
    registry: DatatypeRegistry,
) -> tuple[BulkPropertyOutcome, ...]:
    """Sets `property_key` to `values` across every entry in `targets`, one
    `save_property_values` call and one savepoint per entry, so a stale or missing
    entry never blocks the rest (FR-39, ADR-0035). Returns exactly `len(targets)`
    outcomes, in `targets` order.

    **Whole-request versus per-entry.** Anything derivable from `property_key`,
    `values` and `reason` alone is checked once before the loop: `reason` (FR-37),
    the property definition (404, FR-11), and the shared `values` schema,
    cardinality and binding-strength validation. That keeps the batch
    order-independent: a batch whose first targets all conflict must still refuse a
    bad `values` or `reason`. A stale `expected_row_version` and a missing entry
    depend on one entry and are per-entry outcomes.

    Raises `PropertyDefinitionNotFoundError`, `DeprecatedPropertyWriteError`,
    `nptc.catalogue.changelog.ChangelogNoteError` or `PropertyValidationError` for
    a whole-request problem. A raise here leaves no write and no audit event:
    `session_scope` rolls back the whole transaction, discarding entries already
    applied.

    `reason` is validated first, then `acquire_append_lock` runs before any
    session-touching statement. Taking it once here, not leaving it to the first
    applied entry's audit append, matters for a batch whose earliest entries are
    all `unchanged`. A placement after `_load_active_property_definition`'s
    `select()` would reopen the autoflush gap (ADR-0035).

    An entry deleted by another transaction between the row-version pre-check and
    this call's flush (`ObjectDeletedError`) becomes `not-found`, as for a
    `business_key` that never existed.

    A `property_value.bulk_set` header is appended once after the loop, only when
    at least one target applied (`tally_bulk_outcomes`). A batch of only
    `conflict` or `not-found` outcomes changed nothing and emits nothing
    (ADR-0035, ADR-0018).
    """
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)

    definition = _load_active_property_definition(session, property_key)
    preflight = _preflight_property_write(definition, values, registry)
    if preflight.write_issues:
        raise PropertyValidationError(preflight.write_issues)

    outcomes: list[BulkPropertyOutcome] = []

    for target in targets:
        try:
            entry = load_entry_for_update(session, target.business_key)
        except EntryNotFoundError:
            outcomes.append(_not_found_outcome(target.business_key))
            continue

        try:
            assert_entry_row_version(session, entry, target.expected_row_version)
        except EntryVersionConflictError as exc:
            outcomes.append(_conflict_outcome(target.business_key, exc.report))
            continue

        before_version = entry.row_version
        # One savepoint per entry, around the write only; `save_property_values`
        # opens none. Without it, a `version_id_col` collision at flush (past the
        # precondition check) would abort the whole batch and could leave this
        # entry's rows deleted but not reinserted, as in `save_entry`'s layer two.
        savepoint = session.begin_nested()
        try:
            save_property_values(
                session,
                ctx,
                entry=entry,
                property_key=property_key,
                values=values,
                reason=validated_reason,
                registry=registry,
                expected_row_version=target.expected_row_version,
            )
            savepoint.commit()
        except StaleDataError, ObjectDeletedError:
            savepoint.rollback()
            session.expire(entry)
            try:
                refreshed = load_entry_for_update(session, target.business_key)
            except EntryNotFoundError:
                # Another transaction deleted the row between the precondition
                # check and this flush. No entry is left to report a conflict
                # against, and an escaping `EntryNotFoundError` would turn a
                # partly successful batch into a whole-request 404, whose
                # meaning is "unknown property_key".
                outcomes.append(_not_found_outcome(target.business_key))
                continue
            try:
                # Reuses the pre-check's conflict path, so attribution does not
                # depend on which layer caught it. `row_version` only increases
                # and the flush already found it past `target.expected_row_version`,
                # so this is expected to raise.
                assert_entry_row_version(session, refreshed, target.expected_row_version)
            except EntryVersionConflictError as exc:
                outcomes.append(_conflict_outcome(target.business_key, exc.report))
                continue
            # Defensive: the check above was expected to raise. The write's
            # savepoint was rolled back, so `unchanged` would falsely claim the
            # entry already holds the target values (for example after a delete
            # and recreate under the same `business_key` landing at
            # `row_version=1`). Report `conflict`, built from `refreshed`.
            outcomes.append(
                _conflict_outcome(
                    target.business_key,
                    ConflictReport(
                        business_key=refreshed.business_key,
                        expected_row_version=target.expected_row_version,
                        current_row_version=refreshed.row_version,
                    ),
                )
            )
            continue

        if entry.row_version == before_version:
            outcomes.append(
                BulkPropertyOutcome(
                    business_key=target.business_key,
                    status="unchanged",
                    row_version=entry.row_version,
                )
            )
        else:
            outcomes.append(
                BulkPropertyOutcome(
                    business_key=target.business_key,
                    status="applied",
                    row_version=entry.row_version,
                )
            )

    result = tuple(outcomes)
    tallies = tally_bulk_outcomes(result)

    # A no-effect batch emits no header (ADR-0035), so a client retrying a stale
    # selection writes no audit row per attempt. The header shares
    # `ctx.correlation_id` with the per-entry events (NFR-08).
    if tallies["applied"] > 0:
        record_batch_summary(
            session,
            ctx,
            action="property_value.bulk_set",
            entity_type=_BULK_ENTITY_TYPE,
            entity_id=property_key,
            reason=validated_reason,
            tallies=tallies,
        )

    return result


def tally_bulk_outcomes(outcomes: Sequence[BulkPropertyOutcome]) -> dict[str, int]:
    """Counts `outcomes` by `status`. The one place the audit header and the
    HTTP response both count a batch, so the two cannot drift apart (ADR-0035)."""
    tallies: dict[str, int] = {"applied": 0, "unchanged": 0, "conflict": 0, "not-found": 0}
    for outcome in outcomes:
        tallies[outcome.status] += 1
    return tallies


def _value_payload(row: PropertyValue) -> Mapping[str, object]:
    return {"ordinal": row.ordinal, "value": row.value, "justification": row.justification}


def _change_kind(*, existing: bool, inserted: bool) -> ChangeKind:
    if not existing:
        return ChangeKind.CREATED
    if not inserted:
        return ChangeKind.DELETED
    return ChangeKind.UPDATED
