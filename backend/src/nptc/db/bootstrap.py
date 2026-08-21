"""Idempotent seeding of the four `origin = system` built-in properties
(issue #51, PRD S6.5/S6.6): Discipline, Subgroup, Specimen and Usage
guidance.

**Lives under `nptc.db`, not `nptc.registry`, because of ADR-0013 SS2's
leaf rule** - `nptc.registry` may import `nptc_shared`, SQLAlchemy,
`jsonschema` and the stdlib, and nothing else from `nptc`, specifically so
`registry/**` never imports `nptc.db`
(`test_datatype_dispatch.py::test_registry_never_imports_a_non_leaf_sibling_package`
enforces this mechanically). This module imports the `PropertyDefinition`
ORM model directly to insert rows, which is exactly what the leaf rule
exists to keep out of `registry/`; ADR-0013 itself names
`registry/definitions.py` as the not-yet-built PropertyDefinition service
(#51/#55) that will eventually own this concern from the registry side,
constructing rows from a datatype-agnostic spec rather than importing the
ORM model by name - this module is the pre-#52/#137 bootstrap seeding
alone, not that service.

**Seeds through `PropertyDefinition`'s own mapped `INSERT`, never
`op.bulk_insert` or hand-written SQL** (ADR-0012) - a data migration
bypasses the handler, the schema derivation and the binding validation,
so it could seed a definition the running application would itself
reject. This is also what makes this issue's own acceptance criterion
true: the four built-in fields travel the same storage code path as an
admin-defined property, with no special-casing beyond `origin = 'system'`
on the row itself.

**Idempotent by key, not by a one-shot marker** - `seed_system_properties`
re-checks `property_definition.key` against the database on every call
(FR-09: no migration, no restart, no deployment gates this), so calling
it again after a partial or repeat run only inserts whatever is still
missing.

**Safe under concurrent callers, not just serial repeat calls.** The
`SELECT` of existing keys and the following `INSERT`s are not atomic, so
two processes starting at once (the realistic shape of this seeding
running from application startup) can both see a key missing and race to
insert it. Each row's `INSERT` therefore runs inside its own `SAVEPOINT`
(`Session.begin_nested()`); the loser's `IntegrityError` is caught **only
when its sqlstate is `23505` (unique violation)** - narrowly matching the
`uq_property_definition_key` race this exists to handle - the savepoint is
rolled back, and that key is treated as already seeded. Any other
`IntegrityError` (a binding `CHECK`, the `key` regex, ...) re-raises: it is
a genuine bug in `_build_system_property_definitions`, exactly the class
of error the "seed through the real write path, not `op.bulk_insert`"
argument exists to catch, and swallowing it would report success with
that key silently missing. The session itself stays usable for the
remaining rows either way. This is still `PropertyDefinition`'s own mapped
`INSERT`, not `ON CONFLICT DO NOTHING`, so the "same write path as an
admin-defined property" claim below holds for every row, including the
one that loses the race.

Field values below are fixed against PRD SS6.5/6.6, not invented here:

- **Discipline / Subgroup** (FR-90/FR-91-92): coded, `0..*`, bound to a
  governed RCPA local code system (`binding_target = 'local_code_system'`)
  - that `LocalCodeSystem` table is itself still a stub (P1-6's own
  scaffolding note), so no `value_set_uri` is set for either; the FK-less
  `local_code_system` binding target exists precisely so this is
  representable before that table lands.
- **Specimen** (FR-88/FR-89): coded, `0..*`, bound to the SNOMED CT-AU
  value set rooted at `123038009` |Specimen|, exactly as PRD S6.6 verifies
  it (`<123038009` resolves every sampled specimen). `'Any'` is
  deliberately never a specimen value - see `catalogue_entry.
  specimen_unconstrained` (issue #46) for where that flag actually lives.
- **Usage guidance** (OI-12): free text, `0..1`, no binding, not
  filterable - retained as an editorial field, never structured.

**`scope`**: `Discipline`/`Subgroup`/`Specimen` are `both` - classification
that belongs on the submission form and stays editable during maintenance
(FR-23). `Usage guidance` is `maintenance`-only - an editorial field
RCPA-QAP fills in after submission, per OI-12's "empty throughout the
sample but intended" note; it does not belong on the submission form
itself.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)

#: Postgres sqlstate for a unique-violation - the only failure this
#: module's savepoint is entitled to swallow. Any other IntegrityError
#: (a binding CHECK, the `key` regex, ...) is a genuine bug in
#: `_build_system_property_definitions` and must propagate, not be
#: silently reported as "already seeded" with the key actually missing.
_UNIQUE_VIOLATION = "23505"

#: PRD S6.6: SNOMED CT-AU, `<123038009` |Specimen (specimen)|, URL-encoded
#: per the PRD's own worked example.
_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"


def _build_system_property_definitions() -> tuple[PropertyDefinition, ...]:
    """A fresh set of unattached `PropertyDefinition` instances every call
    - never a module-level constant, since a mapped instance can only ever
    belong to one `Session` at a time, and this factory may be called
    against a different session on every invocation (e.g. once per test)."""
    return (
        PropertyDefinition(
            key="discipline",
            label="Discipline",
            datatype="code",
            cardinality=PropertyCardinality.ZERO_OR_MANY,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=True,
            binding_target=BindingTarget.LOCAL_CODE_SYSTEM,
            strength=BindingStrength.REQUIRED,
            filterable=True,
            origin=PropertyOrigin.SYSTEM,
            display_order=10,
        ),
        PropertyDefinition(
            key="subgroup",
            label="Subgroup",
            datatype="code",
            cardinality=PropertyCardinality.ZERO_OR_MANY,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            binding_target=BindingTarget.LOCAL_CODE_SYSTEM,
            strength=BindingStrength.REQUIRED,
            filterable=True,
            origin=PropertyOrigin.SYSTEM,
            display_order=20,
        ),
        PropertyDefinition(
            key="specimen",
            label="Specimen",
            datatype="code",
            cardinality=PropertyCardinality.ZERO_OR_MANY,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            binding_target=BindingTarget.VALUE_SET,
            value_set_uri=_SPECIMEN_VALUE_SET_URI,
            strength=BindingStrength.REQUIRED,
            edition="au",
            filterable=True,
            origin=PropertyOrigin.SYSTEM,
            display_order=30,
        ),
        PropertyDefinition(
            key="usage_guidance",
            label="Usage guidance",
            datatype="string",
            cardinality=PropertyCardinality.ZERO_OR_ONE,
            scope=PropertyScope.MAINTENANCE,
            required_for_submission=False,
            required_for_publication=False,
            filterable=False,
            origin=PropertyOrigin.SYSTEM,
            display_order=40,
        ),
    )


def seed_system_properties(session: Session) -> list[str]:
    """Inserts every built-in property definition not already present
    by `key`, via `PropertyDefinition`'s own mapped `INSERT` - the same
    write path an admin-defined property uses, per this module's own
    docstring. Each row's insert runs in its own `SAVEPOINT`; only a
    genuine unique-violation race on that row's key is caught and
    skipped (see the module docstring's "safe under concurrent callers"
    note) - any other integrity failure propagates, since it is a real
    defect in the seed data, not a race. Returns the keys actually
    inserted (empty on a repeat call once every row exists, and excludes
    any key a concurrent caller won the race on); does not commit - the
    caller controls the outer transaction boundary, matching every other
    write path in this codebase."""
    definitions = _build_system_property_definitions()
    wanted_keys = [definition.key for definition in definitions]
    existing_keys = frozenset(
        session.scalars(
            select(PropertyDefinition.key).where(PropertyDefinition.key.in_(wanted_keys))
        )
    )

    inserted: list[str] = []
    for definition in definitions:
        if definition.key in existing_keys:
            continue
        try:
            with session.begin_nested():
                session.add(definition)
                session.flush()
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
                raise
            continue
        inserted.append(definition.key)
    return inserted
