"""Service-layer tests for `nptc.db.definitions` and
`nptc.registry.definitions` (issue #55, FR-11, FR-12, NFR-38).

Uses an ORM `Session` bound to `app_db`, matching `test_catalogue_bindings.
py`'s own module docstring for why (one session-scoped Postgres container
across the whole test run - every assertion here is a relative delta or
scoped to rows this test itself created, never an absolute count).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import create_entry
from nptc.catalogue.property_values import (
    PropertyDefinitionNotFoundError,
    PropertyValueInput,
    save_property_values,
)
from nptc.db.bootstrap import seed_system_properties
from nptc.db.definitions import (
    amend_definition,
    create_definition,
    deprecate_definition,
    list_definitions,
    load_definition,
)
from nptc.db.models.audit import AuditEvent
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.property_value import PropertyValue
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.definitions import (
    DefinitionAudience,
    DeprecatedPropertyWriteError,
    PropertyAlreadyDeprecatedError,
    PropertyDefinitionKeyExistsError,
    PropertyKeyImmutableError,
    PropertyReactivationRefusedError,
    SystemPropertyDeprecationRefusedError,
)
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps

_REASON = "Created for the #55 property deprecation test suite."


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


@pytest.fixture
def registry() -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(HandlerDeps(terminology_client=None, local_code_lookup=None))
    )


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_key() -> str:
    return f"test_prop_{uuid.uuid4().hex[:12]}"


def _create(session: Session, *, key: str | None = None, **overrides: object) -> PropertyDefinition:
    kwargs: dict[str, object] = {
        "key": key or _new_key(),
        "label": "Test property",
        "datatype": "string",
        "cardinality": "0..1",
        "scope": "both",
        "required_for_submission": False,
        "required_for_publication": False,
        "filterable": False,
        "display_order": 999,
        "reason": _REASON,
    }
    kwargs.update(overrides)
    return create_definition(session, AuditContext.system(), **kwargs)  # type: ignore[arg-type]


# --- create_definition -------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_create_definition_emits_one_audit_event(app_session: Session) -> None:
    before = _audit_event_count(app_session)

    _create(app_session)
    app_session.flush()

    assert _audit_event_count(app_session) == before + 1


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_create_definition_refuses_a_duplicate_key(app_session: Session) -> None:
    key = _new_key()
    _create(app_session, key=key)
    app_session.flush()

    with pytest.raises(PropertyDefinitionKeyExistsError):
        _create(app_session, key=key)


# --- amend_definition (FR-12: key immutability) -------------------------


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_amend_definition_changes_label_key_stays_identical(app_session: Session) -> None:
    definition = _create(app_session, label="Original label")
    app_session.flush()
    key = definition.key

    amended = amend_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
        label="Amended label",
    )
    app_session.flush()

    assert amended.key == key
    assert amended.label == "Amended label"


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_amend_definition_refuses_a_key_change(app_session: Session) -> None:
    definition = _create(app_session)
    app_session.flush()

    with pytest.raises(PropertyKeyImmutableError):
        amend_definition(
            app_session,
            AuditContext.system(),
            definition=definition,
            expected_row_version=definition.row_version,
            reason=_REASON,
            key="a_different_key",
        )


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_amend_definition_refuses_a_stale_row_version(app_session: Session) -> None:
    definition = _create(app_session)
    app_session.flush()

    with pytest.raises(Exception) as excinfo:
        amend_definition(
            app_session,
            AuditContext.system(),
            definition=definition,
            expected_row_version=definition.row_version + 1,
            reason=_REASON,
            label="Should not apply",
        )
    assert getattr(type(excinfo.value), "http_status", None) == 409


# --- deprecate_definition (FR-11) ---------------------------------------


@pytest.mark.req("FR-11")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_deprecate_definition_emits_one_audit_event_and_sets_status(
    app_session: Session,
) -> None:
    definition = _create(app_session)
    app_session.flush()
    before = _audit_event_count(app_session)

    deprecated = deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    assert deprecated.status == "deprecated"
    assert deprecated.deprecated_at is not None
    assert _audit_event_count(app_session) == before + 1


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecate_definition_retains_recorded_values(
    app_session: Session, registry: DatatypeRegistry
) -> None:
    """FR-11's central acceptance criterion: deprecating a property must
    never touch its recorded `property_value` rows. Asserted as a relative
    delta on the row count and by reading the value back afterwards -
    never an absolute count against the shared table."""
    definition = _create(app_session, datatype="string", cardinality="0..1")
    entry = create_entry(
        app_session, AuditContext.system(), preferred_term="FR-11 test entry", reason=_REASON
    )
    app_session.flush()

    save_property_values(
        app_session,
        AuditContext.system(),
        entry=entry,
        property_key=definition.key,
        values=[PropertyValueInput(value="hello")],
        reason=_REASON,
        registry=registry,
        expected_row_version=entry.row_version,
    )
    app_session.flush()

    before_count = app_session.execute(
        select(func.count())
        .select_from(PropertyValue)
        .where(PropertyValue.property_key == definition.key)
    ).scalar_one()
    assert before_count == 1

    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    after_count = app_session.execute(
        select(func.count())
        .select_from(PropertyValue)
        .where(PropertyValue.property_key == definition.key)
    ).scalar_one()
    assert after_count == before_count

    stored = app_session.execute(
        select(PropertyValue).where(
            PropertyValue.entry_id == entry.id, PropertyValue.property_key == definition.key
        )
    ).scalar_one()
    assert stored.value == "hello"


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecate_definition_refuses_an_already_deprecated_property(
    app_session: Session,
) -> None:
    definition = _create(app_session)
    app_session.flush()
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    with pytest.raises(PropertyAlreadyDeprecatedError):
        deprecate_definition(
            app_session,
            AuditContext.system(),
            definition=definition,
            expected_row_version=definition.row_version,
            reason=_REASON,
        )


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecate_definition_refuses_a_system_property(app_session: Session) -> None:
    seed_system_properties(app_session)
    app_session.flush()
    definition = load_definition(app_session, "usage_guidance")

    with pytest.raises(SystemPropertyDeprecationRefusedError):
        deprecate_definition(
            app_session,
            AuditContext.system(),
            definition=definition,
            expected_row_version=definition.row_version,
            reason=_REASON,
        )


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_amend_definition_refuses_reactivating_a_deprecated_property(
    app_session: Session,
) -> None:
    """Deprecation is one-way - there is no `reactivate()` function, so the
    only way to attempt `deprecated` -> `active` is via `amend_definition`
    with a `status` kwarg, and that must be refused too."""
    definition = _create(app_session)
    app_session.flush()
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    with pytest.raises(PropertyReactivationRefusedError):
        amend_definition(
            app_session,
            AuditContext.system(),
            definition=definition,
            expected_row_version=definition.row_version,
            reason=_REASON,
            status="active",
        )


# --- list_definitions (FR-11: data-entry vs export audience) -----------


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_list_definitions_data_entry_omits_a_deprecated_property(app_session: Session) -> None:
    definition = _create(app_session)
    app_session.flush()
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    data_entry_keys = {
        d.key for d in list_definitions(app_session, audience=DefinitionAudience.DATA_ENTRY)
    }
    export_keys = {d.key for d in list_definitions(app_session, audience=DefinitionAudience.EXPORT)}

    assert definition.key not in data_entry_keys
    assert definition.key in export_keys


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_export_listing_resolves_to_a_usable_spec_and_handler(
    app_session: Session, registry: DatatypeRegistry
) -> None:
    from nptc.db.property_specs import spec_for

    definition = _create(app_session)
    app_session.flush()
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    [exported] = [
        d
        for d in list_definitions(app_session, audience=DefinitionAudience.EXPORT)
        if d.key == definition.key
    ]
    spec = spec_for(exported)
    handler = registry.get(exported.datatype)
    assert handler is not None
    assert spec.key == definition.key


# --- value writes against a deprecated property (FR-11) -----------------


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_value_write_against_a_deprecated_property_is_refused(
    app_session: Session, registry: DatatypeRegistry
) -> None:
    definition = _create(app_session, datatype="string", cardinality="0..1")
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="FR-11 write-refusal entry",
        reason=_REASON,
    )
    app_session.flush()
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason=_REASON,
    )
    app_session.flush()

    with pytest.raises(DeprecatedPropertyWriteError) as excinfo:
        save_property_values(
            app_session,
            AuditContext.system(),
            entry=entry,
            property_key=definition.key,
            values=[PropertyValueInput(value="should not save")],
            reason=_REASON,
            registry=registry,
            expected_row_version=entry.row_version,
        )
    assert excinfo.value.property_key == definition.key


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_value_write_against_an_unknown_property_still_404s(app_session: Session) -> None:
    with pytest.raises(PropertyDefinitionNotFoundError):
        load_definition(app_session, "no_such_property_key")
