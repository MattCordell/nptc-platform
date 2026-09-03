"""`nptc.catalogue.property_value_sources` service-layer tests (issue #247,
FR-10, FR-52, FR-90).

`nptc.db.bootstrap.seed_system_properties` seeds the real `specimen`
(value-set-bound) and `discipline`/`subgroup` (local-code-system-bound)
definitions through their own real write path - the same precedent
`test_catalogue_property_values.py` follows - so this file exercises the
route's SNOMED and local-code branches against the actual seeded shape
rather than a hand-rolled stand-in. `discipline` additionally carries
migration 0011's own seeded codes (e.g. `chemical_pathology`), exercised
end to end for the "no terminology-server call at all" acceptance
criterion.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.catalogue.local_codes import (
    DatabaseLocalCodeLookup,
    create_local_code,
    deprecate_local_code,
)
from nptc.catalogue.property_value_sources import (
    PropertyNotCodeTypeError,
    PropertyValueSourceMisconfiguredError,
    ValueItem,
    ValuePage,
    list_property_values,
)
from nptc.catalogue.property_values import PropertyDefinitionNotFoundError
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.local_code_system import LocalCodeSystem
from nptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.terminology.errors import TerminologyUnavailableError, TerminologyUpstreamError
from nptc_shared.terminology import (
    SNOMED_CT_AU,
    ExpandedConcept,
    Expansion,
    Operation,
    StubTerminologyClient,
    TerminologyOutcomeError,
    TerminologyTransportError,
)

#: PRD S6.6's own worked example (matches `nptc.db.bootstrap`'s seeded
#: `specimen` binding) - `_SPECIMEN_VALUE_SET_URI` there is itself a
#: literal, not derived, so this test file follows the same precedent
#: rather than importing that private module constant.
_SPECIMEN_ECL = "<123038009"


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _seed(session: Session) -> None:
    seed_system_properties(session)
    session.flush()


def _administrator() -> Principal:
    roles = frozenset({Role.ADMINISTRATOR})
    return Principal(
        user_id=None,
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def _expansion(codes_and_displays: list[tuple[str, str]]) -> Expansion:
    concepts = tuple(
        ExpandedConcept(code=code, system="http://snomed.info/sct", display=display)
        for code, display in codes_and_displays
    )
    return Expansion(concepts=concepts, total=len(concepts), offset=0)


# --- unknown key / non-code property -----------------------------------------


@pytest.mark.req("FR-10")
def test_list_property_values_rejects_an_unknown_key(app_session: Session) -> None:
    with pytest.raises(PropertyDefinitionNotFoundError):
        list_property_values(app_session, StubTerminologyClient(), key="not_a_real_property")


@pytest.mark.req("FR-10")
def test_list_property_values_rejects_a_non_code_property(app_session: Session) -> None:
    definition = PropertyDefinition(
        key="a_free_text_values_test",
        label="A free text values test",
        datatype="string",
        cardinality=PropertyCardinality.ZERO_OR_MANY,
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={},
    )
    app_session.add(definition)
    app_session.flush()

    with pytest.raises(PropertyNotCodeTypeError):
        list_property_values(app_session, StubTerminologyClient(), key="a_free_text_values_test")


# --- specimen (value_set binding) --------------------------------------------


@pytest.mark.req("FR-10")
@pytest.mark.req("FR-52")
def test_list_property_values_serves_specimen_from_the_value_set_binding(
    app_session: Session,
) -> None:
    """One `$expand` call resolves the whole page - never one call per
    code (FR-52), and the caller supplies no ECL or value set URI of its
    own (the acceptance criterion, verbatim)."""
    _seed(app_session)
    client = StubTerminologyClient()
    client.seed_expansion(
        _SPECIMEN_ECL,
        _expansion([("122192001", "Acanthamoeba culture")]),
        edition=SNOMED_CT_AU,
    )

    page = list_property_values(app_session, client, key="specimen")

    assert page == ValuePage(
        items=(ValueItem(code="122192001", display="Acanthamoeba culture"),), total=1
    )
    assert [r.operation for r in client.requests] == [Operation.EXPAND]


@pytest.mark.req("FR-10")
def test_list_property_values_passes_the_caller_filter_through_to_expand(
    app_session: Session,
) -> None:
    _seed(app_session)
    client = StubTerminologyClient()
    client.seed_expansion(
        _SPECIMEN_ECL,
        _expansion([("122192001", "Acanthamoeba culture")]),
        edition=SNOMED_CT_AU,
        filter="acantha",
    )
    client.seed_expansion(
        _SPECIMEN_ECL,
        _expansion([("122192001", "Acanthamoeba culture"), ("71388002", "Procedure")]),
        edition=SNOMED_CT_AU,
    )

    filtered = list_property_values(app_session, client, key="specimen", filter="acantha")
    unfiltered = list_property_values(app_session, client, key="specimen")

    assert [item.code for item in filtered.items] == ["122192001"]
    assert [item.code for item in unfiltered.items] == ["122192001", "71388002"]


@pytest.mark.req("FR-10")
def test_list_property_values_rejects_a_malformed_stored_value_set_uri(
    app_session: Session,
) -> None:
    """Defence in depth (this module's own docstring): every real
    `value_set_uri` is written by `implicit_value_set_url`, so this path is
    not reachable through normal admin writes - it proves the failure is a
    typed 500, not an unhandled crash, if that guarantee is ever broken."""
    definition = PropertyDefinition(
        key="misconfigured_value_set_test",
        label="Misconfigured value set test",
        datatype="code",
        cardinality=PropertyCardinality.ZERO_OR_MANY,
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        binding_target=BindingTarget.VALUE_SET,
        value_set_uri="http://example.org/not-an-implicit-value-set",
        strength=BindingStrength.REQUIRED,
        edition="au",
        constraints={},
    )
    app_session.add(definition)
    app_session.flush()

    with pytest.raises(PropertyValueSourceMisconfiguredError):
        list_property_values(
            app_session, StubTerminologyClient(), key="misconfigured_value_set_test"
        )


@pytest.mark.req("FR-10")
def test_list_property_values_raises_unavailable_when_expand_cannot_be_reached(
    app_session: Session,
) -> None:
    _seed(app_session)
    client = StubTerminologyClient()
    client.seed_error(Operation.EXPAND, TerminologyTransportError("connection refused"))

    with pytest.raises(TerminologyUnavailableError):
        list_property_values(app_session, client, key="specimen")


@pytest.mark.req("FR-10")
def test_list_property_values_raises_upstream_for_an_unclassified_failure(
    app_session: Session,
) -> None:
    """An absence-shaped or otherwise unclassified expand failure is never
    read as "no matches" - see `classify_terminology_error`'s own docstring
    for why this route passes no `not_found` factory at all."""
    _seed(app_session)
    client = StubTerminologyClient()
    client.seed_error(Operation.EXPAND, TerminologyOutcomeError("server refused the request"))

    with pytest.raises(TerminologyUpstreamError):
        list_property_values(app_session, client, key="specimen")


# --- discipline (local_code_system binding) ----------------------------------


@pytest.mark.req("FR-10")
@pytest.mark.req("FR-90")
def test_list_property_values_serves_discipline_with_no_terminology_call(
    app_session: Session,
) -> None:
    """Exercises migration 0011's own seed data end to end - the
    acceptance criterion, verbatim: "the same response shape, with no
    terminology-server call at all"."""
    _seed(app_session)
    client = StubTerminologyClient()

    page = list_property_values(app_session, client, key="discipline")

    assert "chemical_pathology" in {item.code for item in page.items}
    assert client.requests == ()


@pytest.mark.req("FR-10")
def test_list_property_values_excludes_a_deprecated_local_code(app_session: Session) -> None:
    _seed(app_session)
    system = app_session.execute(
        select(LocalCodeSystem).where(LocalCodeSystem.key == "discipline")
    ).scalar_one()
    deprecated = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="deprecated_discipline_values_test",
        display="Deprecated discipline values test",
        reason="test fixture",
    )
    app_session.flush()
    deprecate_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        code=deprecated,
        reason="superseded",
    )
    app_session.flush()

    page = list_property_values(app_session, StubTerminologyClient(), key="discipline")

    codes = {item.code for item in page.items}
    assert "deprecated_discipline_values_test" not in codes

    resolved = DatabaseLocalCodeLookup(app_session).resolve(
        "discipline", "deprecated_discipline_values_test"
    )
    assert resolved is not None
    assert resolved.status == "deprecated"


def test_list_property_values_paginates_local_code_system_results(app_session: Session) -> None:
    _seed(app_session)

    first_page = list_property_values(
        app_session, StubTerminologyClient(), key="discipline", count=1
    )

    assert len(first_page.items) == 1
    assert first_page.total > 1


# --- response shape parity ----------------------------------------------------


def test_response_shape_is_identical_for_both_binding_targets(app_session: Session) -> None:
    """The acceptance criterion, as code: a caller of `list_property_values`
    cannot tell a `value_set` binding from a `local_code_system` binding
    apart from the returned type alone."""
    _seed(app_session)
    client = StubTerminologyClient()
    client.seed_expansion(_SPECIMEN_ECL, _expansion([]), edition=SNOMED_CT_AU)

    specimen_page = list_property_values(app_session, client, key="specimen")
    discipline_page = list_property_values(app_session, client, key="discipline")

    assert type(specimen_page) is ValuePage
    assert type(discipline_page) is ValuePage
    assert all(isinstance(item, ValueItem) for item in discipline_page.items)
