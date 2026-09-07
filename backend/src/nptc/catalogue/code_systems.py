"""The `system_token` alias registry for FR-17's exact-code lookup routes
(issue #140).

`GET /catalogue/code/{system_token}/{code}` needs a short, URL-friendly
alias for a code system's full URI - `sct` for `http://snomed.info/sct` -
because the URI itself (with its `:` and `/`) is not a legal single path
segment. This module is the one place that alias lives.

**Code, never a database table.** ADR-0019 rejected a database-backed
permission registry as unreviewable and untypecheckable in favour of a
frozen Python mapping reviewed row-by-row against the PRD; the same
argument applies here. A second registered system is a deployment adding
support for a code system the catalogue does not yet bind against - a
considered, reviewed change to this module, not a seed row an
administrator could add by accident through a future admin screen. Ships
with `sct` alone; see `docs/adr/0033-exact-code-lookup-routes.md`.

**One 404, shared by two different causes.** A `system_token` (or, on
`/catalogue/lookup`, a raw system URI) that is not registered, and one that
*is* registered but matches no published entry's code, both raise
`CodeLookupNotFoundError` with the identical fixed detail text
`REGISTERED_TOKENS_DETAIL` - a caller cannot use response text to tell "your
token is wrong" from "that code does not exist", matching the
non-disclosure precedent `nptc.catalogue.queries.get_entry` already sets
for a hidden-status `business_key`. `docs/adr/0033-...md` records the open
question this settles and why.

Never imports FastAPI: `SYSTEM_TOKEN_PATTERN` is consumed by
`nptc.api.routers.catalogue_shared`'s `SystemTokenPath` to build a `Path`
annotation, and the raising functions below are called from
`nptc.api.routers.catalogue`, but this module itself stays a plain,
HTTP-framework-free registry - matching `nptc.catalogue.queries`' own
posture.
"""

from __future__ import annotations

import re
from typing import Final

from nptc.catalogue.errors import CodeLookupNotFoundError
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM

__all__ = [
    "REGISTERED_TOKENS_DETAIL",
    "SYSTEM_TOKENS",
    "SYSTEM_TOKEN_PATTERN",
    "require_registered_system",
    "system_for_token",
]

#: Frozen token -> system URI registry. See the module docstring for why
#: this is code, not a table.
SYSTEM_TOKENS: Final[dict[str, str]] = {
    "sct": SNOMED_CT_SYSTEM,
}

#: A well-formed token: lowercase ASCII letters, digits and hyphens, first
#: character a letter, 1-32 characters long. Permissive enough for a future
#: alias, strict enough that a malformed one is a 422 from the path pattern
#: before any query runs (`BusinessKeyPath`'s own precedent) - never the
#: "well-formed but unregistered" 404 below.
SYSTEM_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

#: The one shared 404 sentence - see the module docstring's "one 404,
#: shared by two different causes". Names each registered system as both
#: its token and its URI, since a `/catalogue/lookup` caller supplied a URI
#: and never saw the token at all - naming only the token would leave that
#: caller told about a parameter they didn't use (issue #140 review). Built
#: from `SYSTEM_TOKENS` rather than hand-listed, so a second registered
#: alias updates this message for free.
REGISTERED_TOKENS_DETAIL: Final[str] = (
    "No published catalogue entry matches this system and code. Registered code "
    "systems: "
    + ", ".join(f"{token} ({uri})" for token, uri in sorted(SYSTEM_TOKENS.items()))
    + "."
)


def system_for_token(token: str) -> str:
    """The system URI a registered `token` aliases.

    Raises `CodeLookupNotFoundError` for a `token` not in `SYSTEM_TOKENS` -
    a well-formed token (already past `SYSTEM_TOKEN_PATTERN`'s 422 gate)
    that simply is not one this deployment has registered, which is a 404,
    not a 422 (see the module docstring)."""
    system = SYSTEM_TOKENS.get(token)
    if system is None:
        raise CodeLookupNotFoundError(f"system_token {token!r} is not registered")
    return system


def require_registered_system(system: str) -> str:
    """`system` unchanged, if it is one of `SYSTEM_TOKENS`' target URIs -
    otherwise `CodeLookupNotFoundError`, on the same fixed sentence
    `system_for_token` raises.

    `GET /catalogue/lookup` accepts the full URI rather than a token, but
    the same registry gates it: a URI this deployment has not aliased is
    exactly as unresolvable as an unregistered token, and the module
    docstring's shared-sentence decision applies to both."""
    if system not in SYSTEM_TOKENS.values():
        raise CodeLookupNotFoundError(f"system {system!r} is not registered")
    return system
