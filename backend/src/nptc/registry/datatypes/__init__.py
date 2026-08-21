"""The builtin datatype handlers manifest (FR-77, ADR-0013 SS4).

This module is the one-line edit point for a new builtin datatype - adding a
handler here (plus its own module) is *not* "an edit outside the handler
module": FR-77's failure condition is edits in other *layers*, and this file
is the handler package's own manifest of its own contents.

`BUILTIN_DATATYPES` is a deps-free enumeration of the built-in set, imported
by `backend/tests/test_datatype_dispatch.py` (the AST guard) instead of
`build_builtin_handlers` - the guard has no business constructing a
`HandlerDeps`/`TerminologyClient` it does not need. Still one enumeration,
inside the handler package the guard is scoped around, not a second one the
guard maintains itself.
"""

from __future__ import annotations

from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.decimal import DecimalHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.datatypes.url import UrlHandler
from nptc.registry.handlers import DatatypeHandler, HandlerDeps

BUILTIN_DATATYPES: tuple[str, ...] = ("code", "string", "decimal", "positiveInt", "url")
"""Exactly PRD SS6.5's five, no sixth. Extensibility is proven by #53's
synthetic-datatype test, not by pre-registering speculative ones.

`boolean` is deliberately excluded, on a semantic ground load-bearing for
FR-89 (ADR-0013 SS9): a `0..1` boolean has three states, and the absent
state is exactly the "accepts any specimen" vs "nobody has filled this in
yet" ambiguity FR-89's `specimen_unconstrained` core column exists to
destroy. The registry *can* accept a `boolean` handler; none is registered
here, and registering one later needs its own decision about that tri-state
problem, not a drop-in default."""


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
