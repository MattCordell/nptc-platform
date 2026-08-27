"""Validates and writes a property's values as a whole (issue #52, FR-09,
FR-10, FR-88, FR-89).

**Outside `nptc.registry`, deliberately.** This module imports `nptc.db`
(the ORM models) and `nptc.audit`, both of which `nptc.registry`'s own
leaf rule (ADR-0013 SS2) forbids that package from importing. The
registry supplies the pure decision (`nptc.registry.schema.
validate_values`); this module is the one write path that acts on it.

**Validate everything, then mutate, then flush once.** `save_property_
values` never adds or deletes a row until every value in the incoming set
has been checked - a rejected write raises before `session.add`/`session.
delete` is called at all, so it leaves no `PropertyValue` row and no
partial state (this issue's own acceptance criterion). Compare
`nptc.catalogue.entries.save_entry`'s row-version check, which takes the
same "reject before touching the row" posture for a different precondition.

**Whole-property replace, not a diff against the existing rows.** A write
supplies the complete list of values a property should hold afterwards;
existing rows for `(entry, property_key)` are deleted and the supplied
values inserted fresh at ordinals `0..n-1`. This is what
`property_value`'s own model docstring means by "every write MUST replace
the whole attribute" - applied one level up, at the row-set level rather
than a single JSONB value - and it is what makes cardinality's upper
bound (ADR-0012, enforced by `nptc.registry.schema.validate_values`)
actually meaningful: a caller cannot bypass it by adding one row at a time
without ever supplying the full set for validation.

**FR-89's cross-field invariant.** `specimen_unconstrained = true`
(PRD S6.2) asserts "this test accepts any specimen", which is a fact about
the *entry*, not the *property* - so it cannot live inside
`nptc.registry.schema.validate_values`, which only ever sees one
property's own values. `_validate_specimen_cross_field` is the one piece
of specimen-specific knowledge in this module, checked only when
`property_key == "specimen"`; every other property's validation is
entirely generic.

**FR-10's binding-strength override lives here, not in `CodeHandler`.**
`CodeHandler.validate(value, spec)` never sees a `justification` - that
text is `property_value.justification`, a sibling column, not part of the
JSONB `value` a handler validates - so it always reports an out-of-value-
set code as `not-in-value-set` regardless of `strength`. This module is
where the value and its justification are both in hand:
`_apply_binding_strength` drops that issue for a `required` strength never
(it always blocks), drops it for `example` unconditionally (advisory:
FHIR's weakest strength constrains nothing), and drops it for `extensible`
only when the matching `PropertyValueInput.justification` is non-blank -
otherwise the issue survives, now naming the missing justification rather
than the raw binding failure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import AuditFieldPolicy
from nptc.audit.recording import record_snapshot_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.errors import ConflictReport, EntryVersionConflictError
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.property_value import PropertyValue
from nptc.registry.handlers import (
    BindingSpec,
    DatatypeRegistry,
    PropertyDefinitionSpec,
    ValidationIssue,
)
from nptc.registry.schema import validate_constraints, validate_values

__all__ = [
    "PropertyDefinitionNotFoundError",
    "PropertyValidationError",
    "PropertyValueInput",
    "PropertyWriteIssue",
    "save_property_values",
]

#: A binding value-set failure - the one issue code eligible for a
#: strength-based override. Every other issue code (a schema/shape
#: failure, `forbidden-code`, `not-a-local-code`, a cardinality bound) is
#: never eligible: `strength` governs FR-10's value-set check specifically,
#: not "whether this value is valid" in general.
_NOT_IN_VALUE_SET = "not-in-value-set"

#: The one property this module has specific knowledge of (FR-89) - see
#: the module docstring's cross-field note. Every other property is
#: handled entirely generically.
_SPECIMEN_KEY = "specimen"

#: A synthetic policy for the audit snapshot this module records - not
#: `nptc.audit.policy.policy_for(PropertyValue)`, which classifies that
#: model's own per-row columns (`entry_id`/`property_key`/`ordinal`/...).
#: A `save_property_values` call replaces a whole row *set* in one
#: transaction, so the thing worth diffing is "the property's value list
#: before" vs "...after", not any one row's attribute history.
_PROPERTY_VALUES_AUDIT_POLICY = AuditFieldPolicy(
    entity_type="property_value_set",
    auditable=frozenset({"values"}),
    withheld=frozenset(),
    ignored=frozenset(),
    known=frozenset({"values"}),
)


class PropertyDefinitionNotFoundError(LookupError):
    """Raised when no `property_definition` matches the given key - a
    caller error (an unknown or mistyped property), not a bad value."""

    http_status: ClassVar[int] = 404


@dataclass(frozen=True)
class PropertyValueInput:
    """One value to save, paired with its own `justification` - FR-10's
    extensible-strength case needs both together, and `property_value`'s
    own `value`/`justification` columns are siblings on the same row, not
    a single JSONB document, so a caller cannot supply just `value` and
    expect this module to find a justification anywhere else."""

    value: Any
    justification: str | None = None


@dataclass(frozen=True)
class PropertyWriteIssue:
    """One field-level problem with an attempted write, in the language
    PRD SS17.2.5 requires: says what was wrong and, via `message`, what to
    do about it - never a stack trace, a raw schema-validation dump, or an
    HTTP status. `ordinal` is `None` for a cardinality issue that applies
    to the property as a whole rather than one value in it."""

    property_key: str
    label: str
    code: str
    message: str
    ordinal: int | None = None


@dataclass(frozen=True)
class PropertyValidationError(ValueError):
    """Raised by `save_property_values` when one or more supplied values
    fail validation. Carries every issue found in one round trip (`nptc.
    registry.schema.validate_values` never stops at the first problem), so
    a caller can show a field-level message per bad value rather than
    forcing a fix-one-submit-again loop. `http_status` follows the same
    ClassVar convention `EntryVersionConflictError`/`ChangelogNoteError`
    already use, so `nptc.api.errors` can map this without a new pattern
    (see that module's own note that #149/#150 must not simply inherit it
    unchanged - this handler is the first purpose-built one for a
    property-value write)."""

    issues: tuple[PropertyWriteIssue, ...] = field(default_factory=tuple)
    http_status: ClassVar[int] = 422

    def __post_init__(self) -> None:
        # A frozen dataclass subclassing `ValueError` never runs
        # `Exception.__init__`, so `self.args` stays `()` - `repr()` and a
        # bare `logging.exception(exc)` would otherwise show nothing about
        # which issues were raised. `object.__setattr__` is required here:
        # `frozen=True` blocks the plain assignment `self.args = ...` even
        # inside `__post_init__`.
        object.__setattr__(self, "args", (str(self),))

    def __str__(self) -> str:
        return f"{len(self.issues)} property value issue(s): " + "; ".join(
            f"{issue.property_key}: {issue.message}" for issue in self.issues
        )


def _binding_spec(definition: PropertyDefinition) -> BindingSpec | None:
    if definition.binding_target is None:
        return None
    return BindingSpec(
        binding_target=definition.binding_target,
        value_set_uri=definition.value_set_uri,
        strength=definition.strength or "",
        edition=definition.edition or "",
        local_code_system_key=definition.local_code_system_key,
    )


def _scope(definition: PropertyDefinition) -> frozenset[str]:
    if definition.scope == "both":
        return frozenset({"submission", "maintenance"})
    return frozenset({definition.scope})


def _spec_for(definition: PropertyDefinition) -> PropertyDefinitionSpec:
    """The frozen view `nptc.registry` handlers and `validate_values`
    take - never the ORM row itself (ADR-0013 SS2's leaf rule: `nptc.
    registry` must not import `nptc.db`, so the conversion happens here,
    on the `nptc.catalogue` side of that boundary)."""
    return PropertyDefinitionSpec(
        key=definition.key,
        label=definition.label,
        datatype=definition.datatype,
        cardinality=definition.cardinality,
        scope=_scope(definition),
        required_for_submission=definition.required_for_submission,
        required_for_publication=definition.required_for_publication,
        binding=_binding_spec(definition),
        filterable=definition.filterable,
        constraints=definition.constraints,
    )


def _validate_specimen_cross_field(
    entry: CatalogueEntry, property_key: str, values: Sequence[Any]
) -> Sequence[PropertyWriteIssue]:
    """FR-89: `specimen_unconstrained = true` asserts "this test accepts
    any specimen" - an entry cannot claim that *and* carry one or more
    specimen values at the same time; the two facts are mutually
    exclusive, not merely redundant. See PRD S6.2 for the flag's full
    rationale (distinguishing "accepts any specimen" from "nobody has
    filled this in yet")."""
    if property_key != _SPECIMEN_KEY or not entry.specimen_unconstrained or not values:
        return ()
    return (
        PropertyWriteIssue(
            property_key=property_key,
            label="Specimen",
            code="specimen-unconstrained-conflict",
            message=(
                "this entry is marked as accepting any specimen "
                "(specimen_unconstrained) - clear that flag before recording "
                "a specimen value, or remove the specimen value before setting it"
            ),
        ),
    )


def _apply_binding_strength(
    issues: Sequence[ValidationIssue],
    spec: PropertyDefinitionSpec,
    inputs: Sequence[PropertyValueInput],
) -> Sequence[ValidationIssue]:
    """Drops or rewords a `not-in-value-set` issue per FR-10's strength
    rule - see the module docstring's own note on why this cannot live in
    `CodeHandler` itself. Every other issue code passes through untouched."""
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
    """Replaces every `property_value` row for `(entry, property_key)`
    with `values`, validated as a whole set before any row is touched.

    Raises `PropertyDefinitionNotFoundError` for an unknown `property_key`,
    `nptc.catalogue.changelog.ChangelogNoteError` for a rejected `reason`,
    `EntryVersionConflictError` (FR-38) if `expected_row_version` no longer
    matches `entry.row_version`, and `PropertyValidationError` (never a bare
    `IntegrityError`) for a value or cardinality problem - each raised
    before `session.add`/`session.delete` is ever called, so a rejected
    write leaves neither a partial write nor an audit event behind,
    matching `save_entry`'s own precondition-before-mutation posture
    (FR-37).

    **Why `expected_row_version` guards this write, even though it only
    ever touches `property_value` rows.** `save_property_values` is a
    whole-property replace with no per-row version of its own to check -
    two editors who each load the same entry, then each save a change to
    the *same* property, would otherwise silently clobber one another
    (last write wins, with a plausible-looking audit trail for both),
    which is exactly the outcome FR-38 forbids for `catalogue_entry`
    itself. Checking `entry.row_version` and then bumping it as part of
    this write (rather than adding a second, `property_value`-scoped
    version column) reuses `catalogue_entry.row_version`'s existing
    `version_id_col` machinery as the one optimistic lock a caller needs
    to track per entry, covering both this path and `save_entry`'s.

    Returns the newly inserted rows, ordered by ordinal.
    """
    validated_reason = validate_changelog_note(reason)

    # `entry.id` is read into every query/insert below - a brand-new,
    # not-yet-flushed `entry` has no identity yet, which would either match
    # zero existing rows on a query that should have found some, or try to
    # insert PropertyValue rows with a NULL entry_id. Flushing first, only
    # when needed, closes that gap - mirrors `nptc.catalogue.bindings.
    # create_binding`'s identical guard for the same "create the entry and
    # its dependent row in one transaction" call pattern.
    if not sa_inspect(entry).identity:
        session.flush()

    if entry.row_version != expected_row_version:
        # `changed_by`/`changed_at` are left unpopulated here, unlike
        # `nptc.catalogue.entries.save_entry`'s own conflict report: that
        # attribution lookup (`_latest_change_attribution`) is a private
        # helper of that module, and duplicating it for this one field
        # would widen this fix beyond the concurrency guard itself.
        # `ConflictReport` already treats both as optional for exactly
        # this case.
        raise EntryVersionConflictError(
            ConflictReport(
                business_key=entry.business_key,
                expected_row_version=expected_row_version,
                current_row_version=entry.row_version,
            )
        )

    definition = session.execute(
        select(PropertyDefinition).where(PropertyDefinition.key == property_key)
    ).scalar_one_or_none()
    if definition is None:
        raise PropertyDefinitionNotFoundError(f"no property_definition with key {property_key!r}")

    spec = _spec_for(definition)
    handler = registry.get(definition.datatype)
    # A malformed `constraints` document is a defect in the *definition*,
    # not something this write's caller could have avoided - checked before
    # any value is judged against it, so a bad definition never fails open
    # (see `CodeHandler.validate`'s own defensive fallback for the case
    # where it does anyway).
    validate_constraints(spec, handler)
    raw_values = [item.value for item in values]

    schema_issues = validate_values(raw_values, spec, handler, row_version=definition.row_version)
    schema_issues = _apply_binding_strength(schema_issues, spec, values)
    write_issues = [
        PropertyWriteIssue(
            property_key=property_key,
            label=definition.label,
            code=issue.code,
            message=issue.message,
            ordinal=int(issue.path) if issue.path is not None else None,
        )
        for issue in schema_issues
    ]
    write_issues.extend(_validate_specimen_cross_field(entry, property_key, raw_values))
    if write_issues:
        raise PropertyValidationError(tuple(write_issues))

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

    # A no-op write (the same values resubmitted) is checked *before* any
    # row is touched: comparing payloads after the DELETE/INSERT would
    # still leave a real (if pointless) write in the transaction, and this
    # is also what keeps a no-op from bumping `entry.row_version` below -
    # an editor who re-submits unchanged values should not invalidate a
    # concurrent editor's own unrelated, still-current row_version.
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

    # Bumps `catalogue_entry.row_version` as part of this write, so a
    # second concurrent `save_property_values`/`save_entry` call against
    # the same entry sees a stale `expected_row_version` rather than
    # silently clobbering this one - see the docstring's note on reusing
    # `row_version`'s existing `version_id_col` machinery as the one lock
    # this entry has.
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


def _value_payload(row: PropertyValue) -> Mapping[str, object]:
    return {"ordinal": row.ordinal, "value": row.value, "justification": row.justification}


def _change_kind(*, existing: bool, inserted: bool) -> ChangeKind:
    if not existing:
        return ChangeKind.CREATED
    if not inserted:
        return ChangeKind.DELETED
    return ChangeKind.UPDATED
