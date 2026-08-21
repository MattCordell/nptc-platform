"""`nptc.registry.local_codes` service-layer tests (issue #56, FR-90,
FR-91, FR-92), plus the AST guard for this issue's acceptance criterion:
the advisory SNOMED map must never be treated as a code binding by the
validation sweep.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.db.models.audit import AuditEvent
from nptc.db.models.local_code import LocalCodeStatus
from nptc.db.models.local_code_snomed_map import LocalCodeSnomedMap
from nptc.db.models.local_code_system import LocalCodeSystemStatus
from nptc.registry.local_codes import (
    InvalidLocalCodeSystemKeyError,
    InvalidMatchStrengthError,
    LocalCodeAlreadyDeprecatedError,
    LocalCodeSystemAlreadyDeprecatedError,
    create_local_code,
    create_local_code_system,
    create_snomed_map_row,
    deprecate_local_code,
    deprecate_local_code_system,
    find_local_code,
    find_local_code_with_system_status,
)
from nptc.registry.lookup import DatabaseLocalCodeLookup
from nptc_shared.sctid import InvalidSCTIDError

REPO_ROOT = Path(__file__).resolve().parents[2]

_VALID_SNOMED_CODE = "394596001"


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _principal(*, roles: frozenset[Role]) -> Principal:
    return Principal(
        user_id=None,
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def _administrator() -> Principal:
    return _principal(roles=frozenset({Role.ADMINISTRATOR}))


def _observer() -> Principal:
    return _principal(roles=frozenset({Role.OBSERVER}))


# --- create_local_code_system / deprecate_local_code_system -----------------


def test_create_local_code_system_requires_registry_manage(app_session: Session) -> None:
    with pytest.raises(PermissionDeniedError):
        create_local_code_system(
            app_session,
            AuditContext.system(),
            actor=_observer(),
            key="discipline_denied",
            uri="https://nptc.example.org/CodeSystem/discipline_denied",
            title="Discipline",
            description="test",
            owner="RCPA-QAP",
            reason="test-only fixture",
        )


def test_create_local_code_system_rejects_a_malformed_key(app_session: Session) -> None:
    """`ck_local_code_system_key` is the actual database invariant; this
    pins the fail-loud Python-level layer that pre-empts it - a malformed
    key never reaches the session, let alone flush."""
    before = _audit_event_count(app_session)

    with pytest.raises(InvalidLocalCodeSystemKeyError):
        create_local_code_system(
            app_session,
            AuditContext.system(),
            actor=_administrator(),
            key="Not A Valid Key",
            uri="https://nptc.example.org/CodeSystem/invalid_key_test",
            title="Discipline",
            description="test",
            owner="RCPA-QAP",
            reason="test-only fixture",
        )

    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-90")
def test_create_local_code_system_records_one_audit_event(app_session: Session) -> None:
    before = _audit_event_count(app_session)

    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="discipline_svc_test",
        uri="https://nptc.example.org/CodeSystem/discipline_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()

    assert system.status == str(LocalCodeSystemStatus.ACTIVE)
    assert _audit_event_count(app_session) == before + 1


@pytest.mark.req("FR-90")
def test_deprecate_local_code_system_is_a_status_transition(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="deprecate_svc_test",
        uri="https://nptc.example.org/CodeSystem/deprecate_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    deprecate_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        reason="deprecating this system",
    )

    assert system.status == str(LocalCodeSystemStatus.DEPRECATED)
    assert _audit_event_count(app_session) == before + 1


def test_deprecating_an_already_deprecated_system_is_refused(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="double_deprecate_test",
        uri="https://nptc.example.org/CodeSystem/double_deprecate_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    deprecate_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        reason="deprecating this system",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(LocalCodeSystemAlreadyDeprecatedError):
        deprecate_local_code_system(
            app_session,
            AuditContext.system(),
            actor=_administrator(),
            system=system,
            reason="retired again",
        )

    assert _audit_event_count(app_session) == before


def test_deprecate_local_code_system_requires_registry_manage(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="deprecate_denied_test",
        uri="https://nptc.example.org/CodeSystem/deprecate_denied_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()

    with pytest.raises(PermissionDeniedError):
        deprecate_local_code_system(
            app_session,
            AuditContext.system(),
            actor=_observer(),
            system=system,
            reason="deprecating this system",
        )


# --- create_local_code / deprecate_local_code --------------------------------


@pytest.mark.req("FR-92")
def test_create_local_code_supports_provisional(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="subgroup_svc_test",
        uri="https://nptc.example.org/CodeSystem/subgroup_svc_test",
        title="Subgroup",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="coagulation",
        display="Coagulation",
        provisional=True,
        reason="migrating verbatim, RCPA-QAP has not reconciled the axis yet",
    )
    app_session.flush()

    assert code.provisional is True
    assert code.definition is None
    assert _audit_event_count(app_session) == before + 1


def test_create_local_code_requires_registry_manage(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="code_denied_test",
        uri="https://nptc.example.org/CodeSystem/code_denied_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()

    with pytest.raises(PermissionDeniedError):
        create_local_code(
            app_session,
            AuditContext.system(),
            actor=_observer(),
            system=system,
            code="denied",
            display="Denied",
            reason="test-only fixture",
        )


@pytest.mark.req("FR-90")
def test_deprecate_local_code_is_a_status_transition(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="code_deprecate_test",
        uri="https://nptc.example.org/CodeSystem/code_deprecate_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="deprecate_me",
        display="Deprecate me",
        reason="creating a test fixture code",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    deprecate_local_code(
        app_session, AuditContext.system(), actor=_administrator(), code=code, reason="superseded"
    )

    assert code.status == str(LocalCodeStatus.DEPRECATED)
    assert code.deprecated_at is not None
    assert code.deprecation_reason == "superseded"
    assert _audit_event_count(app_session) == before + 1


def test_deprecating_an_already_deprecated_code_is_refused(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="code_double_deprecate_test",
        uri="https://nptc.example.org/CodeSystem/code_double_deprecate_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="deprecate_twice",
        display="Deprecate twice",
        reason="creating a test fixture code",
    )
    app_session.flush()
    deprecate_local_code(
        app_session, AuditContext.system(), actor=_administrator(), code=code, reason="superseded"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(LocalCodeAlreadyDeprecatedError):
        deprecate_local_code(
            app_session,
            AuditContext.system(),
            actor=_administrator(),
            code=code,
            reason="superseded again",
        )

    assert _audit_event_count(app_session) == before


# --- create_snomed_map_row ---------------------------------------------------


@pytest.mark.req("FR-91")
def test_create_snomed_map_row_records_one_audit_event(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="map_svc_test",
        uri="https://nptc.example.org/CodeSystem/map_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="chemical_pathology",
        display="Chemical pathology",
        reason="creating a test fixture code",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    map_row = create_snomed_map_row(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        local_code=code,
        code=_VALID_SNOMED_CODE,
        display="Chemical pathology",
        match_strength="exact",
        advisory_note="Advisory only, not a code_binding: test fixture.",
        reason="publishing the advisory map",
    )

    assert isinstance(map_row, LocalCodeSnomedMap)
    assert _audit_event_count(app_session) == before + 1


@pytest.mark.req("FR-91")
def test_create_snomed_map_row_rejects_an_invalid_match_strength(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="map_bad_strength_svc_test",
        uri="https://nptc.example.org/CodeSystem/map_bad_strength_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="chemical_pathology",
        display="Chemical pathology",
        reason="creating a test fixture code",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(InvalidMatchStrengthError):
        create_snomed_map_row(
            app_session,
            AuditContext.system(),
            actor=_administrator(),
            local_code=code,
            code=_VALID_SNOMED_CODE,
            display="Chemical pathology",
            match_strength="perfect",
            advisory_note="Advisory only, not a code_binding: test fixture.",
            reason="publishing the advisory map",
        )

    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-91")
def test_create_snomed_map_row_rejects_an_invalid_sctid(app_session: Session) -> None:
    """`ck_local_code_snomed_map_code` (`nptc_sctid_is_valid`) is the
    actual database invariant; this pins the fail-loud Python-level layer
    that pre-empts it, matching `create_binding`'s own treatment of
    `code`."""
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="map_bad_code_svc_test",
        uri="https://nptc.example.org/CodeSystem/map_bad_code_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="chemical_pathology",
        display="Chemical pathology",
        reason="creating a test fixture code",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(InvalidSCTIDError):
        create_snomed_map_row(
            app_session,
            AuditContext.system(),
            actor=_administrator(),
            local_code=code,
            code="not-a-code",
            display="Chemical pathology",
            match_strength="exact",
            advisory_note="Advisory only, not a code_binding: test fixture.",
            reason="publishing the advisory map",
        )

    assert _audit_event_count(app_session) == before


def test_create_snomed_map_row_requires_registry_manage(app_session: Session) -> None:
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="map_denied_svc_test",
        uri="https://nptc.example.org/CodeSystem/map_denied_svc_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    code = create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="chemical_pathology",
        display="Chemical pathology",
        reason="creating a test fixture code",
    )
    app_session.flush()

    with pytest.raises(PermissionDeniedError):
        create_snomed_map_row(
            app_session,
            AuditContext.system(),
            actor=_observer(),
            local_code=code,
            code=_VALID_SNOMED_CODE,
            display="Chemical pathology",
            match_strength="exact",
            advisory_note="Advisory only, not a code_binding: test fixture.",
            reason="publishing the advisory map",
        )


# --- LocalCodeLookup ----------------------------------------------------------


def test_database_local_code_lookup_resolves_a_seeded_discipline(app_session: Session) -> None:
    """Exercises migration 0010's own seed data end to end."""
    lookup = DatabaseLocalCodeLookup(app_session)

    resolved = lookup.resolve("discipline", "chemical_pathology")

    assert resolved is not None
    assert resolved.display == "Chemical pathology"
    assert resolved.status == str(LocalCodeStatus.ACTIVE)
    assert resolved.system_status == str(LocalCodeSystemStatus.ACTIVE)
    assert resolved.provisional is False


def test_database_local_code_lookup_returns_none_for_an_unknown_code(app_session: Session) -> None:
    lookup = DatabaseLocalCodeLookup(app_session)

    assert lookup.resolve("discipline", "not_a_real_code") is None


def test_find_local_code_returns_none_for_an_unknown_system(app_session: Session) -> None:
    assert find_local_code(app_session, system_key="not_a_real_system", code="x") is None
    assert (
        find_local_code_with_system_status(app_session, system_key="not_a_real_system", code="x")
        is None
    )


@pytest.mark.req("FR-90")
def test_lookup_surfaces_system_deprecation_independently_of_the_code(
    app_session: Session,
) -> None:
    """`deprecate_local_code_system` deprecates the system without
    touching its member codes' own `status` - a caller resolving a code
    through a since-deprecated system needs `system_status` to see that;
    `status` alone still reads `active`."""
    system = create_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        key="system_deprecation_visible_test",
        uri="https://nptc.example.org/CodeSystem/system_deprecation_visible_test",
        title="Discipline",
        description="test",
        owner="RCPA-QAP",
        reason="creating a test fixture code system",
    )
    app_session.flush()
    create_local_code(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        code="untouched_code",
        display="Untouched code",
        reason="creating a test fixture code",
    )
    app_session.flush()
    deprecate_local_code_system(
        app_session,
        AuditContext.system(),
        actor=_administrator(),
        system=system,
        reason="deprecating this system",
    )
    app_session.flush()

    resolved = DatabaseLocalCodeLookup(app_session).resolve(
        "system_deprecation_visible_test", "untouched_code"
    )

    assert resolved is not None
    assert resolved.status == str(LocalCodeStatus.ACTIVE)
    assert resolved.system_status == str(LocalCodeSystemStatus.DEPRECATED)


# --- Acceptance criterion: the advisory map is never treated as a
#     code binding by the validation sweep -----------------------------------


def _referenced_names(source: str, names: frozenset[str]) -> set[str]:
    """Every `Name`/`Attribute` identifier referenced anywhere in `source`
    matching one of `names` - a plain AST walk, not a substring search, so
    a comment or docstring mentioning the name in prose does not itself
    count. Mirrors `test_catalogue_bindings.py`'s own `_referenced_names`."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name in names or name in names:
                    found.add(alias.name)
    return found


_MAP_NAMES = frozenset({"LocalCodeSnomedMap"})

#: The only legitimate references: the aggregator that imports it for
#: autogenerate (issue #56's own `db/models/__init__.py` change), and the
#: service module that manages it. The model module that *defines* the
#: class (`local_code_snomed_map.py`) is deliberately not listed - a
#: `ClassDef` is not a `Name`/`Attribute`/`Import` reference, so
#: `_referenced_names` never matches it there in the first place; see
#: `test_allowed_references_list_is_not_stale`, which would catch this
#: allowlist going stale if that ever changed. Explicit and exhaustive,
#: matching `test_catalogue_bindings.py`'s own `_ALLOWED_REFERENCES`
#: precedent - a new reference anywhere else in `backend/src` (in
#: particular `nptc.validation`, once it lands, or `nptc.catalogue.
#: bindings`) fails this test.
_ALLOWED_REFERENCES = frozenset(
    {
        REPO_ROOT / "backend" / "src" / "nptc" / "db" / "models" / "__init__.py",
        REPO_ROOT / "backend" / "src" / "nptc" / "registry" / "local_codes.py",
    }
)

_POSITIVE_CONTROL_SOURCE = """
from nptc.db.models.local_code_snomed_map import LocalCodeSnomedMap


def rogue_sweep_check(session):
    return session.query(LocalCodeSnomedMap).all()
"""


def test_guard_flags_a_known_violation() -> None:
    """Positive control (mirrors `test_catalogue_bindings.py`'s own
    precedent) - proves the walker can actually fail."""
    assert _referenced_names(_POSITIVE_CONTROL_SOURCE, _MAP_NAMES)


def _all_backend_source_files() -> list[Path]:
    return sorted((REPO_ROOT / "backend" / "src").rglob("*.py"))


@pytest.mark.parametrize(
    "path", _all_backend_source_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_local_code_snomed_map_is_never_referenced_outside_its_own_module(path: Path) -> None:
    """The acceptance criterion, as code: the advisory map is never
    treated as a code binding by the validation sweep. `nptc.validation`
    is still a stub today, so this guard's real job is to fail loudly the
    day something under it (or `nptc.catalogue.bindings`) first imports
    `LocalCodeSnomedMap`, rather than let that slip through review
    unnoticed."""
    if path in _ALLOWED_REFERENCES:
        pytest.skip(
            "the model's own definition, the autogenerate aggregator, or this issue's service module"
        )

    source = path.read_text(encoding="utf-8")
    referenced = _referenced_names(source, _MAP_NAMES)
    assert not referenced, f"{path}: unexpected reference to {referenced}"


def test_allowed_references_list_is_not_stale() -> None:
    for path in _ALLOWED_REFERENCES:
        assert path.is_file(), f"{path} no longer exists - remove it from _ALLOWED_REFERENCES"
        assert _referenced_names(path.read_text(encoding="utf-8"), _MAP_NAMES), (
            f"{path} no longer references LocalCodeSnomedMap - remove it from _ALLOWED_REFERENCES"
        )


def test_local_code_snomed_map_model_has_no_entry_id_column() -> None:
    """Structural half of the acceptance criterion: there is no
    `entry_id` column, and no foreign key to `catalogue_entry`, on this
    table at all - so a sweep cannot even naively join it to
    `catalogue_entry` the way it does `code_binding`. Inspects the mapped
    table's actual columns/FKs, not the module source (which mentions both
    names in prose, in its own docstring)."""
    columns = LocalCodeSnomedMap.__table__.columns
    assert "entry_id" not in columns
    referred_tables = {fk.column.table.name for column in columns for fk in column.foreign_keys}
    assert "catalogue_entry" not in referred_tables
