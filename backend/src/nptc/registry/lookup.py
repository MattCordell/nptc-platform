"""`LocalCodeLookup`'s real shape (issue #56, FR-90), owed to
`docs/adr/0013-datatype-handler-registry.md`'s placeholder: "Placeholder
with no members - #56 (FR-90) owns its real shape."

**Why a `Protocol`, not the `LocalCode` model directly.** ADR-0013
constructs `HandlerDeps` (and, through it, `CodeHandler`) with
`local_code_lookup: LocalCodeLookup | None = None`, and #53's
`build_builtin_handlers` must remain importable without a live database
session (`NFR-37`: `transform/tests`/`shared/tests` run with no network
access, and `nptc.terminology`'s own stub-client precedent is exactly this
shape). A `Protocol` lets `nptc.catalogue`/`nptc.registry` depend on the
*shape* of a lookup without depending on `sqlalchemy.orm.Session` at
import time, matching `nptc.terminology`'s `TerminologyClient` contract
(ADR-0003) for the same reason.

**This module only defines the shape and a database-backed
implementation - it does not wire `CodeHandler` up to it.** ADR-0013's own
open-questions list assigns "`LocalCodeLookup`'s shape" to #56 and leaves
`CodeHandler`'s adoption of it, and lifting `UnsupportedBindingError` for
`binding_target = 'local_code_system'`, to #53 - #51 is in flight on a
separate branch as this lands, and touching handler wiring here would
collide with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from nptc.registry.local_codes import find_local_code

__all__ = ["DatabaseLocalCodeLookup", "LocalCodeLookup", "ResolvedLocalCode"]


@dataclass(frozen=True, slots=True)
class ResolvedLocalCode:
    """What a `LocalCodeLookup` returns for a code that exists - just
    enough for `CodeHandler` to validate a `property_value` and render a
    display term, without exposing the full `LocalCode` ORM row (mirrors
    `nptc.terminology`'s own served-label-shaped return types, not the
    SQLAlchemy model, across that module boundary)."""

    code: str
    display: str
    status: str
    provisional: bool


class LocalCodeLookup(Protocol):
    """The read contract a `code`-datatype handler needs to validate a
    value bound to `binding_target = 'local_code_system'` (PRD line 415:
    "validated internally against the platform's own `LocalCode` table,
    because Ontoserver does not hold them"). Deliberately narrow - no
    write methods; management goes through `nptc.registry.local_codes`,
    gated on `Permission.REGISTRY_MANAGE` (FR-90's "administrator-only
    management")."""

    def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None:
        """Returns the resolved code, or `None` if `code` does not exist
        in the `system_key` system at all - a handler distinguishes "does
        not exist" from "exists but deprecated" via `ResolvedLocalCode.
        status`, the same distinction `code_binding.status` supports for
        SNOMED bindings."""
        ...


class DatabaseLocalCodeLookup:
    """The database-backed `LocalCodeLookup` implementation, built on
    `nptc.registry.local_codes.find_local_code`. Holds a `Session` for the
    lifetime of one request/job, matching every other service-layer
    caller's own session-per-call-site convention (`nptc.catalogue.
    bindings.create_binding` and siblings) - this is not a singleton, so
    `HandlerDeps` construction (ADR-0013) stays per-request, matching
    `TerminologyClient`'s own non-singleton treatment (NFR-37)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None:
        local_code = find_local_code(self._session, system_key=system_key, code=code)
        if local_code is None:
            return None
        return ResolvedLocalCode(
            code=local_code.code,
            display=local_code.display,
            status=local_code.status,
            provisional=local_code.provisional,
        )
