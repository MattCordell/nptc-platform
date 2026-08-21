# ADR-0013: Datatype handler registry design

**Status:** Accepted
**Date:** 2026-08-16

## Context

FR-77 is a constraint on where code may live, not a feature: "datatype handling MUST be
implemented as a registry of handlers, each supplying a JSON Schema fragment, a validation
routine, a form control, and a serialiser for each export format" (PRD SS6.5). Its failure
condition is stated as precisely: "if adding a datatype requires edits in more than the
handler module and its tests, the requirement has not been met." The check named alongside
it is "a single test that registers a synthetic datatype and exercises it end to end through
save, validate, filter and export" - [#53](https://github.com/MattCordell/nptc-platform/issues/53)'s
synthetic-datatype test.

[ADR-0012](0012-property-registry-storage-and-validation.md) explicitly fenced this off ("the
datatype **handler contract** (FR-77) is issue #137's ADR") and handed this ADR three seams:
the `datatype` TEXT column with no CHECK and no ENUM, the handler-owned `constraints` JSONB
column, and the JSON Schema derivation memoised on `(key, row_version)`. ADR-0012's
no-CHECK/no-ENUM decision has an unnamed consequence this ADR must close: **the registry is
the only place the valid datatype set is known**, so it is also the only thing that can
reject an unknown datatype at write time - a rejection ADR-0012 assumed but did not itself
specify a mechanism for.

This ADR cannot wait. Storage ([#51](https://github.com/MattCordell/nptc-platform/issues/51)),
export (the P4 renderers) and search
([#54](https://github.com/MattCordell/nptc-platform/issues/54)) are all about to be written,
and each is a place a `switch` on datatype naturally accumulates the moment its author reaches
for the obvious thing. [#53](https://github.com/MattCordell/nptc-platform/issues/53) is
blocked on this ADR and must not open a PR until it merges.

FR-77 is a SHOULD, but the constraint it places on four MUSTs is what earns an ADR of this
length: FR-13 (automatic index generation), FR-16 (faceted filtering), FR-83 (semantic tag
removal confined to the export renderer) and FR-09 (no migration/restart/deployment to add a
property). Each of those MUSTs has a specific way a datatype `switch` breaks it, argued in its
own numbered decision below.

**This is a documentation-only change.** No code is written; `backend/src/nptc/registry/`
stays a one-line docstring stub. FR-77 stays `status: planned` - an ADR is a design record,
not evidence of implementation (ADR-0002's distinction, as ADR-0012 restated it).

## Decision

**1. The contract is a `typing.Protocol` with eleven members.** Four are FR-77's own sentence
(`json_schema_fragment`, `validate`, `form_control`, `serialise`); seven are forced by the
seams ADR-0012 left open - the `datatype` identity property, `constraints_schema()` for the
`constraints`-interior schema, `index_shape()`, `supported_filter_ops()` and `filter_clause()`
(the two members FR-16's filtering needs), `facet_expression()`, and `sort_key()`. A Protocol
has no constructor member of its own; a handler's own `__init__` (the `code` handler's
`TerminologyClient` argument below) is part of the concrete class, not the eleven-member
contract it satisfies. Handlers see a frozen `PropertyDefinitionSpec` view, never the ORM
model - this
is what keeps `nptc.registry` from importing `nptc.db`, and it is what lets
[#53](https://github.com/MattCordell/nptc-platform/issues/53)'s test build every input by hand
with no database. `validate()` is local and structural; FR-10's binding check reaches the
terminology server through a `TerminologyClient` injected into the `code` handler's
constructor (ADR-0003's synchronous Protocol) - so handlers are **constructed**, not imported
as module-level singletons, which is what keeps NFR-37 satisfiable via
`StubTerminologyClient` with no second injection mechanism competing with the first.

**2. The module boundary.** `nptc.registry` is a leaf: it may import `nptc_shared`,
SQLAlchemy, `jsonschema` and the stdlib, and nothing else from `nptc`. Everyone else imports
it. The rule is **"no datatype switch outside `backend/src/nptc/registry/datatypes/`"** -
strictly narrower than CONTRIBUTING.md's current wording, deliberately: the registry's own
definition CRUD ([#51](https://github.com/MattCordell/nptc-platform/issues/51)/[#55](https://github.com/MattCordell/nptc-platform/issues/55))
and schema derivation ([#52](https://github.com/MattCordell/nptc-platform/issues/52)) have no
business branching on datatype either. Layout:

```text
backend/src/nptc/registry/
  __init__.py      public surface
  handlers.py      DatatypeHandler Protocol + value types + DatatypeRegistry + errors
  datatypes/
    __init__.py    build_builtin_handlers(deps) <- THE one-line edit point
    code.py  string.py  decimal.py  positive_int.py  url.py
  definitions.py   PropertyDefinition service (#51/#55)
  schema.py        JSON Schema derivation + (key, row_version) memoisation (#52)
```

**3. The form control crosses a language boundary, and the ADR says so.** The handler returns
a `FormControlDescriptor`: a `ControlKind` from a closed enum plus JSON-serialisable params,
surfaced on the property-definition API response and therefore in the OpenAPI doc and the
generated client ([#143](https://github.com/MattCordell/nptc-platform/issues/143)/[#147](https://github.com/MattCordell/nptc-platform/issues/147)).
**`ControlKind` members are named after the interaction, never after a datatype** (`text`,
`textarea`, `number`, `uri`, `concept_picker`) - that is the whole resolution: the frontend
receives `control` and `params` and never learns a datatype name, so a new datatype selects an
existing kind and touches no frontend code. FR-77's "one handler module" diff test is scoped
to the Python backend, and that scoping is sound *because the frontend contains no datatype
dispatch to scope*. Two packages are touched only when the *interaction* vocabulary grows -
disclosed, not denied. The frontend's no-switch rule is enforced by the type system
(`Record<ControlKind, ComponentType<ControlProps>>`, no default branch -> a missing kind is a
`tsc -b` error), complemented by one vitest guard asserting no datatype literal appears in
`frontend/src` outside the generated client types.

Mapping table:

| Datatype | Control | Params |
|---|---|---|
| `string` | `text` / `textarea` | (length hint from `constraints`) |
| `decimal` | `number` | `{step: "any"}` |
| `positiveInt` | `number` | `{step: 1, minimum: 1}` |
| `url` | `uri` | `{schemes}` |
| `code` | `concept_picker` | `{valueSetUri, strength, edition, allowJustification}` |

`allowJustification` is computed from `strength == "extensible"` inside the handler, so the
frontend does not branch on `strength` either.

**4. Registration is explicit construction in one function** -
`nptc.registry.datatypes.build_builtin_handlers(deps)` returning a tuple. No decorator, no
`pkgutil` scan, no entry points. `DatatypeRegistry` is an *instance*, not module globals, so
[#53](https://github.com/MattCordell/nptc-platform/issues/53)'s test builds
builtins-plus-synthetic without mutating shared state. **`get()` has no default and no
fallback and its return type is not `Optional`** - #53's "clear no-handler-registered error,
not a silent fallthrough" AC expressed as a signature.

Why a one-line addition to `build_builtin_handlers` is *not* "an edit outside the handler
module": FR-77's failure condition is edits in other *layers*, and `datatypes/__init__.py` is
the handler package's own manifest of its own contents. Contrast the two things ADR-0012
rejected on these grounds - a `CHECK (datatype IN ...)` and a Postgres ENUM - both of which are
edits to *storage*, a different layer entirely.

Two obligations follow and must be stated as [#51](https://github.com/MattCordell/nptc-platform/issues/51)'s,
since they are ADR-0012's unnamed compensating control: the write path resolves
`registry.get(datatype)` before insert/update (unknown -> 422 naming the registered set, never
a stored row); and a startup reconciliation
(`SELECT DISTINCT datatype FROM property_definition` subset-of registered set) exposed as a
CLI subcommand, so removing a handler from a deployment holding live values fails at boot with
the list rather than at the first read of an affected entry.

**5. Enforcement: `backend/tests/test_datatype_dispatch.py`**, a pure-`ast` pytest guard
modelled directly on
[test_sql_parameterisation.py](../../backend/tests/test_sql_parameterisation.py) - same
`SCAN_DIRS` constant, same frozen `Violation` with `file:line: [rule] detail`, same
print-the-whole-list convention, same inline-source positive control asserting an exact
per-rule `Counter`. Marked `@pytest.mark.req("FR-77")`. Runs in CI already (the `python` job's
`uv run pytest`), no new tooling.

- **`SCAN_DIRS` is the exact same two paths as `test_sql_parameterisation.py`'s** -
  `backend/src` and `backend/migrations`, not a narrower `backend/src/nptc` - so the two
  guards genuinely share one constant rather than two constants that happen to look alike.
  Scoping to `registry/datatypes/` is a separate, additional exclusion:
  `backend/src/nptc/registry/datatypes/` is excluded from this guard only, not from
  `test_sql_parameterisation.py`. (`backend/tests` is outside `SCAN_DIRS` either way, so
  [#53](https://github.com/MattCordell/nptc-platform/issues/53)'s synthetic test may use
  literals freely.)
- **The known-datatype literal set is imported from `BUILTIN_DATATYPES`, not hardcoded** - the
  module-level tuple in `datatypes/__init__.py` (SS10's Protocol block), not
  `build_builtin_handlers` itself, which the guard cannot call without constructing a
  `HandlerDeps`/`TerminologyClient` it has no business needing. A hardcoded list inside the
  guard would make the guard itself a second enumeration of the valid set, i.e. the guard
  would itself violate FR-77; `BUILTIN_DATATYPES` avoids that because it still lives inside
  the handler package the guard is scoped around.
- A "datatype-bearing expression" is recognised **by name, not by type** (the guard is
  syntactic and says so): `Attribute.attr == "datatype"`, `Name.id == "datatype"` or
  `*_datatype`, or a `Subscript` with a constant `"datatype"` slice.
- Four rules:
  - `datatype-match` - an `ast.Match` whose subject is a datatype-bearing expression.
  - `datatype-compare` - `Eq`/`NotEq`/`In`/`NotIn` against a string constant, or a
    tuple/list/set of them, on one side of the comparison.
  - `datatype-dispatch-table` - an `ast.Dict` with two or more keys, all string constants,
    whose key set is either a **subset** of the known datatypes, or a **superset** of them
    (catches the "all five plus a `\"default\"`/`\"fallback\"` key" shape, which a subset test
    alone would miss precisely because it is a superset).
  - `registry-imports-sibling` - an import inside `registry/**` naming `nptc.db`,
    `nptc.catalogue`, `nptc.exports`, `nptc.api`, `nptc.validation`, `nptc.submissions`,
    `nptc.releases`, or `nptc.jobs` (SS2's leaf rule made mechanical).
- **Worked false-positive analysis** (this is what makes the design reviewable):
  `{"code": "12345", "system": "http://snomed.info/sct"}` is not flagged, because `"system"`
  is not a datatype literal and neither the subset nor the superset test matches a key set
  that shares no elements with the known datatypes at all. `if binding.code == "code":` is not
  flagged, because `.code` is not `.datatype`. A one-key dispatch dict is an accepted gap.
- **A fifth rule is explicitly rejected**: "any bare string literal equal to a known
  datatype". `code`, `string` and `url` are ordinary English words and ordinary JSON keys
  elsewhere in this codebase; such a rule fires dozens of times on day one and gets suppressed
  within a week, which is worse than not having it.
- **mypy's complementary half**: `DatatypeHandler` is a `Protocol`, `build_builtin_handlers`
  is annotated `-> tuple[DatatypeHandler, ...]`, so a missing member or a drifted signature is
  a `mypy --strict` error at the registration site. Two mechanisms because they prove opposite
  things: mypy proves a handler is *complete*; the AST guard proves dispatch exists
  *nowhere else*.
- **`typing.Literal` datatype unions and `assert_never` are rejected, not merely unused** - a
  closed `Literal` is a second enumeration of the valid set, and every `assert_never` site
  becomes a required edit when a datatype is added: FR-77's failure condition wearing a type
  checker's clothes, the same objection ADR-0012 raised against a CHECK one layer down.
  `datatype` is `str` in Python types throughout; no `enum.Enum` for it either. The asymmetry
  that keeps this consistent: `ControlKind`, `SerialisationTarget`, `FilterOp`, `IndexKind`
  and `ValueExpression` *are* closed enums, because none of them grows when a datatype is
  added - that is the test for a legitimate closed type here.
- **Named limits, verbatim, so review knows its job**:
  1. Proxy switches - `if definition.binding_target is not None:` is `datatype == "code"` in
     disguise, as is `if "minimum" in definition.constraints:` - the most likely real
     violation and the one hardest for a syntactic guard to catch.
  2. Reflective dispatch (`getattr(self, f"handle_{datatype}")`).
  3. Dispatch expressed in SQL (a static literal `CASE WHEN pd.datatype = 'decimal'` -
     NFR-22's guard bans dynamic SQL *text*, not this).
  4. The frontend and generated client.
  5. One handler branching on another datatype's name inside `registry/datatypes/`, excluded
     by construction.
  6. A dispatch dict keyed on the known datatypes plus one extra `"default"`/`"fallback"` key -
     the superset case the `datatype-dispatch-table` rule now also catches (added above after
     review), named here anyway since it is the shape most likely to be written unthinkingly.

**6. The contract lives in `backend/src/nptc/registry/`, not `shared/`.** ADR-0003's criterion
is that *both* backend and transform need it (FR-74); not met here. The transform has no
notion of a property definition, let alone a datatype - `transform/src/nptc_transform/dataset.py`
emits four fixed properties whose values are `(value: str, code: str | None)`, and
ADR-0010's `import-dataset.json` carries no `datatype` field. `shared/` would also acquire
`jsonschema` and SQLAlchemy expression types, in a package ADR-0003 fought to hold at one
runtime dependency (`httpx`). **Name the revisit trigger**: if a future issue makes the
transform validate seeded values against the handlers, that is a superseding ADR moving
`handlers.py` to `shared/` and leaving the SQLAlchemy-dependent members (`filter_clause`,
`facet_expression`) in `backend/` - recording the split point now keeps that from being a
rewrite.

**7. The export seam: `SerialisationTarget` enumerates *representations*, not export
formats.** Three members - `PLAIN_TEXT` (CSV cell, SPIA xlsx cell, any future flat artefact),
`JSON` (FR-20 read API), `FHIR_VALUE` (FR-64 supplement property value). Adding a fourth
export *format* selects an existing representation and touches **zero** handlers; adding a
fourth *representation* is a multi-handler edit, disclosed here rather than discovered later.
The dependency does not invert: `SerialisationTarget` lives in `registry/handlers.py`, each
renderer imports the registry, resolves by `datatype`, and calls `serialise(value, target)`.
The edge is `exports -> registry`, one way, kept by guard rule 4. One method, not one per
format, so a renderer never enumerates handlers and a handler never enumerates renderers.

**No handler may strip a semantic tag, and this is a rule, not an omission (FR-83).** FR-83's
guarantee is a claim about the number of call sites: exactly one, in the export renderer, over
a value read from `code_binding.fsn`. There is no conflict - an FSN is a `CodeBinding` core
column, never a registry property value - but the `code` handler is exactly where a
well-meaning author would add a second strip. **This is also the ADR's concrete statement of
the cost of violating the boundary**: a per-datatype branch inside the export renderer is
precisely the shape in which a second tag-strip call site appears, and FR-83's guarantee is
then false with no test failing.

**8. The search seam (FR-13/FR-16): ADR-0012's index mapping is confirmed and relocated.**
`code` -> GIN `jsonb_path_ops`, `string`/`url` -> text expression index,
`decimal`/`positiveInt` -> numeric expression index is a switch on datatype, so per FR-77 it
belongs in the handler; **[#54](https://github.com/MattCordell/nptc-platform/issues/54)
consumes an `IndexShape` it is handed, it does not compute one.** The handler declares the
shape as a *value*, never as SQL text: `IndexShape(expression, requires_conformance_sweep)`,
where `expression: ValueExpression` is a closed three-member enum (`RAW_JSONB`, `TEXT_SCALAR`,
`NUMERIC_SCALAR`) that #54 maps to fixed SQL fragments under `psycopg.sql.SQL`. `IndexShape`
carries no `kind: IndexKind` field of its own - `IndexKind` (`GIN` or `EXPRESSION_BTREE`,
naming the physical index type) is derived from `expression` via a fixed two-entry mapping
(`INDEX_KIND_BY_EXPRESSION` in SS10's Protocol block), so a handler cannot return one of the
three meaningless `IndexKind`/`ValueExpression` pairings (e.g. `GIN` with a numeric scalar) -
the same "unrepresentable rather than merely refused" principle SS9 applies to `positiveInt`.
Two reasons the enum-plus-mapping beats a handler-supplied SQL string: handlers stay free of
SQL text entirely (a better NFR-22 posture - the guard cannot prove a handler-supplied string
is a literal), and #54's `match` is over a set that does *not* grow when a datatype is added.
**ADR-0012's cast-safety precondition becomes the machine-readable `requires_conformance_sweep`
flag** rather than a
paragraph #54 must remember.

FR-16 is the second half and the handler owns both: `supported_filter_ops()` tells
[#139](https://github.com/MattCordell/nptc-platform/issues/139) which operators to offer,
`filter_clause()` builds the predicate as a SQLAlchemy `ColumnElement[bool]` (never a string -
NFR-22 then holds by construction), `facet_expression()` returns the `GROUP BY` expression or
`None` where faceting is meaningless (`decimal`). **FR-16's stated cost**: a datatype switch
in the search layer with a silent `else` makes a property *silently* unfilterable - the facet
vanishes from the UI with nothing failing anywhere. That is worse than an exception, and is
why `get()` raises.

**9. The initial set is exactly PRD SS6.5's five - `code`, `string`, `decimal`, `positiveInt`,
`url` - and no sixth.** Extensibility is proven by
[#53](https://github.com/MattCordell/nptc-platform/issues/53)'s synthetic datatype, not by
pre-registering speculative ones. `positiveInt` is a separate handler, not `decimal` with a
`minimum`: its fragment `{"type": "integer", "minimum": 1}` makes `1.5` *unrepresentable*
rather than merely refused (ADR-0012's principle, applied here to a schema fragment instead of
a column CHECK).

**`boolean` is excluded on a semantic ground, and the exclusion is load-bearing for FR-89**:
PRD SS6.2/SS6.6 makes `specimen_unconstrained` a core column *because* `boolean` is not an
available datatype, and a `0..1` boolean has three states whose absent state is exactly the
ambiguity FR-89 exists to destroy ("accepts any specimen" vs "nobody has filled this in yet").
**Resolve the apparent contradiction with FR-77's own text**, which names `boolean` as an
example of a datatype the mechanism must be able to accommodate: both hold - the registry
*can* accept a boolean handler, none is registered, and `specimen_unconstrained` stays a core
column regardless of whether one ever is. FR-77 constrains the mechanism; FR-89 constrains
this one flag. Registering a `boolean` handler later needs its own decision about the
tri-state problem, named here so it cannot land as a drop-in that quietly reopens FR-89's
ambiguity somewhere else.

**10. The Protocol, verbatim.**

```python
"""backend/src/nptc/registry/handlers.py - design record, not yet implemented.

Reproduced verbatim from ADR-0013 so #53's synthetic-datatype test is
writable from this ADR alone.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import ColumnElement

from nptc_shared.terminology import TerminologyClient


# --- value types the registry passes to and receives from a handler -----


@dataclass(frozen=True, slots=True)
class BindingSpec:
    """Populated only when datatype == "code" (FR-10)."""

    binding_target: str  # "value_set" | "local_code_system"
    value_set_uri: str | None
    strength: str  # "required" | "extensible" | "example"
    edition: str


@dataclass(frozen=True, slots=True)
class PropertyDefinitionSpec:
    """The frozen view a handler is given - never the ORM model, so
    registry/ never imports db/ (SS2) and #53 can build one by hand with
    no database."""

    key: str
    label: str
    datatype: str
    cardinality: str  # "0..1" | "1..1" | "0..*" | "1..*"
    scope: frozenset[str]  # subset of {"submission", "maintenance"}
    required_for_submission: bool
    required_for_publication: bool
    binding: BindingSpec | None
    filterable: bool
    constraints: Mapping[str, Any]


class ControlKind(enum.Enum):
    """Named after the interaction, never after a datatype (SS3)."""

    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    URI = "uri"
    CONCEPT_PICKER = "concept_picker"


@dataclass(frozen=True, slots=True)
class FormControlDescriptor:
    control: ControlKind
    params: Mapping[str, Any]  # JSON-serialisable only


class SerialisationTarget(enum.Enum):
    """Representations, not export formats (SS7)."""

    PLAIN_TEXT = "plain_text"
    JSON = "json"
    FHIR_VALUE = "fhir_value"


class IndexKind(enum.Enum):
    """Not a handler-supplied field (see IndexShape below) - #54 derives it
    from ValueExpression via INDEX_KIND_BY_EXPRESSION, a fixed two-entry
    mapping, so a handler cannot return the six combinations of IndexKind x
    ValueExpression when only three are meaningful."""

    GIN = "gin"
    EXPRESSION_BTREE = "expression_btree"


class ValueExpression(enum.Enum):
    """Closed set #54's `match` switches over - does not grow when a
    datatype is added (SS8)."""

    RAW_JSONB = "raw_jsonb"
    TEXT_SCALAR = "text_scalar"
    NUMERIC_SCALAR = "numeric_scalar"


INDEX_KIND_BY_EXPRESSION: Mapping[ValueExpression, IndexKind] = {
    ValueExpression.RAW_JSONB: IndexKind.GIN,
    ValueExpression.TEXT_SCALAR: IndexKind.EXPRESSION_BTREE,
    ValueExpression.NUMERIC_SCALAR: IndexKind.EXPRESSION_BTREE,
}


@dataclass(frozen=True, slots=True)
class IndexShape:
    """No `kind` field - it is unrepresentable-by-construction that a
    handler pairs GIN with a numeric scalar. #54 looks up IndexKind from
    `expression` via INDEX_KIND_BY_EXPRESSION."""

    expression: ValueExpression
    requires_conformance_sweep: bool


class FilterOp(enum.Enum):
    EQUALS = "equals"
    IN = "in"
    PREFIX = "prefix"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str | None = None  # ordinal / sub-field, for multi-valued properties


# --- the contract itself -------------------------------------------------


class DatatypeHandler(Protocol):
    """Eleven members. Four are FR-77's own sentence (json_schema_fragment,
    validate, form_control, serialise); seven are forced by the seams
    ADR-0012 left open."""

    @property
    def datatype(self) -> str: ...

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]: ...

    def constraints_schema(self) -> Mapping[str, Any]:
        """Validates the *interior* of the constraints JSONB column
        ADR-0012 reserved but did not define (#52)."""
        ...

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        """Local and structural. FR-10's binding check is a live terminology
        call and reaches the server through self, not this method - the
        code handler is constructed with a TerminologyClient (below)."""
        ...

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor: ...

    def serialise(self, value: Any, target: SerialisationTarget) -> Any: ...

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        """None where indexing is meaningless for this datatype."""
        ...

    def supported_filter_ops(self) -> frozenset[FilterOp]: ...

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        """A SQLAlchemy expression, never a string - NFR-22 holds by
        construction, not by review."""
        ...

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        """None where faceting is meaningless (e.g. decimal)."""
        ...

    def sort_key(self, value: Any) -> Any:
        """The one speculative member of the eleven - #53 may drop it
        (open question 5)."""
        ...


# --- errors ---------------------------------------------------------------


class UnknownDatatypeError(LookupError):
    """Raised by DatatypeRegistry.get() for an unregistered datatype -
    never a default, never a silent fallthrough (FR-16's stated cost)."""


class DuplicateDatatypeError(ValueError):
    """Raised by DatatypeRegistry.__init__() if the handler sequence
    contains two handlers with the same `datatype` - construction-time,
    not a runtime surprise. There is no register() method (SS4: handlers
    are supplied to the constructor as a tuple, never added one at a
    time)."""


class UnsupportedFilterOpError(ValueError):
    """Raised by filter_clause() for an op absent from
    supported_filter_ops()."""


class UnsupportedBindingError(ValueError):
    """Raised by CodeHandler.validate() when binding_target =
    'local_code_system' and the handler was constructed with
    local_code_lookup=None - a loud refusal, never a silent pass, until
    #56 supplies a real LocalCodeLookup (open question 1)."""


# --- the registry and its construction ------------------------------------


class DatatypeRegistry:
    """An instance, not module globals - #53 builds builtins-plus-synthetic
    without mutating shared state."""

    def __init__(self, handlers: Sequence[DatatypeHandler]) -> None: ...

    def get(self, datatype: str) -> DatatypeHandler:
        """No default, no fallback. Return type is not Optional."""
        ...

    def known_datatypes(self) -> frozenset[str]:
        """The runtime valid-datatype set, used by #51's write-time
        registry.get() resolution and startup reconciliation. The AST guard
        (SS5) cannot call this - it has no registry instance to call it on -
        so it imports the static BUILTIN_DATATYPES tuple below instead;
        the two are required to agree, and #53 registering a handler not
        in BUILTIN_DATATYPES is the one legitimate reason the two sets
        could diverge, which is exactly why BUILTIN_DATATYPES, not this
        method, is what the guard reads."""
        ...


class LocalCodeLookup(Protocol):
    """#56 (FR-90) landed this shape directly in `nptc.registry.handlers`
    (this module), replacing the placeholder ADR-0013 first sketched:

    ```python
    @dataclass(frozen=True, slots=True)
    class ResolvedLocalCode:
        code: str
        display: str
        status: str
        system_status: str
        provisional: bool

    class LocalCodeLookup(Protocol):
        def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None: ...
    ```

    Both stay leaf-safe (stdlib-only), unlike `nptc.catalogue.
    local_codes` (below), which #56 could not land inside `nptc.registry`
    itself - #197/#53's own leaf-package rule (SS2) forbids it importing
    `nptc.audit`/`nptc.auth`/`nptc.db.models`, which the write path and
    the database-backed lookup both need.

    One read method, deliberately - management goes through
    `nptc.catalogue.local_codes`, gated on `Permission.REGISTRY_MANAGE`
    (FR-90's "administrator-only management"), not through this Protocol.
    `resolve` returning `None` is "does not exist in `system_key` at all";
    `ResolvedLocalCode.status == 'deprecated'` is "exists, but retired",
    and `system_status` carries the same fact for the owning system
    independently (deprecating a system does not cascade to its member
    codes) - the same distinction `code_binding.status` supports for
    SNOMED bindings, and what the FR-45 sweep's `local_code_retired`
    warning reads. `nptc.catalogue.local_codes.DatabaseLocalCodeLookup`
    is the database-backed implementation #53 constructs `HandlerDeps`
    with; #53 still owns lifting `UnsupportedBindingError` for
    `binding_target = 'local_code_system'` and wiring `CodeHandler.
    validate()`'s `local_code_system` branch to actually call `resolve()`
    (open question 1 below, and `CodeHandler._validate_binding`'s own
    comment) - #56 supplies the shape and the implementation, not the
    handler wiring."""


@dataclass(frozen=True, slots=True)
class HandlerDeps:
    """Constructor dependencies for builtin handlers - handlers are
    constructed, not imported as singletons, so StubTerminologyClient
    needs no second injection mechanism (NFR-37)."""

    terminology_client: TerminologyClient
    local_code_lookup: LocalCodeLookup | None = None  # #56, FR-90


BUILTIN_DATATYPES: tuple[str, ...] = ("code", "string", "decimal", "positiveInt", "url")
"""A deps-free enumeration of the built-in set, in `datatypes/__init__.py`
alongside `build_builtin_handlers` - the AST guard (SS5) imports this, not
`build_builtin_handlers` itself, so it needs no TerminologyClient/HandlerDeps
to learn the known-datatype set. Still one enumeration, inside the handler
package, not a second one the guard maintains itself."""


def build_builtin_handlers(deps: HandlerDeps) -> tuple[DatatypeHandler, ...]:
    """THE one-line edit point (SS4). Returns exactly the five handlers
    named by BUILTIN_DATATYPES above. The CodeHandler this constructs is
    given deps.local_code_lookup; when that is None it raises
    UnsupportedBindingError for binding_target = 'local_code_system'
    rather than silently accepting one (open question 1)."""
    ...
```

### Rejected alternatives

| Alternative | Why not |
|---|---|
| React component or component-name string across the API | Ties the API response to a specific frontend rendering technology and, for a component name, is indistinguishable from a datatype name by another route - reopens exactly what `ControlKind` closes. |
| `ControlKind` named after datatypes (`code_control`, `string_control`) | Reintroduces the coupling SS3 exists to break: a new datatype would either need a new `ControlKind` member (a frontend edit every time) or reuse a wrongly-named one. |
| A custom ESLint rule for the frontend's no-datatype-dispatch rule | `Record<ControlKind, ComponentType<ControlProps>>` with no default branch already makes a missing kind a `tsc -b` error - a second, independent tool to maintain buys nothing beyond what the type system already gives for free. |
| A default/fallback datatype handler | Exactly the shape FR-16 warns against: an unregistered datatype fails silently (a facet disappears, a value is unfilterable) instead of loudly at `registry.get()`. |
| Decorator-based registration (`@registry.register("string")`) | Registration then happens as an import side effect, so the registered set depends on which modules happened to be imported - #53 cannot build a builtins-plus-synthetic registry deterministically without also controlling import order. |
| `pkgutil`/entry-point scanning of `datatypes/` | Discovery over a directory listing is one more place a `switch`-shaped thing could hide (a filename convention doing the dispatching), and it is unnecessary: five handlers is a five-line tuple. |
| `typing.Literal["code", "string", ...]` datatype union plus `assert_never` | A closed `Literal` is a second enumeration of the valid set, and every `assert_never` site becomes a required edit when a datatype is added - FR-77's failure condition wearing a type checker's clothes. |
| `enum.Enum` for `datatype` | Same objection as the `Literal` case, one layer further from the database: an enum member list is code that must be edited to add a datatype. |
| An ABC instead of a `Protocol` | Forces every handler, including #53's synthetic one, to inherit from a base class in `nptc.registry` - a `Protocol` lets a handler be any object with the right shape, which is what keeps mypy's completeness check independent of any particular class hierarchy. |
| One `serialise_<format>` method per export format | A handler would grow a method every time a new export format is added, even though the format selects an existing representation (SS7) - the multiplication FR-77 exists to prevent, just relocated to the method list instead of a `switch`. |
| `DatatypeRegistry` importing `nptc.exports` (or vice versa, both ways) | Either direction other than the one chosen inverts the dependency SS7 states explicitly (`exports -> registry`) and would need a second guard rule to detect the reverse edge as well. |
| A `coerce()` member on the handler | Coercion (turning a submitted string into the stored JSONB shape) is a distinct concern from validation and was not named in FR-77's four; adding it now is scope creep the ADR does not need to carry. |
| `IndexShape.expression` as raw SQL text | Defeats the point of a value-typed shape: the guard could no longer distinguish a handler-supplied SQL fragment from a literal, and NFR-22's posture would depend on review instead of construction. |
| Leaving the index mapping decision inside #54 | #54 would then choose per-datatype SQL itself, which is a `switch` on datatype in the search layer - the exact violation SS8 exists to prevent. |
| `filter_clause()` returning a string predicate | The same NFR-22 argument as the index shape: a string predicate cannot be proven safe by construction, only by review. |
| The contract in `shared/` | ADR-0003's criterion (both backend and transform need it) is not met; the transform has no notion of a datatype at all (SS6). |
| Registering a `boolean` handler now, to fully exercise the mechanism | FR-89's tri-state problem needs its own decision, not a drop-in default; extensibility is proven by #53's synthetic datatype instead. |
| `positiveInt` implemented as `decimal` with a `minimum` constraint | Leaves `1.5` merely refused by validation rather than unrepresentable by the schema fragment - ADR-0012's principle, applied here one layer up. |
| A guard rule flagging any bare string literal equal to a known datatype | `code`, `string` and `url` are ordinary English words and JSON keys elsewhere; the rule fires dozens of times on day one and is suppressed within a week. |
| A hardcoded datatype list inside the AST guard | Makes the guard itself a second enumeration of the valid set - the guard would violate the requirement it enforces. |
| Excluding all of `registry/` from the guard | Would also exempt `definitions.py` and `schema.py`, which have no legitimate reason to branch on datatype - only `datatypes/` earns the exclusion. |
| CONTRIBUTING.md prose plus review alone, no mechanical guard | [#137](https://github.com/MattCordell/nptc-platform/issues/137)'s acceptance criteria explicitly reject this: a concrete enforcement mechanism is required, not a documented convention. |

### Consequences

What each issue inherits from this ADR instead of choosing its own shape:

- **[#53](https://github.com/MattCordell/nptc-platform/issues/53)** the full Protocol
  signature list, so the synthetic-datatype test is writable from this ADR alone, with the
  `datatypes/__init__.py` manifest line (`BUILTIN_DATATYPES` plus `build_builtin_handlers`)
  pre-declared as inside the handler package. It may also drop `sort_key` (open question 5)
  and must construct `CodeHandler` with `local_code_lookup=None`, raising
  `UnsupportedBindingError` for `binding_target = 'local_code_system'` until #56 lands (open
  question 1).
- **[#51](https://github.com/MattCordell/nptc-platform/issues/51)** the write-time
  `registry.get()` resolution and the startup reconciliation
  (`SELECT DISTINCT datatype` subset-of registered set) - ADR-0012's unnamed compensating
  control for its no-CHECK/no-ENUM decision.
- **[#52](https://github.com/MattCordell/nptc-platform/issues/52)** `constraints_schema()` as
  the validator for ADR-0012's reserved `constraints` interior, run at definition-amendment
  time only.
- **[#54](https://github.com/MattCordell/nptc-platform/issues/54)** `IndexShape` as an input
  it consumes rather than computes, a `match` over `ValueExpression` instead of over
  `datatype`, and the cast-safety precondition carried as `requires_conformance_sweep` rather
  than a paragraph to remember.
- **[#55](https://github.com/MattCordell/nptc-platform/issues/55)** unaffected -
  privilege-level, never touches a handler.
- **[#139](https://github.com/MattCordell/nptc-platform/issues/139)** the three FR-16 members
  (`supported_filter_ops`, `filter_clause`, `facet_expression`) and the rule that it never
  learns a datatype name; the `code` facet's grouping key is deferred to it as open question
  2.
- **The P4 export renderers** a one-way `exports -> registry` import edge, a single
  `serialise` call per value, and the FR-83 prohibition on a second tag-strip call site.
- **The frontend ([#147](https://github.com/MattCordell/nptc-platform/issues/147)/[#151](https://github.com/MattCordell/nptc-platform/issues/151))**
  `FormControlDescriptor` on the property-definition API response, an exhaustive
  `Record<ControlKind, ...>` with no fallback branch, and one vitest literal guard.

Plus:

- `backend/tests/test_datatype_dispatch.py` is new, marked `@pytest.mark.req("FR-77")`.
- CONTRIBUTING.md's line on datatype dispatch and CLAUDE.md's FR-77 line get narrowed from
  `registry/` to `registry/datatypes/` **when [#53](https://github.com/MattCordell/nptc-platform/issues/53)
  lands** - flagged here so the wording travels with the code rather than being forgotten.
- [docs/architecture/data-model.md](../architecture/data-model.md) gains that pointer **in
  this PR** (its `constraints` and `datatype` rows): `constraints`'s interior is each
  handler's `constraints_schema()`, and `datatype`'s valid set is `DatatypeRegistry.known_datatypes()`
  checked at write time, not a schema-level constraint.
- FR-77 stays `planned`.
- The AST guard's six named limits are written down as review responsibilities rather than
  discovered later.

### Open questions deferred to named issues

Following ADR-0012's precedent of naming the deciding issue rather than guessing:

1. `LocalCodeLookup`'s shape -> [#56](https://github.com/MattCordell/nptc-platform/issues/56)
   (FR-90). Position taken here: [#53](https://github.com/MattCordell/nptc-platform/issues/53)
   constructs `CodeHandler` with `local_code_lookup=None` and raises
   `UnsupportedBindingError` for `binding_target = 'local_code_system'` - a loud refusal,
   never a silent pass.
2. `code`'s facet grouping key (code alone vs `(system, code)`) ->
   [#139](https://github.com/MattCordell/nptc-platform/issues/139), undecidable until #56
   settles whether local codes share the property-value shape. Both options are recorded here
   so #139 does not have to rediscover them: group by `code` alone (simpler, loses the
   system distinction where two systems reuse a code) or by `(system, code)` (correct, adds a
   composite facet key).
3. `FHIR_VALUE`'s per-datatype `CodeSystem.property.type` mapping -> the FR-64 supplement
   issue (not yet created). The target member (`SerialisationTarget.FHIR_VALUE`) is fixed
   here; the per-datatype FHIR types it maps to are not.
4. Unknown `ControlKind` at runtime (a client older than the server encounters a kind it does
   not recognise) -> [#151](https://github.com/MattCordell/nptc-platform/issues/151):
   render-nothing-plus-warning vs a read-only text view. Not decided here because it is a
   frontend UX call, not a registry design question.
5. Whether `sort_key` belongs on the handler at all ->
   [#53](https://github.com/MattCordell/nptc-platform/issues/53) may drop it. Named as the one
   speculative member of the eleven, rather than letting it arrive unargued.

   **Resolved by #53: dropped.** No caller needs it - #139's faceted filters need
   `supported_filter_ops`/`filter_clause`/`facet_expression`, not a sort key - and an unused
   Protocol member is a cost every future handler pays for no benefit. `DatatypeHandler` as
   implemented has ten members, not eleven; §1 and §10 above are historical (the design record
   as accepted) and are not edited to match, per this ADR's own practice of naming a resolution
   rather than rewriting the decision it resolves.

## Verification

Documentation-only, so verification is the repo's own gates plus a read-through against
[#137](https://github.com/MattCordell/nptc-platform/issues/137)'s acceptance criteria.

```powershell
pre-commit run --all-files          # pre-commit-hooks, ruff, ruff-format, mypy, frontend lint/format
uv run python scripts/traceability_check.py   # regenerates docs/requirements/traceability.md
uv run pytest scripts/tests                   # requirements.yaml schema + status validity
git diff --stat                               # confirm traceability.md regenerated cleanly
```

`.pre-commit-config.yaml` has no markdownlint hook; the `docs.yml` workflow runs `markdownlint`
and the lychee link check as separate CI jobs, not via pre-commit - worth eyeballing both
locally before pushing anyway, since they are what actually catch a bad heading or a broken
`../architecture/...` relative link in the new ADR or the index row.

Checked against #137's four acceptance criteria explicitly:

- Merged before [#53](https://github.com/MattCordell/nptc-platform/issues/53) opens a PR.
- Names a **concrete** enforcement mechanism - `backend/tests/test_datatype_dispatch.py` with
  its four rules, scan dirs, single exclusion and positive control, plus `mypy --strict` on
  the Protocol.
- Defines a handler precisely enough that #53's synthetic-datatype test is writable from the
  ADR alone - the Protocol block above gives save (`validate`), validate (`validate`,
  `constraints_schema`), filter (`supported_filter_ops`, `filter_clause`, `facet_expression`)
  and export (`serialise`) each a member to call.
- States which PRD requirements break if the boundary is violated - FR-83 (a second
  tag-strip call site appears in a per-datatype export branch, SS7) and FR-16 (a silent
  `else` makes a property invisibly unfilterable, SS8), each argued in its own section rather
  than listed.
