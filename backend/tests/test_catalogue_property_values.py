"""`nptc.catalogue.property_values` service-layer tests (issue #52, FR-09,
FR-10, FR-88, FR-89).

Uses an ORM `Session` bound to `app_db`, matching `test_catalogue_bindings.
py`'s own precedent. `nptc.db.bootstrap.seed_system_properties` seeds the
real Discipline/Subgroup/Specimen/Usage guidance definitions through their
own real write path, so FR-88/FR-89 are exercised against the actual
seeded shape rather than a hand-rolled stand-in.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.entries import create_entry
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import (
    PropertyDefinitionNotFoundError,
    PropertyValidationError,
    PropertyValueInput,
    save_property_values,
)
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_definition import (
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.db.models.property_value import PropertyValue
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc_shared.terminology.models import Edition, ValidationResult
from nptc_shared.terminology.stub import StubTerminologyClient

_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
_SPECIMEN_EDITION = Edition(module_id="au", label="au")
_SPECIMEN_SYSTEM = "http://example.org/specimen-test"


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _inputs(*values: object) -> list[PropertyValueInput]:
    """Wraps bare values with no justification - the common case in these
    tests. FR-10's extensible/justification tests build `PropertyValueInput`
    directly instead."""
    return [PropertyValueInput(value=value) for value in values]


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_entry(session: Session, preferred_term: str = "Full blood count") -> CatalogueEntry:
    return create_entry(
        session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for issue #52 property_values test",
    )


def _registry(
    session: Session, terminology: StubTerminologyClient | None = None
) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=terminology or StubTerminologyClient(),
                local_code_lookup=DatabaseLocalCodeLookup(session),
            )
        )
    )


def _new_string_property(
    session: Session, *, key: str, cardinality: str, max_length: int | None = None
) -> PropertyDefinition:
    definition = PropertyDefinition(
        key=key,
        label=key.replace("_", " ").title(),
        datatype="string",
        cardinality=cardinality,
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={"maxLength": max_length} if max_length is not None else {},
    )
    session.add(definition)
    session.flush()
    return definition


def _specimen_seeded(session: Session) -> None:
    seed_system_properties(session)
    session.flush()


def _seed_specimen_stub(terminology: StubTerminologyClient, codes: list[str]) -> None:
    for code in codes:
        terminology.seed_validate_code(
            code,
            ValidationResult(code=code, result=True),
            value_set_url=_SPECIMEN_VALUE_SET_URI,
            edition=_SPECIMEN_EDITION,
        )


def _property_value_count(session: Session, *, entry_id: uuid.UUID, property_key: str) -> int:
    return session.execute(
        select(func.count())
        .select_from(PropertyValue)
        .where(PropertyValue.entry_id == entry_id, PropertyValue.property_key == property_key)
    ).scalar_one()


# --- basic write path --------------------------------------------------------


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_inserts_rows_and_emits_one_audit_event(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_free_text", cardinality="0..*")
    before = _audit_event_count(app_session)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs("first", "second"),
        reason="Recording free text for FR-09 test",
        registry=_registry(app_session),
    )

    assert [row.value for row in rows] == ["first", "second"]
    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "property_value.set"


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_replaces_the_whole_set(app_session: Session) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_free_text_2", cardinality="0..*")
    registry = _registry(app_session)
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs("a", "b", "c"),
        reason="First write",
        registry=registry,
    )

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs("only-one"),
        reason="Replacement write",
        registry=registry,
    )

    assert [row.value for row in rows] == ["only-one"]
    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 1


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_with_an_empty_list_deletes_existing_rows(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_free_text_3", cardinality="0..*")
    registry = _registry(app_session)
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs("a"),
        reason="First write",
        registry=registry,
    )

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=[],
        reason="Clearing the property",
        registry=registry,
    )

    assert rows == []
    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_rejects_an_unknown_property_key(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(PropertyDefinitionNotFoundError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key="does_not_exist",
            values=_inputs("x"),
            reason="Should never be recorded",
            registry=_registry(app_session),
        )


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_save_property_values_rejects_a_blank_reason_before_touching_any_row(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_free_text_4", cardinality="0..*")

    with pytest.raises(ChangelogNoteError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=_inputs("x"),
            reason="",
            registry=_registry(app_session),
        )

    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0


# --- validation leaves no partial state --------------------------------------


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_a_schema_violation_leaves_no_row_and_raises_a_field_level_error(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_short_text", cardinality="0..*", max_length=3)

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=_inputs("way too long"),
            reason="Should be rejected",
            registry=_registry(app_session),
        )

    assert excinfo.value.issues[0].property_key == prop.key
    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_a_cardinality_violation_leaves_no_row(app_session: Session) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(
        app_session, key="a_single_value", cardinality=PropertyCardinality.ZERO_OR_ONE
    )

    with pytest.raises(PropertyValidationError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=_inputs("one", "two"),
            reason="Should be rejected",
            registry=_registry(app_session),
        )

    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_an_invalid_write_does_not_leave_the_prior_valid_rows_disturbed(
    app_session: Session,
) -> None:
    """ "Leaves no partial state" also covers a *rejected replacement* -
    the property's existing rows must survive untouched, not be deleted
    before the new set is found invalid."""
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_short_text_2", cardinality="0..*", max_length=3)
    registry = _registry(app_session)
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs("ok"),
        reason="Valid first write",
        registry=registry,
    )

    with pytest.raises(PropertyValidationError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=_inputs("way too long"),
            reason="Should be rejected",
            registry=registry,
        )

    rows = (
        app_session.execute(
            select(PropertyValue).where(
                PropertyValue.entry_id == entry.id, PropertyValue.property_key == prop.key
            )
        )
        .scalars()
        .all()
    )
    assert [row.value for row in rows] == ["ok"]


# --- FR-88 / FR-89: Specimen --------------------------------------------------


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_specimen_accepts_the_samples_seven_specimen_case(app_session: Session) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)
    codes = [f"specimen-{n}" for n in range(7)]
    terminology = StubTerminologyClient()
    _seed_specimen_stub(terminology, codes)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key="specimen",
        values=[
            PropertyValueInput(value={"system": _SPECIMEN_SYSTEM, "code": code}) for code in codes
        ],
        reason="Seven specimens, matching the sample's worst case",
        registry=_registry(app_session, terminology),
    )

    assert len(rows) == 7


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_specimen_rejects_the_literal_value_any(app_session: Session) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key="specimen",
            values=_inputs({"system": _SPECIMEN_SYSTEM, "code": "Any"}),
            reason="Should be rejected",
            registry=_registry(app_session),
        )

    assert excinfo.value.issues[0].code == "forbidden-code"
    assert _property_value_count(app_session, entry_id=entry.id, property_key="specimen") == 0


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_specimen_value_is_rejected_when_the_entry_is_marked_unconstrained(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    entry.specimen_unconstrained = True
    app_session.flush()
    _specimen_seeded(app_session)
    terminology = StubTerminologyClient()
    _seed_specimen_stub(terminology, ["specimen-1"])

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key="specimen",
            values=_inputs({"system": _SPECIMEN_SYSTEM, "code": "specimen-1"}),
            reason="Should be rejected - entry is specimen_unconstrained",
            registry=_registry(app_session, terminology),
        )

    assert any(issue.code == "specimen-unconstrained-conflict" for issue in excinfo.value.issues)


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_specimen_unconstrained_entry_accepts_zero_specimen_values(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    entry.specimen_unconstrained = True
    app_session.flush()
    _specimen_seeded(app_session)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key="specimen",
        values=[],
        reason="No specimen values - the entry is unconstrained",
        registry=_registry(app_session),
    )

    assert rows == []


# --- FR-10: local code system binding, no terminology call ------------------


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_discipline_resolves_against_local_code_with_no_terminology_call(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)
    terminology = StubTerminologyClient()

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key="discipline",
        # Seeded by migration 0011 as a real member of the `discipline`
        # local code system.
        values=_inputs(
            {"system": "http://example.org/local/discipline", "code": "chemical_pathology"}
        ),
        reason="Discipline resolved against LocalCode, not Ontoserver",
        registry=_registry(app_session, terminology),
    )

    assert len(rows) == 1
    assert terminology.requests == ()


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_discipline_rejects_a_code_absent_from_the_local_system(app_session: Session) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key="discipline",
            values=_inputs({"system": "http://example.org/local/discipline", "code": "not-a-code"}),
            reason="Should be rejected",
            registry=_registry(app_session),
        )

    assert excinfo.value.issues[0].code == "not-a-local-code"


# --- FR-10: binding strength (required / extensible / example) --------------

_VS_URI = "http://example.org/vs"
_VS_EDITION = Edition(module_id="test", label="test")
_VS_SYSTEM = "http://example.org/coded-test"
_OUT_OF_SET_CODE = "out-of-set"


def _new_coded_property(session: Session, *, key: str, strength: str) -> PropertyDefinition:
    definition = PropertyDefinition(
        key=key,
        label=key.replace("_", " ").title(),
        datatype="code",
        cardinality="0..1",
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        binding_target="value_set",
        value_set_uri=_VS_URI,
        strength=strength,
        edition="test",
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
    )
    session.add(definition)
    session.flush()
    return definition


def _stub_rejecting(code: str) -> StubTerminologyClient:
    terminology = StubTerminologyClient()
    terminology.seed_validate_code(
        code,
        ValidationResult(code=code, result=False, message="not in value set"),
        value_set_url=_VS_URI,
        edition=_VS_EDITION,
    )
    return terminology


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_required_strength_rejects_an_out_of_value_set_code_even_with_a_justification(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_coded_property(app_session, key="a_required_code", strength="required")
    terminology = _stub_rejecting(_OUT_OF_SET_CODE)

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=[
                PropertyValueInput(
                    value={"system": _VS_SYSTEM, "code": _OUT_OF_SET_CODE},
                    justification="I have a good reason",
                )
            ],
            reason="Should be rejected regardless of the justification",
            registry=_registry(app_session, terminology),
        )

    assert excinfo.value.issues[0].code == "not-in-value-set"


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_extensible_strength_rejects_an_out_of_value_set_code_with_no_justification(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_coded_property(app_session, key="an_extensible_code", strength="extensible")
    terminology = _stub_rejecting(_OUT_OF_SET_CODE)

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=prop.key,
            values=_inputs({"system": _VS_SYSTEM, "code": _OUT_OF_SET_CODE}),
            reason="Should be rejected - no justification supplied",
            registry=_registry(app_session, terminology),
        )

    assert excinfo.value.issues[0].code == "justification-required"


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_extensible_strength_accepts_an_out_of_value_set_code_with_a_justification(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_coded_property(app_session, key="an_extensible_code_2", strength="extensible")
    terminology = _stub_rejecting(_OUT_OF_SET_CODE)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=[
            PropertyValueInput(
                value={"system": _VS_SYSTEM, "code": _OUT_OF_SET_CODE},
                justification="Chosen deliberately outside the governed set, per the requester",
            )
        ],
        reason="Accepted with a recorded justification",
        registry=_registry(app_session, terminology),
    )

    assert len(rows) == 1
    assert rows[0].justification == (
        "Chosen deliberately outside the governed set, per the requester"
    )


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_example_strength_accepts_an_out_of_value_set_code_with_no_justification(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_coded_property(app_session, key="an_example_code", strength="example")
    terminology = _stub_rejecting(_OUT_OF_SET_CODE)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=prop.key,
        values=_inputs({"system": _VS_SYSTEM, "code": _OUT_OF_SET_CODE}),
        reason="example strength is advisory only",
        registry=_registry(app_session, terminology),
    )

    assert len(rows) == 1
