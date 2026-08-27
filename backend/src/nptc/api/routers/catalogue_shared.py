"""Response models and helpers shared between the public read router
(`routers/catalogue.py`, FR-20) and the authenticated write router
(`routers/catalogue_bindings.py`, issue #219).

Both routers deliberately stay separate modules - `catalogue.py`'s own
docstring explains why a POST cannot fold into the public surface - but a
binding written by one has to come back out looking exactly like a binding
read by the other, so `Binding`/`BindingList`/`_binding`/`BusinessKeyPath`
live here rather than being duplicated or imported private-to-private
between the two routers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict

from nptc.api.errors import StoredFSNNotRenderableError
from nptc.catalogue import queries
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.exports.semantic_tag import EmptyDisplayTermError, NotAServedFSNError, render_display_term

__all__ = [
    "Binding",
    "BindingList",
    "BusinessKeyPath",
]

#: Shared by every route addressing an entry by its public identifier. A
#: business key that is not `NPTC-` plus at least six digits (FR-03) is a
#: 422 here, before any query runs.
BusinessKeyPath = Annotated[
    str,
    Path(
        pattern=BUSINESS_KEY_PATTERN.pattern,
        description="The entry's public identifier, e.g. `NPTC-000247` (FR-03).",
        examples=["NPTC-000247"],
    ),
]


class Binding(BaseModel):
    """A SNOMED CT code binding, active or retired.

    `code` is a string, always (FR-06). `display_term` is `fsn` with its
    semantic tag removed exactly once, by FR-83's single sanctioned
    renderer - it is derived here rather than stored, because a stored
    stripped value is indistinguishable from an unstripped one and that
    ambiguity is what makes double-stripping possible.

    A retired binding carries `retirement_reason` and, where PRD FR-08's
    replacement case applies, `replaced_by_code` - the successor's *code*,
    which is what a client holding the retired one needs in order to move.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    code: str
    fsn: str
    display_term: str
    au_preferred_term: str | None
    edition_hint: str
    status: str
    retirement_reason: str | None
    replaced_by_code: str | None


class BindingList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[Binding]


def _display_term(fsn: str) -> str:
    """FR-83's one sanctioned strip, with its refusal re-labelled: the
    caller here supplied a business key/code, not the FSN, so a stored FSN
    that fails to strip is a 500 (`StoredFSNNotRenderableError`), not the
    422 `render_display_term` raises on its own."""
    try:
        return render_display_term(fsn)
    except (NotAServedFSNError, EmptyDisplayTermError) as exc:
        raise StoredFSNNotRenderableError(str(exc)) from exc


def _binding(row: queries.BindingRow) -> Binding:
    return Binding(
        system=row.system,
        code=row.code,
        fsn=row.fsn,
        display_term=_display_term(row.fsn),
        au_preferred_term=row.au_preferred_term,
        edition_hint=row.edition_hint,
        status=row.status,
        retirement_reason=row.retirement_reason,
        replaced_by_code=row.replaced_by_code,
    )
