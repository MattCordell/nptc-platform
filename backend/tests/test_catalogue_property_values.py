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
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.entries import allocate_business_key, create_entry, format_business_key
from nptc.catalogue.errors import EntryVersionConflictError
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import (
    BulkPropertyOutcome,
    EntryPropertyTarget,
    PropertyDefinitionNotFoundError,
    PropertyValidationError,
    PropertyValueInput,
    assert_specimen_flag_allowed,
    save_property_values,
    save_property_values_for_entries,
)
from nptc.db.bootstrap import seed_system_properties
from nptc.db.definitions import deprecate_definition
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
from nptc.registry.definitions import DeprecatedPropertyWriteError
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.registry.schema import MalformedConstraintsError
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
        expected_row_version=entry.row_version,
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
def test_save_property_values_flushes_an_unflushed_entry_first(app_session: Session) -> None:
    """A transient `entry` (added to the session, never flushed) has no
    identity yet - `entry.id` is read into every query and insert this
    service issues, so without flushing first the delete/insert would
    either match zero existing rows or try to write `entry_id=NULL`.
    Mirrors `test_create_binding_flushes_an_unflushed_entry_before_
    binding_it`'s identical hazard for the same "create the entry and its
    dependent row in one transaction" call pattern. `_new_entry` (used by
    every other test here) goes through `create_entry`, which always
    flushes internally as part of its own audit write, so this is the one
    test that builds `entry` directly to exercise the transient case."""
    entry = CatalogueEntry(
        business_key=allocate_business_key(app_session), preferred_term="Unflushed entry"
    )
    app_session.add(entry)
    assert sa_inspect(entry).identity is None
    prop = _new_string_property(app_session, key="an_unflushed_entry_prop", cardinality="0..*")

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("x"),
        reason="Writing a property against a not-yet-flushed entry",
        registry=_registry(app_session),
    )

    assert entry.id is not None
    assert rows[0].entry_id == entry.id


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
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("a", "b", "c"),
        reason="First write",
        registry=registry,
    )

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("a"),
        reason="First write",
        registry=registry,
    )

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
        property_key="specimen",
        values=[],
        reason="No specimen values - the entry is unconstrained",
        registry=_registry(app_session),
    )

    assert rows == []


# --- FR-89: the reverse direction (issue #249) ------------------------------


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_assert_specimen_flag_allowed_refuses_when_specimen_values_exist(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)
    terminology = StubTerminologyClient()
    _seed_specimen_stub(terminology, ["specimen-1"])
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key="specimen",
        values=_inputs({"system": _SPECIMEN_SYSTEM, "code": "specimen-1"}),
        reason="Recorded a specimen value before flagging unconstrained",
        registry=_registry(app_session, terminology),
    )

    with pytest.raises(PropertyValidationError) as excinfo:
        assert_specimen_flag_allowed(app_session, entry)

    issue = excinfo.value.issues[0]
    assert issue.code == "specimen-unconstrained-conflict"
    assert issue.property_key == "specimen"
    assert issue.ordinal == 0


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_assert_specimen_flag_allowed_permits_an_entry_with_no_specimen_values(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    _specimen_seeded(app_session)

    assert_specimen_flag_allowed(app_session, entry)  # must not raise


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
        expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
            expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
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
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs({"system": _VS_SYSTEM, "code": _OUT_OF_SET_CODE}),
        reason="example strength is advisory only",
        registry=_registry(app_session, terminology),
    )

    assert len(rows) == 1


# --- FR-38: optimistic concurrency on the entry's row_version ---------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_property_values_rejects_a_stale_expected_row_version(app_session: Session) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_versioned_prop", cardinality="0..*")
    stale_version = entry.row_version

    with pytest.raises(EntryVersionConflictError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            expected_row_version=stale_version - 1,
            property_key=prop.key,
            values=_inputs("x"),
            reason="Should be rejected - stale row_version",
            registry=_registry(app_session),
        )

    assert excinfo.value.report.expected_row_version == stale_version - 1
    assert excinfo.value.report.current_row_version == stale_version
    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_property_values_bumps_row_version_so_a_second_stale_writer_is_rejected(
    app_session: Session,
) -> None:
    """Two editors load the same entry, each save a change to the same
    property; the second must see a version conflict rather than silently
    clobbering the first (FR-38's "no silent last-write-wins")."""
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_versioned_prop_2", cardinality="0..*")
    registry = _registry(app_session)
    editor_a_version = entry.row_version
    editor_b_version = entry.row_version

    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=editor_a_version,
        property_key=prop.key,
        values=_inputs("editor-a-value"),
        reason="Editor A saves first",
        registry=registry,
    )

    with pytest.raises(EntryVersionConflictError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            expected_row_version=editor_b_version,
            property_key=prop.key,
            values=_inputs("editor-b-value"),
            reason="Editor B saves against a now-stale version",
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
    assert [row.value for row in rows] == ["editor-a-value"]


# --- a no-op write leaves row_version and the audit trail untouched ---------


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_resubmitting_the_same_values_is_a_no_op(app_session: Session) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_free_text_no_op", cardinality="0..*")
    registry = _registry(app_session)
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("same", "values"),
        reason="First write",
        registry=registry,
    )
    row_version_after_first_write = entry.row_version
    audit_count_after_first_write = _audit_event_count(app_session)

    rows = save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("same", "values"),
        reason="Resubmitting the same values",
        registry=registry,
    )

    assert [row.value for row in rows] == ["same", "values"]
    assert entry.row_version == row_version_after_first_write
    assert _audit_event_count(app_session) == audit_count_after_first_write


# --- a malformed constraints document fails closed, not open ----------------


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_a_malformed_constraints_document_is_rejected_before_any_value_is_judged(
    app_session: Session,
) -> None:
    """`forbidden_codes` stored as a bare string (not a list) must not
    silently fail open - `validate_constraints` catches the shape defect
    in the *definition* before `CodeHandler.validate` ever sees a value."""
    entry = _new_entry(app_session)
    definition = PropertyDefinition(
        key="a_malformed_constraints_prop",
        label="A Malformed Constraints Prop",
        datatype="code",
        cardinality="0..1",
        scope=PropertyScope.MAINTENANCE,
        required_for_submission=False,
        required_for_publication=False,
        binding_target="value_set",
        value_set_uri=_VS_URI,
        strength="required",
        edition="test",
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={"forbidden_codes": "Any"},
    )
    app_session.add(definition)
    app_session.flush()
    terminology = StubTerminologyClient()
    terminology.seed_validate_code(
        "Any",
        ValidationResult(code="Any", result=True),
        value_set_url=_VS_URI,
        edition=_VS_EDITION,
    )

    with pytest.raises(MalformedConstraintsError):
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            expected_row_version=entry.row_version,
            property_key=definition.key,
            values=_inputs({"system": _VS_SYSTEM, "code": "Any"}),
            reason="Should be rejected - the definition itself is malformed",
            registry=_registry(app_session, terminology),
        )

    assert _property_value_count(app_session, entry_id=entry.id, property_key=definition.key) == 0


# --- issue #265: save_property_values_for_entries (FR-39's bulk seam) -------


def _outcomes_by_key(outcomes: tuple[BulkPropertyOutcome, ...]) -> dict[str, BulkPropertyOutcome]:
    return {outcome.business_key: outcome for outcome in outcomes}


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_applies_across_multiple_entries_in_targets_order(app_session: Session) -> None:
    entry_a = _new_entry(app_session, "Bulk entry A")
    entry_b = _new_entry(app_session, "Bulk entry B")
    prop = _new_string_property(app_session, key="a_bulk_free_text", cardinality="0..*")

    outcomes = save_property_values_for_entries(
        app_session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(
                business_key=entry_a.business_key, expected_row_version=entry_a.row_version
            ),
            EntryPropertyTarget(
                business_key=entry_b.business_key, expected_row_version=entry_b.row_version
            ),
        ],
        property_key=prop.key,
        values=_inputs("bulk-value"),
        reason="Bulk write across two entries",
        registry=_registry(app_session),
    )

    assert [outcome.business_key for outcome in outcomes] == [
        entry_a.business_key,
        entry_b.business_key,
    ]
    assert [outcome.status for outcome in outcomes] == ["applied", "applied"]
    assert _property_value_count(app_session, entry_id=entry_a.id, property_key=prop.key) == 1
    assert _property_value_count(app_session, entry_id=entry_b.id, property_key=prop.key) == 1


@pytest.mark.req("FR-39")
@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_bulk_reports_a_conflict_for_a_stale_entry_and_still_applies_the_rest(
    app_session: Session,
) -> None:
    entry_stale = _new_entry(app_session, "Bulk entry stale")
    entry_fresh = _new_entry(app_session, "Bulk entry fresh")
    prop = _new_string_property(app_session, key="a_bulk_conflict_prop", cardinality="0..*")
    registry = _registry(app_session)
    stale_version = entry_stale.row_version
    # Advances entry_stale's row_version through an unrelated write, so the
    # version the bulk request below carries for it is now behind.
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry_stale,
        expected_row_version=entry_stale.row_version,
        property_key=prop.key,
        values=_inputs("already-set"),
        reason="Advance the version before the bulk write",
        registry=registry,
    )

    outcomes = save_property_values_for_entries(
        app_session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(
                business_key=entry_stale.business_key, expected_row_version=stale_version
            ),
            EntryPropertyTarget(
                business_key=entry_fresh.business_key, expected_row_version=entry_fresh.row_version
            ),
        ],
        property_key=prop.key,
        values=_inputs("bulk-value"),
        reason="Bulk write with one stale entry",
        registry=registry,
    )

    by_key = _outcomes_by_key(outcomes)
    stale_outcome = by_key[entry_stale.business_key]
    assert stale_outcome.status == "conflict"
    assert stale_outcome.conflict is not None
    assert stale_outcome.conflict.current_row_version == entry_stale.row_version
    assert stale_outcome.row_version == entry_stale.row_version
    assert by_key[entry_fresh.business_key].status == "applied"
    rows = (
        app_session.execute(
            select(PropertyValue).where(
                PropertyValue.entry_id == entry_stale.id, PropertyValue.property_key == prop.key
            )
        )
        .scalars()
        .all()
    )
    assert [row.value for row in rows] == ["already-set"]


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_reports_not_found_for_a_missing_business_key(app_session: Session) -> None:
    entry = _new_entry(app_session, "Bulk entry present")
    prop = _new_string_property(app_session, key="a_bulk_not_found_prop", cardinality="0..*")
    missing_key = format_business_key(999_265_001)

    outcomes = save_property_values_for_entries(
        app_session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(business_key=missing_key, expected_row_version=1),
            EntryPropertyTarget(
                business_key=entry.business_key, expected_row_version=entry.row_version
            ),
        ],
        property_key=prop.key,
        values=_inputs("x"),
        reason="One missing business key, one present",
        registry=_registry(app_session),
    )

    assert outcomes[0].status == "not-found"
    assert outcomes[0].row_version is None
    assert outcomes[0].conflict is None
    assert outcomes[1].status == "applied"


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_bulk_row_version_increments_by_exactly_one_for_an_applied_entry(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_bulk_version_bump_prop", cardinality="0..*")
    before_version = entry.row_version

    outcomes = save_property_values_for_entries(
        app_session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(
                business_key=entry.business_key, expected_row_version=before_version
            )
        ],
        property_key=prop.key,
        values=_inputs("bumped"),
        reason="Row version bump check",
        registry=_registry(app_session),
    )

    assert outcomes[0].row_version == before_version + 1
    assert entry.row_version == before_version + 1


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_bulk_rejects_a_blank_reason_before_touching_any_entry(app_session: Session) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_bulk_blank_reason_prop", cardinality="0..*")
    before_audit_count = _audit_event_count(app_session)

    with pytest.raises(ChangelogNoteError):
        save_property_values_for_entries(
            app_session,
            AuditContext.system(),
            targets=[
                EntryPropertyTarget(
                    business_key=entry.business_key, expected_row_version=entry.row_version
                )
            ],
            property_key=prop.key,
            values=_inputs("x"),
            reason="",
            registry=_registry(app_session),
        )

    assert _property_value_count(app_session, entry_id=entry.id, property_key=prop.key) == 0
    assert _audit_event_count(app_session) == before_audit_count


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_validates_the_shared_values_set_even_when_the_only_target_would_conflict(
    app_session: Session,
) -> None:
    """Order-independence (FR-37/FR-39): the shared `values` set is derived
    from the request document alone, so it is validated before the loop
    reaches any entry - a batch must not return 200-with-conflicts having
    never looked at an invalid `values` set just because every target it
    named happens to be stale."""
    entry = _new_entry(app_session)
    prop = _new_string_property(
        app_session, key="a_bulk_order_independence_prop", cardinality="0..*", max_length=3
    )
    guaranteed_mismatch = entry.row_version + 999

    with pytest.raises(PropertyValidationError):
        save_property_values_for_entries(
            app_session,
            AuditContext.system(),
            targets=[
                EntryPropertyTarget(
                    business_key=entry.business_key, expected_row_version=guaranteed_mismatch
                )
            ],
            property_key=prop.key,
            values=_inputs("way too long"),
            reason="Values invalid even though the only target would conflict",
            registry=_registry(app_session),
        )


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_an_invalid_shared_values_set_writes_nothing_for_any_entry(
    app_session: Session,
) -> None:
    entry_a = _new_entry(app_session, "Bulk invalid values A")
    entry_b = _new_entry(app_session, "Bulk invalid values B")
    prop = _new_string_property(
        app_session, key="a_bulk_short_text_prop", cardinality="0..*", max_length=3
    )

    with pytest.raises(PropertyValidationError):
        save_property_values_for_entries(
            app_session,
            AuditContext.system(),
            targets=[
                EntryPropertyTarget(
                    business_key=entry_a.business_key, expected_row_version=entry_a.row_version
                ),
                EntryPropertyTarget(
                    business_key=entry_b.business_key, expected_row_version=entry_b.row_version
                ),
            ],
            property_key=prop.key,
            values=_inputs("way too long"),
            reason="Should be rejected for the whole batch",
            registry=_registry(app_session),
        )

    assert _property_value_count(app_session, entry_id=entry_a.id, property_key=prop.key) == 0
    assert _property_value_count(app_session, entry_id=entry_b.id, property_key=prop.key) == 0


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_an_entry_already_holding_the_target_value_is_unchanged_not_an_error(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    prop = _new_string_property(app_session, key="a_bulk_noop_prop", cardinality="0..*")
    registry = _registry(app_session)
    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key=prop.key,
        values=_inputs("already-set"),
        reason="Pre-set the value outside the bulk write",
        registry=registry,
    )
    version_after_presave = entry.row_version

    outcomes = save_property_values_for_entries(
        app_session,
        AuditContext.system(),
        targets=[
            EntryPropertyTarget(
                business_key=entry.business_key, expected_row_version=version_after_presave
            )
        ],
        property_key=prop.key,
        values=_inputs("already-set"),
        reason="Resubmitting the same value through the bulk route",
        registry=registry,
    )

    assert outcomes[0].status == "unchanged"
    assert outcomes[0].row_version == version_after_presave
    assert entry.row_version == version_after_presave


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_bulk_a_specimen_cross_field_conflict_aborts_the_whole_batch(
    app_session: Session,
) -> None:
    """FR-89's specimen cross-field check is deliberately whole-request, not
    per-entry (ADR-0035): the operator explicitly selected this entry, so a
    violation refuses the whole batch rather than silently skipping it. The
    conflicting entry is targeted first, so a valid entry later in the list
    is never reached."""
    entry_unconstrained = _new_entry(app_session, "Bulk specimen unconstrained")
    entry_unconstrained.specimen_unconstrained = True
    app_session.flush()
    entry_ok = _new_entry(app_session, "Bulk specimen ok")
    _specimen_seeded(app_session)
    terminology = StubTerminologyClient()
    _seed_specimen_stub(terminology, ["specimen-1"])

    with pytest.raises(PropertyValidationError) as excinfo:
        save_property_values_for_entries(
            app_session,
            AuditContext.system(),
            targets=[
                EntryPropertyTarget(
                    business_key=entry_unconstrained.business_key,
                    expected_row_version=entry_unconstrained.row_version,
                ),
                EntryPropertyTarget(
                    business_key=entry_ok.business_key, expected_row_version=entry_ok.row_version
                ),
            ],
            property_key="specimen",
            values=_inputs({"system": _SPECIMEN_SYSTEM, "code": "specimen-1"}),
            reason="Should abort the whole batch",
            registry=_registry(app_session, terminology),
        )

    assert any(issue.code == "specimen-unconstrained-conflict" for issue in excinfo.value.issues)
    assert _property_value_count(app_session, entry_id=entry_ok.id, property_key="specimen") == 0


@pytest.mark.req("FR-39")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_bulk_emits_one_batch_event_plus_one_per_entry_event_sharing_correlation_id(
    app_session: Session,
) -> None:
    entry_a = _new_entry(app_session, "Bulk audit A")
    entry_b = _new_entry(app_session, "Bulk audit B")
    prop = _new_string_property(app_session, key="a_bulk_audit_prop", cardinality="0..*")
    ctx = AuditContext.system()

    save_property_values_for_entries(
        app_session,
        ctx,
        targets=[
            EntryPropertyTarget(
                business_key=entry_a.business_key, expected_row_version=entry_a.row_version
            ),
            EntryPropertyTarget(
                business_key=entry_b.business_key, expected_row_version=entry_b.row_version
            ),
        ],
        property_key=prop.key,
        values=_inputs("bulk-audit-value"),
        reason="Bulk write for audit correlation test",
        registry=_registry(app_session),
    )

    events = (
        app_session.execute(
            select(AuditEvent)
            .where(AuditEvent.correlation_id == ctx.correlation_id)
            .order_by(AuditEvent.sequence)
        )
        .scalars()
        .all()
    )
    actions = [event.action for event in events]
    assert actions.count("property_value.set") == 2
    assert actions.count("property_value.bulk_set") == 1
    bulk_event = next(event for event in events if event.action == "property_value.bulk_set")
    assert bulk_event.entity_type == "property_value_bulk"
    assert bulk_event.entity_id == prop.key
    assert bulk_event.before is None
    # Tallies are carried structurally (issue #265 review), not appended to
    # `reason` - the operator's own changelog note is left untouched.
    assert bulk_event.after == {"applied": 2, "unchanged": 0, "conflict": 0, "not-found": 0}
    assert bulk_event.reason == "Bulk write for audit correlation test"


@pytest.mark.req("FR-39")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_bulk_no_batch_header_when_nothing_applied(app_session: Session) -> None:
    """A batch where every target is `conflict`/`not-found` changed nothing,
    so it emits no `property_value.bulk_set` header either (issue #265
    review) - matching ADR-0018's "a no-op write emits no audit event"
    posture, and keeping a client retrying a stale selection from appending
    one permanent audit row per attempt."""
    prop = _new_string_property(app_session, key="a_bulk_no_header_prop", cardinality="0..*")
    ctx = AuditContext.system()
    before = _audit_event_count(app_session)

    outcomes = save_property_values_for_entries(
        app_session,
        ctx,
        targets=[EntryPropertyTarget(business_key="NPTC-999997", expected_row_version=1)],
        property_key=prop.key,
        values=_inputs("bulk-no-header-value"),
        reason="Bulk write that touches nothing",
        registry=_registry(app_session),
    )

    assert outcomes[0].status == "not-found"
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-11")
@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_against_a_deprecated_property_aborts_before_touching_any_entry(
    app_session: Session,
) -> None:
    """FR-11's whole-request abort, proven at the bulk seam's own entry
    point (issue #265 review): `test_save_property_values_against_a_
    deprecated_property_is_422_untouched` already covers the singular
    route's identical check via the shared `_load_active_property_
    definition` helper, but per CONTRIBUTING's "principal failure mode"
    rule that transitive coverage never proved the *batch*-abort half - that
    a multi-target request refuses before any of its several entries is
    touched, not just before the one entry a singular write names."""
    prop = _new_string_property(app_session, key="a_bulk_deprecated_prop", cardinality="0..*")
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=prop,
        expected_row_version=prop.row_version,
        reason="Deprecated for issue #265 bulk test",
    )
    app_session.flush()
    entry_a = _new_entry(app_session, "Bulk deprecated A")
    entry_b = _new_entry(app_session, "Bulk deprecated B")
    before = _audit_event_count(app_session)

    with pytest.raises(DeprecatedPropertyWriteError):
        save_property_values_for_entries(
            app_session,
            AuditContext.system(),
            targets=[
                EntryPropertyTarget(
                    business_key=entry_a.business_key, expected_row_version=entry_a.row_version
                ),
                EntryPropertyTarget(
                    business_key=entry_b.business_key, expected_row_version=entry_b.row_version
                ),
            ],
            property_key=prop.key,
            values=_inputs("should-be-refused"),
            reason="Bulk write against a deprecated property",
            registry=_registry(app_session),
        )

    assert _property_value_count(app_session, entry_id=entry_a.id, property_key=prop.key) == 0
    assert _property_value_count(app_session, entry_id=entry_b.id, property_key=prop.key) == 0
    assert _audit_event_count(app_session) == before
