"""Per-property JSON Schema derivation, memoisation and value validation
(issue #52, FR-09, FR-10, ADR-0012).

**Derived, never stored.** ADR-0012's Decision section is explicit: "JSON
Schema is derived from the definition row by the handler, memoised
in-process against `(key, row_version)`, and persisted nowhere." This
module is that derivation, plus the memoisation cache keyed on exactly that
pair - `row_version`, not `key` alone, is what makes a narrowing amendment
visible without a restart (FR-09): a stale `key`-only cache would keep
serving the old schema until the process restarted, which is precisely the
outcome FR-09 forbids.

**A leaf module** (ADR-0013 SS2): takes a frozen `PropertyDefinitionSpec`
and a `DatatypeHandler`, never the ORM `PropertyDefinition` row. The
`row_version` used as the second half of the cache key is passed in by the
caller for the same reason - this package must not read `nptc.db` to get
it.

**Cardinality is enforced here, not by the schema fragment.** A handler's
`json_schema_fragment` describes one value's shape; the multi-valued
envelope (how many values are permitted) is `PropertyDefinitionSpec.
cardinality`, which ADR-0012 states plainly the `property_value` primary
key cannot close ("it does not enforce cardinality's upper bound... #52
enforces the upper bound at validation time"). `_cardinality_bounds` is a
`match` on the closed `"0..1" | "1..1" | "0..*" | "1..*"` vocabulary, not a
datatype switch, so it is not something `test_datatype_dispatch.py`'s
AST guard is scoped around.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import jsonschema

from nptc.registry.handlers import DatatypeHandler, PropertyDefinitionSpec, ValidationIssue

__all__ = [
    "MalformedConstraintsError",
    "property_schema",
    "reset_schema_cache",
    "validate_constraints",
    "validate_values",
]

#: Bounded, not unbounded - a runaway number of distinct (key, row_version)
#: pairs (every amendment to every property, forever) must not grow this
#: cache without limit. 512 is generous against PRD SS6.5's expected
#: registry size (tens of properties) with headroom for amendment churn
#: across a long-running process.
_SCHEMA_CACHE_SIZE = 512

#: `(key, row_version) -> derived fragment`. A plain dict, not
#: `functools.lru_cache`, because the thing being cached is derived from
#: two arguments (`spec`, `handler`) that are not usefully hashable
#: together - `handler` is a shared, long-lived instance and `spec` is a
#: frozen dataclass rebuilt per call, so keying on it directly would never
#: hit. `key`/`row_version` alone are exactly ADR-0012's own cache key.
_FRAGMENT_CACHE: dict[tuple[str, int], Mapping[str, Any]] = {}
_FRAGMENT_CACHE_ORDER: list[tuple[str, int]] = []


class MalformedConstraintsError(ValueError):
    """Raised by `validate_constraints` when a `PropertyDefinition.
    constraints` document does not conform to its own handler's
    `constraints_schema()`. Distinct from `ValidationIssue` (which reports
    a bad *value*): a malformed `constraints` document is a defect in the
    property definition itself, caught before it can ever be used to judge
    a value."""


def _cardinality_bounds(cardinality: str) -> tuple[int, int | None]:
    """Returns `(minimum, maximum)` values permitted, `maximum=None` for
    unbounded. Closed match over ADR-0012's fixed four-member vocabulary,
    the same one `property_definition`'s own `CHECK` constrains to."""
    match cardinality:
        case "0..1":
            return (0, 1)
        case "1..1":
            return (1, 1)
        case "0..*":
            return (0, None)
        case "1..*":
            return (1, None)
        case _:
            raise ValueError(f"unknown cardinality: {cardinality!r}")


def property_schema(
    spec: PropertyDefinitionSpec, handler: DatatypeHandler, *, row_version: int
) -> Mapping[str, Any]:
    """The whole-property JSON Schema for `spec`: the handler's own
    `json_schema_fragment(spec)`, memoised in-process against
    `(spec.key, row_version)` per ADR-0012 - `row_version`, not `key`
    alone, so a narrowing amendment is picked up without a restart (FR-09).
    Callers validating a *set* of values (the normal multi-valued case)
    call this once and reuse the result - `validate_values` below does
    exactly that."""
    cache_key = (spec.key, row_version)
    cached = _FRAGMENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    fragment = handler.json_schema_fragment(spec)
    _FRAGMENT_CACHE[cache_key] = fragment
    _FRAGMENT_CACHE_ORDER.append(cache_key)
    if len(_FRAGMENT_CACHE_ORDER) > _SCHEMA_CACHE_SIZE:
        oldest = _FRAGMENT_CACHE_ORDER.pop(0)
        _FRAGMENT_CACHE.pop(oldest, None)
    return fragment


def reset_schema_cache() -> None:
    """Clears the module-level `(key, row_version)` cache.

    The cache is process-global by design (ADR-0012: "memoised
    in-process") - there is deliberately no per-`DatatypeRegistry`
    instance, since two registries in the same process are still one
    process. That is exactly wrong for a test suite reusing the same
    property `key` (e.g. `"test_property"`) with `row_version=1` across
    unrelated test functions and different `PropertyDefinitionSpec`
    shapes: without a reset between tests, the second test would silently
    receive the first test's cached fragment. Test modules exercising
    `property_schema`/`validate_values` call this in an autouse fixture;
    production code never calls it - a real amendment changes
    `row_version`, which is what actually invalidates the cache."""
    _FRAGMENT_CACHE.clear()
    _FRAGMENT_CACHE_ORDER.clear()


def validate_constraints(spec: PropertyDefinitionSpec, handler: DatatypeHandler) -> None:
    """Validates `spec.constraints`'s interior against
    `handler.constraints_schema()`. Raises `MalformedConstraintsError`
    rather than returning `ValidationIssue`s - a bad `constraints`
    document is a defect in the *definition*, not something a caller
    submitting a *value* could have avoided."""
    # `dict(...)`: `jsonschema`'s own type stubs want `dict[Any, Any]`,
    # narrower than the `Mapping[str, Any]` every handler's
    # `constraints_schema()` returns.
    constraints_schema = dict(handler.constraints_schema())
    validator_cls = jsonschema.validators.validator_for(constraints_schema)
    validator_cls.check_schema(constraints_schema)
    errors = sorted(
        validator_cls(constraints_schema).iter_errors(spec.constraints),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        joined = "; ".join(e.message for e in errors)
        raise MalformedConstraintsError(
            f"constraints for property {spec.key!r} do not conform to its "
            f"{spec.datatype!r} handler's constraints_schema: {joined}"
        )


def validate_values(
    values: Sequence[Any],
    spec: PropertyDefinitionSpec,
    handler: DatatypeHandler,
    *,
    row_version: int,
) -> Sequence[ValidationIssue]:
    """Validates a whole set of values for one property against `spec`:
    JSON Schema shape (per value), each handler's own local/structural
    `validate()` (per value, including FR-10's binding check for `code`),
    and finally the cardinality bounds ADR-0012 assigns here. Order matters
    only for readability - every value is checked regardless of an earlier
    one's outcome, so a caller sees every problem in one round trip rather
    than one-error-at-a-time.

    `path` on a returned `ValidationIssue` is a decimal string ordinal
    (`"0"`, `"1"`, ...) for a per-value issue, or `None` for a
    cardinality issue that applies to the property as a whole.
    """
    fragment = dict(property_schema(spec, handler, row_version=row_version))
    validator_cls = jsonschema.validators.validator_for(fragment)
    validator_cls.check_schema(fragment)
    validator = validator_cls(fragment)

    issues: list[ValidationIssue] = []
    for ordinal, value in enumerate(values):
        schema_errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        for error in schema_errors:
            issues.append(
                ValidationIssue(code="schema-violation", message=error.message, path=str(ordinal))
            )
        # A value that fails the JSON Schema shape check is not passed to
        # the handler's own validate() - e.g. CodeHandler.validate() would
        # otherwise be asked to Verhoeff-check a value that is not even a
        # coding object.
        if schema_errors:
            continue
        for issue in handler.validate(value, spec):
            issues.append(
                ValidationIssue(code=issue.code, message=issue.message, path=str(ordinal))
            )

    minimum, maximum = _cardinality_bounds(spec.cardinality)
    count = len(values)
    if count < minimum:
        issues.append(
            ValidationIssue(
                code="cardinality-below-minimum",
                message=(f"{spec.label} requires at least {minimum} value(s); {count} supplied"),
            )
        )
    if maximum is not None and count > maximum:
        issues.append(
            ValidationIssue(
                code="cardinality-above-maximum",
                message=(f"{spec.label} accepts at most {maximum} value(s); {count} supplied"),
            )
        )
    return issues
