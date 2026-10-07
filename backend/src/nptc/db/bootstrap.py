"""Idempotent seeding of the four `origin = system` built-in properties (PRD S6.5/S6.6): Discipline,
Subgroup, Specimen and Usage guidance.

**Lives under `nptc.db`, not `nptc.registry`, because of ADR-0013 SS2's leaf rule.** `nptc.registry`
may import `nptc_shared`, SQLAlchemy, `jsonschema` and the stdlib, and nothing else from `nptc`;
`test_datatype_dispatch.py::test_registry_never_imports_a_non_leaf_sibling_package` enforces it.
This module imports the `PropertyDefinition` ORM model to insert rows, which the leaf rule keeps out
of `registry/`. It seeds the built-ins only; admin-defined properties go through
`nptc.db.definitions`.

**Seeds through `PropertyDefinition`'s mapped `INSERT`, never `op.bulk_insert` or hand-written SQL**
(ADR-0012). A data migration bypasses the handler, the schema derivation and the binding validation,
so it could seed a definition the running application would reject. The four built-in fields
therefore use the same storage path as an admin-defined property, with nothing special beyond
`origin = 'system'`.

**Idempotent by key, not by a one-shot marker.** `seed_system_properties` re-checks
`property_definition.key` on every call (FR-09: no migration, restart or deployment gates this), so
a repeat call inserts only what is missing.

**Safe under concurrent callers.** The `SELECT` of existing keys and the `INSERT`s are not atomic,
so two processes starting at once can both see a key missing. Each `INSERT` runs in its own
`SAVEPOINT` (`Session.begin_nested()`). The loser's `IntegrityError` is caught only when its
sqlstate is `23505` (unique violation): the savepoint rolls back and the key counts as seeded. Any
other `IntegrityError` (a binding `CHECK`, the `key` regex) re-raises, because it is a defect in
`_build_system_property_definitions` and swallowing it would report success with the key missing.
The session stays usable for the remaining rows. A plain `INSERT`, not `ON CONFLICT DO NOTHING`,
keeps the same write path for the row that loses the race.

Field values follow PRD SS6.5/6.6:

- **Discipline / Subgroup** (FR-90 to FR-92): coded, `0..*`, bound to a governed RCPA local code
  system (`binding_target = 'local_code_system'`, `local_code_system_key = 'discipline'` or
  `'subgroup'`). Neither has a `value_set_uri`: that column is `value_set`-only (see
  `_VALUE_SET_URI_REQUIRED_CHECK_SQL` on the model).
- **Specimen** (FR-88, FR-89): coded, `0..*`, bound to the SNOMED CT-AU value set `<<123038009`
  |Specimen|, the root and its descendants (ADR-0044). The root is the "any specimen" value and
  stands alone on an entry.
- **Usage guidance** (OI-12): free text, `0..1`, no binding, not filterable. It stays an editorial
  field, never structured.

**`scope`**: Discipline, Subgroup and Specimen are `both`: classification that belongs on the
submission form and stays editable in maintenance (FR-23). Usage guidance is `maintenance`-only:
RCPA-QAP fills it in after submission (OI-12), so it does not belong on the submission form.
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

#: Postgres sqlstate for a unique violation, the only failure the savepoint may swallow; see the
#: module docstring.
_UNIQUE_VIOLATION = "23505"

#: SNOMED CT-AU, `<<123038009` |Specimen (specimen)|, URL-encoded as PRD S6.6's own worked
#: example is. `<<` includes the root, which is the value `Any` takes (ADR-0044).
_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009"


def _build_system_property_definitions() -> tuple[PropertyDefinition, ...]:
    """A fresh set of unattached `PropertyDefinition` instances on every call: a mapped instance
    belongs to one `Session` at a time, and callers may use a different session per call.
    """
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
            local_code_system_key="discipline",
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
            local_code_system_key="subgroup",
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
    """Inserts every built-in definition not already present by `key`, through
    `PropertyDefinition`'s mapped `INSERT`. Each insert runs in its own `SAVEPOINT`; only a
    unique-violation race on that key is skipped, and any other integrity failure propagates (see
    the module docstring). Returns the keys actually inserted: empty on a repeat call, and excluding
    any key a concurrent caller won. Does not commit; the caller owns the transaction boundary.
    """
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
