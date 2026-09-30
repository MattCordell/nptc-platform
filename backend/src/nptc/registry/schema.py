"""Per-property JSON Schema derivation, memoisation and value validation (FR-09, FR-10,
ADR-0012).

**Derived, never stored.** The handler derives the schema from the definition row. It is
memoised in-process against `(key, row_version)` and persisted nowhere (ADR-0012).
`row_version`, not `key` alone, makes a narrowing amendment visible without a restart (FR-09):
a `key`-only cache would keep serving the old schema until the process restarted.

**A leaf module** (ADR-0013 SS2): it takes a frozen `PropertyDefinitionSpec` and a
`DatatypeHandler`, never the ORM row. The caller passes `row_version` for the same reason.

**Cardinality is enforced here, not by the schema fragment.** A handler's
`json_schema_fragment` describes one value. The `property_value` primary key cannot enforce
cardinality's upper bound (ADR-0012), so `PropertyDefinitionSpec.cardinality` does, at
validation time. `_cardinality_bounds` is a `match` on the closed
`"0..1" | "1..1" | "0..*" | "1..*"` vocabulary, not a datatype switch, so
`test_datatype_dispatch.py` does not scope it.
"""

from __future__ import annotations

from collections import OrderedDict
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

#: Bounded so that amendments to every property over a long-running process cannot grow the
#: cache without limit. 512 is generous against PRD SS6.5's expected registry size (tens of
#: properties).
_SCHEMA_CACHE_SIZE = 512

#: `(key, row_version) -> derived fragment`, ADR-0012's own cache key. An `OrderedDict`, not
#: `functools.lru_cache`: the fragment derives from `spec` and `handler`, which do not hash
#: usefully together (`handler` is shared and long-lived, `spec` is a frozen dataclass rebuilt
#: per call, so keying on it would never hit). `move_to_end` on a hit makes it LRU, not FIFO,
#: with one structure to keep in sync.
_FRAGMENT_CACHE: OrderedDict[tuple[str, int], Mapping[str, Any]] = OrderedDict()


class MalformedConstraintsError(ValueError):
    """Raised by `validate_constraints` when a `PropertyDefinition.constraints` document does
    not conform to its handler's `constraints_schema()`. Distinct from `ValidationIssue`, which
    reports a bad *value*: this is a defect in the definition itself, caught before it can
    judge a value."""


def _cardinality_bounds(cardinality: str) -> tuple[int, int | None]:
    """Returns `(minimum, maximum)` permitted values, `maximum=None` for unbounded. Closed over
    the four-member vocabulary that `property_definition`'s own `CHECK` enforces."""
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
    """The whole-property JSON Schema for `spec`: the handler's `json_schema_fragment(spec)`,
    memoised against `(spec.key, row_version)` (ADR-0012). Callers validating a set of values
    call this once and reuse the result, as `validate_values` does."""
    cache_key = (spec.key, row_version)
    cached = _FRAGMENT_CACHE.get(cache_key)
    if cached is not None:
        _FRAGMENT_CACHE.move_to_end(cache_key)
        return cached
    fragment = handler.json_schema_fragment(spec)
    _FRAGMENT_CACHE[cache_key] = fragment
    if len(_FRAGMENT_CACHE) > _SCHEMA_CACHE_SIZE:
        _FRAGMENT_CACHE.popitem(last=False)
    return fragment


def reset_schema_cache() -> None:
    """Clears the module-level `(key, row_version)` cache.

    The cache is process-global by design (ADR-0012), with no per-`DatatypeRegistry` instance.
    That breaks a test suite that reuses a property `key` with `row_version=1` across
    unrelated tests and different specs: the second test would receive the first's cached
    fragment. Test modules call this in an autouse fixture. Production code never does,
    because an amendment changes `row_version`, which invalidates the cache."""
    _FRAGMENT_CACHE.clear()


def validate_constraints(spec: PropertyDefinitionSpec, handler: DatatypeHandler) -> None:
    """Validates `spec.constraints`'s interior against `handler.constraints_schema()`. Raises
    `MalformedConstraintsError`, not `ValidationIssue`s: a bad `constraints` document is a
    defect in the definition, not something a caller submitting a value could have avoided."""
    # `dict(...)`: `jsonschema`'s stubs want `dict[Any, Any]`, narrower than the
    # `Mapping[str, Any]` that handlers return.
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
    """Validates a whole set of values for one property against `spec`: JSON Schema shape per
    value, each handler's `validate()` per value (including FR-10's binding check for `code`),
    then the cardinality bounds (ADR-0012). Every value is checked whatever an earlier one's
    outcome, so a caller sees every problem in one round trip.

    `path` on a returned `ValidationIssue` is a decimal string ordinal (`"0"`, `"1"`, ...) for
    a per-value issue, or `None` for a cardinality issue on the property as a whole.
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
        # A value that fails the schema shape check skips the handler's `validate()`:
        # `CodeHandler` would otherwise Verhoeff-check something that is not a coding object.
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
