"""Answers a coded property's value source - its bound SNOMED value set or
governed local code system - behind one identical shape (issue #247, FR-10,
FR-52, FR-90). See `nptc.registry.handlers.BindingSpec` for the two
`binding_target`s this dispatches on.

**The only place that branches on `binding_target`.** ADR-0013 SS5 names a
`binding_target` proxy switch scattered through storage/export/search code
as the datatype-dispatch violation the AST guard
(`backend/tests/test_datatype_dispatch.py`) cannot catch syntactically -
`list_property_values` below is the one function in the whole backend that
reads `binding.binding_target`; the router and its response model never see
it at all (issue #247's own acceptance criterion: "nothing in `frontend/src`
branches on `binding_target`" - true a fortiori of the backend serving it).

**Lives in `nptc.catalogue`, not `nptc.registry`**, for the same leaf-rule
reason `nptc.catalogue.local_codes`/`property_values` already do (ADR-0013
SS2): this module needs a `Session` (to load the definition and to query
`LocalCode`) and a live `TerminologyClient` call, neither of which
`nptc.registry`'s leaf rule permits it to import.

**Paging is offset/count, not the catalogue's usual opaque keyset cursor**
(contrast `nptc.catalogue.search`/`entries`, both ADR-0024). That is
deliberate, not an oversight: `TerminologyClient.expand`'s FHIR `$expand`
only speaks offset/count, so a shape uniform across both binding targets
has no keyset option on the SNOMED side - `list_local_codes`'s own Postgres
query could support keyset paging in isolation, but forcing an artificial
two-shape inconsistency onto what must be identical wire behaviour would
defeat the whole point of one shared route.

**Active-only on both sides.** A deprecated local code, or one belonging to
a deprecated local code system, is excluded by `list_local_codes` itself;
the SNOMED side passes `active_only=True` to `expand` for the same reason.
Either way, a value already recorded against a since-deprecated code (or
system) still renders, unchanged, through the existing resolution paths
(`DatabaseLocalCodeLookup.resolve` / `CodeHandler.serialise`) - this module
is additive, a new read path, not a replacement for either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy.orm import Session

from nptc.catalogue.local_codes import list_local_codes
from nptc.db.definitions import load_definition
from nptc.db.property_specs import spec_for
from nptc.terminology.concepts import classify_terminology_error
from nptc_shared.terminology import TerminologyClient, TerminologyConfigError, TerminologyError
from nptc_shared.terminology.snomed import (
    ecl_from_implicit_value_set_url,
    edition_from_implicit_value_set_url,
)

__all__ = [
    "PropertyNotCodeTypeError",
    "PropertyValueSourceMisconfiguredError",
    "ValueItem",
    "ValuePage",
    "list_property_values",
]

#: FHIR `$expand`/`list_local_codes` both take a page size, not a "give me
#: everything" mode - a sensible ceiling for a concept-picker page, distinct
#: from `nptc.catalogue.search`'s own `DEFAULT_PAGE_SIZE` (issue #247's
#: route is not that one).
DEFAULT_PAGE_SIZE = 50


class PropertyNotCodeTypeError(ValueError):
    """Raised when `key` names a real property definition that is not
    `datatype == "code"` - it has no bound value source at all, so
    `/values` is a client mistake naming the wrong kind of property, not an
    absence (`PropertyDefinitionNotFoundError` below covers that case)."""

    http_status: ClassVar[int] = 422


class PropertyValueSourceMisconfiguredError(Exception):
    """Raised when `key` names a coded property whose own stored
    `value_set_uri`/`edition` pair cannot be resolved to a real SNOMED
    `Edition` - see `nptc_shared.terminology.snomed.
    edition_from_implicit_value_set_url` for the two recognised
    `value_set_uri` shapes and when each one needs `edition` to agree with
    a known label.

    A data-integrity fault in the stored definition, never a caller mistake
    - a well-formed row always resolves (every real `value_set_uri` in this
    database is either module-qualified, self-describing, or the PRD's own
    bare-system shape paired with a real `edition` label). This path exists
    for defence in depth, not a reachable client scenario for a
    well-formed row; matches `nptc.terminology.errors.TerminologyConfigError`'s
    own "service is misconfigured, not a caller mistake" posture.
    """

    http_status: ClassVar[int] = 500


@dataclass(frozen=True, slots=True)
class ValueItem:
    """One offerable value - identical in shape regardless of which
    `binding_target` served it (issue #247's own acceptance criterion)."""

    code: str
    display: str | None


@dataclass(frozen=True, slots=True)
class ValuePage:
    items: tuple[ValueItem, ...]
    total: int


def _value_set_page(
    client: TerminologyClient,
    *,
    key: str,
    value_set_uri: str,
    edition_label: str,
    filter: str | None,
    offset: int,
    count: int,
) -> ValuePage:
    # The ECL always comes from value_set_uri. The edition does too when
    # the URI is module-qualified (a pinned version, if present, must never
    # be silently dropped) - `edition_label` (the stored `binding.edition`)
    # is only consulted as a fallback for the PRD S6.6 bare-system URI
    # shape, which carries no module of its own at all (see
    # `edition_from_implicit_value_set_url`'s own docstring for both
    # shapes, and why a label-only reconstruction was wrong: issue #247
    # review). Both parses fail the same way for the same reason - a
    # `value_set_uri`/`edition` pair that is not this builder's own output
    # shape - so both share one except clause.
    try:
        ecl = ecl_from_implicit_value_set_url(value_set_uri)
        edition = edition_from_implicit_value_set_url(value_set_uri, label=edition_label)
    except ValueError as exc:
        raise PropertyValueSourceMisconfiguredError(
            f"property {key!r}'s stored value_set_uri/edition could not be interpreted as a "
            "SNOMED implicit ECL value set URI naming a recognised edition"
        ) from exc

    try:
        expansion = client.expand(
            ecl,
            edition=edition,
            count=count,
            offset=offset,
            active_only=True,
            filter=filter,
            # FR-82: a picker needs the edition's own preferred term, not
            # whatever the server defaults to - `edition.display_language`
            # is `None` for editions (e.g. international) that don't set
            # one, which `expand` already treats as "server default".
            display_language=edition.display_language,
        )
    except TerminologyConfigError:
        # Already mapped to 500 by nptc.api.errors - must never reach
        # classify_terminology_error, matching resolve_concept's own
        # carve-out (see that module's docstring).
        raise
    except TerminologyError as exc:
        # No not_found factory: an absence-shaped failure here means the
        # value set itself did not resolve, not that one code is missing -
        # see classify_terminology_error's own docstring for why this
        # route passes none and lets that fall through to the catch-all.
        raise classify_terminology_error(exc) from exc

    items = tuple(
        ValueItem(code=concept.code, display=concept.display) for concept in expansion.concepts
    )
    # `expansion.total` is `None` when the server didn't report one -
    # `offset + len(items)` is the honest floor (at least this many exist),
    # never `len(items)` alone, which at offset > 0 reads to a paging
    # client as fewer rows than it has already seen.
    total = expansion.total if expansion.total is not None else offset + len(items)
    return ValuePage(items=items, total=total)


def _local_code_system_page(
    session: Session,
    *,
    system_key: str,
    filter: str | None,
    offset: int,
    count: int,
) -> ValuePage:
    codes, total = list_local_codes(
        session, system_key=system_key, filter=filter, offset=offset, limit=count
    )
    items = tuple(ValueItem(code=code.code, display=code.display) for code in codes)
    return ValuePage(items=items, total=total)


def list_property_values(
    session: Session,
    client: TerminologyClient,
    *,
    key: str,
    filter: str | None = None,
    offset: int = 0,
    count: int = DEFAULT_PAGE_SIZE,
) -> ValuePage:
    """FR-10's picker data source: every offerable value for the coded
    property named `key`, from whichever value source its own binding
    names - identical in shape either way (see the module docstring).

    Raises `PropertyDefinitionNotFoundError` (404, reused from
    `nptc.catalogue.property_values` via `nptc.db.definitions.
    load_definition` - the same type `GET /registry/properties/{key}`
    already raises for an unknown key) or `PropertyNotCodeTypeError` (422)
    when `key` names a real property that is not `datatype == "code"` and
    so has no bound value source to serve at all.
    """
    definition = load_definition(session, key)
    spec = spec_for(definition)
    if spec.binding is None:
        # No `spec.datatype != "code"` comparison alongside this (FR-77,
        # ADR-0013 SS5 - the AST guard's own "datatype-compare" rule): the
        # DB's own `binding_required_for_code` CHECK
        # ("(datatype = 'code') = (binding_target IS NOT NULL)") already
        # makes `binding is None` exactly equivalent, so this is a
        # structural None-check, never a second, string-literal encoding of
        # the same rule.
        raise PropertyNotCodeTypeError(
            f"property {key!r} is not a coded property; it has no bound value source"
        )

    binding = spec.binding
    if binding.binding_target == "local_code_system":
        assert binding.local_code_system_key is not None  # DB CHECK-enforced pairing
        return _local_code_system_page(
            session,
            system_key=binding.local_code_system_key,
            filter=filter,
            offset=offset,
            count=count,
        )

    assert binding.value_set_uri is not None  # DB CHECK-enforced pairing
    return _value_set_page(
        client,
        key=key,
        value_set_uri=binding.value_set_uri,
        edition_label=binding.edition,
        filter=filter,
        offset=offset,
        count=count,
    )
