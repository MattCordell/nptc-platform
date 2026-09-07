"""FR-98's structural guard (issue #144): every response model reachable
from the real app that carries a label-shaped field declares
`label_provenance` covering exactly those fields.

Modelled on `route_inventory_support.py`'s recursive router walk (the same
gotcha applies: `app.include_router(...)` does not flatten routes into
`app.routes`) and `test_datatype_dispatch.py`'s "positive control + a guard
on the guard" idiom - a checking function that can never actually fail is
worse than no check at all.

No fixtures beyond the 391483001 regression test at the bottom: building
`create_app()` and walking its schema touches no database (`get_terminology_
client()`, called eagerly by `create_app`, opens no socket) and this module
otherwise joins `test_settings.py`/`test_sql_parameterisation.py` as tests
that must not start Docker.
"""

from __future__ import annotations

import re
import typing
from collections.abc import Iterable, Iterator
from typing import Annotated, Any, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.engine import Connection
from starlette.routing import BaseRoute

from nptc.api.app import create_app
from nptc.api.labels import LabelProvenance

# --- the recursive route walk (mirrors route_inventory_support.py) --------


def _iter_api_routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        nested = getattr(route, "routes", None)
        if nested is None:
            original_router = getattr(route, "original_router", None)
            nested = getattr(original_router, "routes", None)
        if nested is not None:
            yield from _iter_api_routes(nested)


def _unwrap(annotation: Any) -> Iterator[Any]:
    """Every type reachable one level down from `annotation` - through
    `X | None`, `list[X]`, `dict[str, X]`, `Annotated[X, ...]` - so the
    caller can recurse until it bottoms out at an actual class."""
    origin = get_origin(annotation)
    if origin is None:
        return
    args = get_args(annotation)
    if origin is Annotated:
        yield args[0]
        return
    yield from args


def _collect_models(root: type[BaseModel]) -> set[type[BaseModel]]:
    """Every `BaseModel` subclass reachable from `root`, including `root`
    itself - through nested fields, list/dict containers and `X | None`.

    `typing.get_type_hints`, not raw `model_fields` annotations: a field
    declared before its own type is fully defined in the file (`from
    __future__ import annotations` postpones every annotation to a string)
    can still be an unresolved `ForwardRef` on `model_fields` at this point -
    `get_type_hints` is what actually resolves it against the model's own
    module namespace, the same resolution FastAPI relies on to build the
    OpenAPI schema in the first place.
    """
    seen: set[type[BaseModel]] = set()

    def visit(model: type[BaseModel]) -> None:
        if model in seen:
            return
        seen.add(model)
        hints = typing.get_type_hints(model)
        for annotation in hints.values():
            stack = [annotation]
            while stack:
                current = stack.pop()
                if isinstance(current, type) and issubclass(current, BaseModel):
                    visit(current)
                    continue
                stack.extend(_unwrap(current))

    visit(root)
    return seen


#: The label-bearing field names this guard already knows about. Not
#: exhaustive by promise - `test_label_field_name_set_is_not_stale` below is
#: what keeps it honest as the schema grows.
LABEL_FIELD_NAMES: frozenset[str] = frozenset(
    {"fsn", "au_preferred_term", "preferred_term", "term"}
)


def label_provenance_gaps(model: type[BaseModel]) -> set[str]:
    """The label-bearing field(s) on `model` (by name, from
    `LABEL_FIELD_NAMES`) that `label_provenance` does not cover - empty if
    `model` carries no label field, or if it declares a `label_provenance`
    field shaped correctly for the ones it does carry.

    "Shaped correctly" is a presence-and-shape check, not a check of an
    instance's actual dict *contents*: a class definition alone cannot say
    which keys a `dict[str, LabelProvenance]` field will hold at runtime
    (`Binding.label_provenance` is populated by `binding_from_row`, not by
    the class itself). `dict[str, LabelProvenance]` is accepted regardless
    of how many label fields the model carries - `EntrySummary`/`EntryDetail`
    choose it even for their one field (`preferred_term`), matching
    `Binding`'s two - so only `Designation`'s singular `LabelProvenance`
    (its one field, `term`) is a genuinely single-field-only shape: a
    singular value cannot unambiguously describe two or more different
    fields, so it is accepted only when there is exactly one label field.
    `test_known_models_declare_provenance_for_exactly_their_own_label_fields`
    below closes the gap a class-level shape check cannot: it checks the
    real models' actual assembled dict *keys* against their own label
    fields.
    """
    hints = typing.get_type_hints(model)
    label_fields = set(hints) & LABEL_FIELD_NAMES
    if not label_fields:
        return set()
    provenance_type = hints.get("label_provenance")
    if provenance_type is None:
        return label_fields
    if provenance_type is LabelProvenance and len(label_fields) == 1:
        return set()
    if get_origin(provenance_type) is dict:
        key_type, value_type = get_args(provenance_type)
        if key_type is str and value_type is LabelProvenance:
            return set()
    return label_fields


# --- positive control (mirrors test_datatype_dispatch.py's own idiom) ------


class _RogueModel(BaseModel):
    """A synthetic violation: a bare label field with no provenance at
    all - proves `label_provenance_gaps` can actually fail."""

    fsn: str


def test_guard_flags_a_known_violation() -> None:
    assert label_provenance_gaps(_RogueModel) == {"fsn"}


class _CompliantSingleFieldModel(BaseModel):
    term: str
    label_provenance: LabelProvenance


class _CompliantMultiFieldModel(BaseModel):
    fsn: str
    au_preferred_term: str | None
    label_provenance: dict[str, LabelProvenance]


def test_guard_passes_a_correctly_shaped_single_field_model() -> None:
    assert label_provenance_gaps(_CompliantSingleFieldModel) == set()


def test_guard_passes_a_correctly_shaped_multi_field_model() -> None:
    assert label_provenance_gaps(_CompliantMultiFieldModel) == set()


def test_guard_flags_a_singular_provenance_covering_two_label_fields() -> None:
    """A single `LabelProvenance` cannot unambiguously describe two
    different fields - declaring *a* `label_provenance` field is not the
    same as declaring the right one."""

    class _AmbiguousShape(BaseModel):
        fsn: str
        au_preferred_term: str | None
        label_provenance: LabelProvenance

    assert label_provenance_gaps(_AmbiguousShape) == {"fsn", "au_preferred_term"}


# --- the real app's schema graph -------------------------------------------


def _real_app_models() -> set[type[BaseModel]]:
    app = create_app()
    models: set[type[BaseModel]] = set()
    for route in _iter_api_routes(app.routes):
        if route.response_model is not None and issubclass(route.response_model, BaseModel):
            models |= _collect_models(route.response_model)
    return models


@pytest.mark.req("FR-98")
def test_every_label_bearing_model_in_the_real_app_declares_provenance() -> None:
    models = _real_app_models()
    assert models, "the walk found no response models at all - the guard would pass vacuously"

    offenders = {
        model.__qualname__: gaps
        for model in models
        if (gaps := label_provenance_gaps(model))
    }
    assert not offenders, (
        f"model(s) with a label field but no matching label_provenance: {offenders}"
    )


@pytest.mark.req("FR-98")
def test_label_field_name_set_is_not_stale() -> None:
    """A guard on the guard, mirroring `test_allowed_references_list_is_
    not_stale`'s own reasoning: `LABEL_FIELD_NAMES` is a fixed frozenset,
    and a new field that merely *looks* label-shaped (matches `term`,
    `fsn` or `display` in its own name) but is neither in that set nor
    covered by its own model's `label_provenance` would otherwise slip
    through `label_provenance_gaps` undetected - that function only ever
    looks at the names already on the list."""
    plausible_label_name = re.compile(r"term|fsn|display", re.IGNORECASE)
    models = _real_app_models()

    #: Reviewed, explicit exemptions - each one checked by hand and found
    #: not to be a SNOMED/catalogue label, the same way
    #: `test_catalogue_bindings.py`'s own `_ALLOWED_REFERENCES` documents
    #: each entry rather than exempting a whole file or a bare substring.
    #: A field not listed here still fails this test, which is the point.
    exempt: dict[str, frozenset[str]] = {
        # A UI sort-order integer (issue #55/#247), never a label of any
        # kind - matches `plausible_label_name` only because `display_order`
        # itself contains the substring "display".
        "PropertyDefinitionResponse": frozenset({"display_order"}),
        # A person's display name (NFR-04/NFR-26: an audit actor's name,
        # never an internal id) - identity data, not a SNOMED/catalogue
        # label.
        "UserRef": frozenset({"display_name"}),
        # A coded property's offerable value (issue #247) - `display` here
        # is a value-set *option's* label, sourced from whichever code
        # system defined it (SNOMED CT or a local code system, the model's
        # own docstring says "identical in shape" either way) - a
        # different vocabulary from this issue's four DesignationType
        # values, which are all about *this catalogue entry's own* label
        # fields. Genuinely label-shaped data FR-98 does not yet have a
        # vocabulary for; flagged for a human decision rather than forced
        # into a DesignationType that would misdescribe it.
        "PropertyValueItem": frozenset({"display"}),
    }

    suspects: dict[str, set[str]] = {}
    for model in models:
        hints = typing.get_type_hints(model)
        provenance_type = hints.get("label_provenance")
        covered_dict = get_origin(provenance_type) is dict
        exempt_fields = exempt.get(model.__qualname__, frozenset())
        for name in hints:
            if name in LABEL_FIELD_NAMES or name == "label_provenance":
                continue
            if name in exempt_fields:
                continue
            if not plausible_label_name.search(name):
                continue
            # A field a dict-shaped label_provenance could plausibly be
            # declaring provenance *for* is not a gap by itself - the
            # per-model key-content tests below are what check that
            # declaration is actually correct. Only a model with **no**
            # label_provenance field at all is an unambiguous miss.
            if provenance_type is None or not covered_dict:
                suspects.setdefault(model.__qualname__, set()).add(name)

    assert not suspects, (
        f"field(s) that look label-shaped but are not in LABEL_FIELD_NAMES and are not "
        f"otherwise covered by a label_provenance field: {suspects}"
    )


@pytest.mark.req("FR-98")
def test_known_models_declare_provenance_for_exactly_their_own_label_fields() -> None:
    """`label_provenance_gaps` can only check *shape* for a `dict`-typed
    field (a class definition carries no runtime dict content) - this
    closes that gap for the four real models FR-98 names, by building one
    of each through its own real assembler and checking the actual keys."""
    import uuid
    from datetime import UTC, datetime

    from nptc.api.labels import (
        AU_PREFERRED_TERM_PROVENANCE,
        DesignationType,
        SemanticTagState,
    )
    from nptc.api.routers.catalogue_shared import (
        Designation,
        EntrySummary,
        binding_from_row,
        designation_from_row,
        entry_summary_fields,
    )
    from nptc.api.routers.terminology import ConceptLookup
    from nptc.catalogue import queries

    binding_row = queries.BindingRow(
        id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
        system="http://snomed.info/sct",
        code="391483001",
        fsn="Microscopy (acid fast bacilli) (procedure)",
        au_preferred_term="Microscopy (acid fast bacilli)",
        edition_hint="au",
        status="active",
        retirement_reason=None,
        replaced_by_code=None,
    )
    binding = binding_from_row(binding_row)
    assert set(binding.label_provenance) == {"fsn", "au_preferred_term"}

    summary = EntrySummary(
        **entry_summary_fields(
            business_key="NPTC-000001",
            preferred_term="Full blood count",
            length=17,
            status="active",
            specimen_unconstrained=False,
            updated_at=datetime.now(UTC),
            has_open_finding=False,
        )
    )
    assert set(summary.label_provenance) == {"preferred_term"}

    designation_row = queries.DesignationRow(
        id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
        term="Full blood count synonym",
        use="synonym",
        language="en-AU",
        status="active",
        length=25,
    )
    designation = designation_from_row(designation_row)
    assert isinstance(designation, Designation)
    assert designation.label_provenance.model_dump() == {
        "designation": "synonym",
        "semantic_tag": "not_applicable",
    }

    concept_lookup = ConceptLookup(
        system="http://snomed.info/sct",
        code="391483001",
        fsn="Microscopy (acid fast bacilli) (procedure)",
        au_preferred_term="Microscopy (acid fast bacilli)",
        active=True,
        edition="au",
        resolved_version=None,
        label_provenance={
            "fsn": {"designation": DesignationType.FSN, "semantic_tag": SemanticTagState.INTACT},
            "au_preferred_term": AU_PREFERRED_TERM_PROVENANCE,
        },
    )
    assert set(concept_lookup.label_provenance) == {"fsn", "au_preferred_term"}


# --- the 391483001 worked regression, over real HTTP -----------------------


@pytest.mark.req("FR-82")
@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_391483001_fsn_is_served_verbatim_with_intact_provenance(app_db: Connection) -> None:
    """PRD SS6.4's named regression case: `391483001`'s FSN carries two
    parenthesised groups, and only the trailing `(procedure)` is the
    semantic tag. FR-82 requires `fsn` served byte-for-byte (no
    re-derivation); FR-98 requires the served payload to say, itself,
    that the tag is intact - both asserted directly against the real
    app rather than against the assembler in isolation.
    """
    import importlib.util
    import sys
    from pathlib import Path

    def _load(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    api_support = _load("api_app_support")
    seed = _load("public_catalogue_support")

    api = next(iter(api_support.build_api_test_app(app_db)))
    seeded = seed.seed_public_catalogue(api.session)

    response = api.get(f"/catalogue/entries/{seeded.canonical}/bindings")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    active = next(item for item in items if item["code"] == seed.ACTIVE_CODE)

    assert active["fsn"] == seed.ACTIVE_FSN
    assert active["fsn"] == "Microscopy (acid fast bacilli) (procedure)"
    assert "display_term" not in active
    assert active["label_provenance"]["fsn"] == {
        "designation": "fsn",
        "semantic_tag": "intact",
    }
