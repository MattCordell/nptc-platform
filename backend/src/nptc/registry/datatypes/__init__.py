"""The builtin datatype handlers manifest (FR-77, ADR-0013 SS4).

The one-line edit point for a new builtin datatype. Adding a handler here, plus its own
module, is not an edit outside the handler module: FR-77's failure condition is edits in other
*layers*, and this file is the handler package's manifest of its own contents.

`BUILTIN_DATATYPES` is a dependency-free enumeration that
`backend/tests/test_datatype_dispatch.py` (the AST guard) imports instead of
`build_builtin_handlers`, which would force it to construct a `HandlerDeps` and a
`TerminologyClient`. It stays one enumeration, inside the package the guard is scoped around.
"""

from __future__ import annotations

from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.decimal import DecimalHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.datatypes.url import UrlHandler
from nptc.registry.handlers import DatatypeHandler, HandlerDeps

BUILTIN_DATATYPES: tuple[str, ...] = ("code", "string", "decimal", "positiveInt", "url")
"""Exactly PRD SS6.5's five. A synthetic datatype in `test_synthetic_datatype.py` proves
extensibility, so no speculative ones are pre-registered.

`boolean` is deliberately excluded (ADR-0013 SS9): a `0..1` boolean has three states, and its
absent state is the "no" versus "nobody has filled this in yet" ambiguity. The registry can
accept a `boolean` handler; registering one needs its own decision about that tri-state
problem."""


def build_builtin_handlers(deps: HandlerDeps) -> tuple[DatatypeHandler, ...]:
    """Returns exactly the five handlers named by `BUILTIN_DATATYPES` above.

    Explicit construction, not a decorator or a `pkgutil` scan (ADR-0013 SS4
    rejects both): the registered set never depends on import order.
    """
    return (
        CodeHandler(
            terminology_client=deps.terminology_client,
            local_code_lookup=deps.local_code_lookup,
        ),
        StringHandler(),
        DecimalHandler(),
        PositiveIntHandler(),
        UrlHandler(),
    )
