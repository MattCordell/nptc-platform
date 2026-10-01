"""Answers a coded property's value source - its bound SNOMED value set or
governed local code system - behind one identical shape (FR-10, FR-52,
FR-90). See `nptc.registry.handlers.BindingSpec` for the two
`binding_target`s this dispatches on. ADR-0031 records the design and
ADR-0038 the resolve-by-code path.

**The only place that branches on `binding_target`.** ADR-0013 SS5 names a
`binding_target` switch scattered through storage, export or search code as
a datatype-dispatch violation that the AST guard
(`backend/tests/test_datatype_dispatch.py`) cannot catch syntactically.
`list_property_values` and `resolve_property_values` are the only backend
functions that read `binding.binding_target`; the router and its response
models never see it.

**Lives in `nptc.catalogue`, not `nptc.registry`**, for the leaf-rule reason
`nptc.catalogue.local_codes` and `property_values` give (ADR-0013 SS2): it
needs a `Session` and a live `TerminologyClient`, neither of which
`nptc.registry` may import.

**Paging is offset/count, not the catalogue's usual keyset cursor**
(ADR-0031). `$expand` only speaks offset/count, so a shape common to both
targets has no keyset option on the SNOMED side.

**Active-only on both sides, for the picker page.** `list_local_codes`
excludes a deprecated code and every code of a deprecated system, and the
SNOMED side passes `active_only=True` to `expand`. A value already recorded
against such a code still renders through `DatabaseLocalCodeLookup.resolve`
and `CodeHandler.serialise`; this module adds a read path and replaces
neither.

**`resolve_property_values` is deliberately not active-only, and not
intersected with the property's bound value set** (ADR-0038). It returns the
display value of a code the catalogue already recorded, for a chip or
carried filter value that the `DEFAULT_PAGE_SIZE` page may not include. A
code the RCPA has since narrowed out of the ECL, or marked inactive, must
still resolve to its label. A code neither side can resolve is omitted, never
invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy.orm import Session

from nptc.catalogue.local_codes import find_local_codes, list_local_codes
from nptc.db.definitions import load_definition
from nptc.db.property_specs import spec_for
from nptc.terminology.concepts import classify_terminology_error
from nptc_shared.sctid import has_valid_format
from nptc_shared.terminology import TerminologyClient, TerminologyConfigError, TerminologyError
from nptc_shared.terminology.snomed import (
    ecl_from_implicit_value_set_url,
    ecl_set_of,
    edition_from_implicit_value_set_url,
)

__all__ = [
    "PropertyNotCodeTypeError",
    "PropertyValueSelectionConflictError",
    "PropertyValueSourceMisconfiguredError",
    "ValueItem",
    "ValuePage",
    "list_property_values",
    "resolve_property_values",
]

#: A page-size ceiling for a concept-picker page: `$expand` and
#: `list_local_codes` both take a page size and have no "everything" mode.
#: Distinct from `nptc.catalogue.search`'s own `DEFAULT_PAGE_SIZE`.
DEFAULT_PAGE_SIZE = 50


class PropertyNotCodeTypeError(ValueError):
    """Raised when `key` names a real property definition that is not
    `datatype == "code"` - it has no bound value source at all, so
    `/values` is a client mistake naming the wrong kind of property, not an
    absence (`PropertyDefinitionNotFoundError` below covers that case)."""

    http_status: ClassVar[int] = 422


class PropertyValueSelectionConflictError(ValueError):
    """Raised by the `/values` route when `code` is combined with `filter`,
    `offset` or `count` (ADR-0038). Pure request-shape validation, so it sits
    beside `PropertyNotCodeTypeError` rather than in a router;
    `resolve_property_values` itself takes no such parameters and never raises
    it."""

    http_status: ClassVar[int] = 422


class PropertyValueSourceMisconfiguredError(Exception):
    """Raised when `key` names a coded property whose stored
    `value_set_uri`/`edition` pair cannot be resolved to a real SNOMED
    `Edition`; see `nptc_shared.terminology.snomed.
    edition_from_implicit_value_set_url` for the two recognised
    `value_set_uri` shapes.

    A data-integrity fault in the stored definition, never a caller mistake.
    A well-formed row always resolves, so this is defence in depth, with the
    posture of `nptc.terminology.errors.TerminologyConfigError`: the service
    is misconfigured.
    """

    http_status: ClassVar[int] = 500


@dataclass(frozen=True, slots=True)
class ValueItem:
    """One offerable value - identical in shape whichever `binding_target`
    served it."""

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
    # The ECL always comes from `value_set_uri`. So does the edition when the
    # URI is module-qualified, so a pinned version is never silently dropped;
    # `edition_label` (the stored `binding.edition`) is only the fallback for
    # the PRD S6.6 bare-system URI, which names no module (see
    # `edition_from_implicit_value_set_url`). Both parses fail the same way,
    # for a pair that is not this builder's own output, so they share one
    # `except`.
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
            # FR-82: a picker needs the edition's own preferred term.
            # `display_language` is `None` for an edition that sets none,
            # which `expand` treats as the server default.
            display_language=edition.display_language,
        )
    except TerminologyConfigError:
        # Already a 500 via `nptc.api.errors`; it must not reach
        # `classify_terminology_error` (see `resolve_concept`).
        raise
    except TerminologyError as exc:
        # No `not_found` factory: absence here means the value set did not
        # resolve, not that one code is missing (`classify_terminology_error`
        # docstring).
        raise classify_terminology_error(exc) from exc

    items = tuple(
        ValueItem(code=concept.code, display=concept.display) for concept in expansion.concepts
    )
    # `expansion.total` is `None` when the server reports none.
    # `offset + len(items)` is the honest floor; `len(items)` alone would read
    # to a paging client as fewer rows than it has already seen.
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
        # No `spec.datatype != "code"` comparison (FR-77, ADR-0013 SS5: the AST
        # guard's datatype-compare rule). The `binding_required_for_code` CHECK,
        # `(datatype = 'code') = (binding_target IS NOT NULL)`, makes
        # `binding is None` equivalent, so this is a structural None-check, not
        # a second encoding of the rule.
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


def _resolve_local_code_system_values(
    session: Session, *, system_key: str, codes: Sequence[str]
) -> ValuePage:
    # `find_local_codes` owns the `local_code`/`local_code_system` join,
    # beside `find_local_code_with_system_status`. This function does the
    # batching - one `IN (...)` call, not N, applying FR-52's discipline to an
    # in-process read as `_resolve_value_set_values` applies it to the
    # terminology server - and restores `codes`' order, dropping codes neither
    # side resolved.
    rows = find_local_codes(session, system_key=system_key, codes=codes)
    by_code = {row.code: row for row in rows}
    items = tuple(
        ValueItem(code=by_code[code].code, display=by_code[code].display)
        for code in codes
        if code in by_code
    )
    return ValuePage(items=items, total=len(items))


def _resolve_value_set_values(
    client: TerminologyClient,
    *,
    key: str,
    value_set_uri: str,
    edition_label: str,
    codes: Sequence[str],
) -> ValuePage:
    # Only the edition is needed, not the bound ECL: this resolution is
    # deliberately not scoped to the property's current value set.
    try:
        edition = edition_from_implicit_value_set_url(value_set_uri, label=edition_label)
    except ValueError as exc:
        raise PropertyValueSourceMisconfiguredError(
            f"property {key!r}'s stored value_set_uri/edition could not be interpreted as a "
            "SNOMED implicit ECL value set URI naming a recognised edition"
        ) from exc

    # A code that is not SCTID-shaped cannot be looked up or safely
    # concatenated into `ecl_set_of`'s query. It is dropped, not allowed to
    # fail the batch: an unresolvable code is omitted, not an error.
    valid_codes = tuple(code for code in codes if has_valid_format(code))
    if not valid_codes:
        return ValuePage(items=(), total=0)

    try:
        expansion = client.expand(
            ecl_set_of(valid_codes),
            edition=edition,
            count=len(valid_codes),
            # Not `active_only=True`: an inactive code must still resolve to
            # its label (module docstring).
            active_only=False,
            display_language=edition.display_language,
        )
    except TerminologyConfigError:
        raise
    except TerminologyError as exc:
        raise classify_terminology_error(exc) from exc

    items = tuple(
        ValueItem(code=concept.code, display=concept.display) for concept in expansion.concepts
    )
    return ValuePage(items=items, total=len(items))


def resolve_property_values(
    session: Session,
    client: TerminologyClient,
    *,
    key: str,
    codes: Sequence[str],
) -> ValuePage:
    """The display value for each of `codes`, whatever its position in (or
    absence from) `list_property_values`'s page (ADR-0038). Neither
    active-only nor scoped to the bound value set; see the module docstring.

    `items` omits a code neither side can resolve, and `total` is always
    `len(items)`, so the two cannot disagree. A duplicate in `codes` resolves
    once: neither branch dedupes, each looks a repeated code up once per
    occurrence, so a repeat would otherwise double up in `items` and inflate
    `total`. Raises the same `PropertyDefinitionNotFoundError` and
    `PropertyNotCodeTypeError` as `list_property_values`, for the same
    reasons.
    """
    distinct_codes = list(dict.fromkeys(codes))
    definition = load_definition(session, key)
    spec = spec_for(definition)
    if spec.binding is None:
        raise PropertyNotCodeTypeError(
            f"property {key!r} is not a coded property; it has no bound value source"
        )

    binding = spec.binding
    if binding.binding_target == "local_code_system":
        assert binding.local_code_system_key is not None  # DB CHECK-enforced pairing
        return _resolve_local_code_system_values(
            session, system_key=binding.local_code_system_key, codes=distinct_codes
        )

    assert binding.value_set_uri is not None  # DB CHECK-enforced pairing
    return _resolve_value_set_values(
        client,
        key=key,
        value_set_uri=binding.value_set_uri,
        edition_label=binding.edition,
        codes=distinct_codes,
    )
