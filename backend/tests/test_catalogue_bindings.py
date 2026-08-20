"""Code binding service-layer tests (issue #48, FR-06, FR-08, FR-82, FR-83).

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_business_key.py`'s own module docstring for why.

FR-84's subsumption check is out of scope here - it is the FR-45 validation
sweep's own concern, layered on top of the rows created here.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.bindings import CodeBindingAlreadyRetiredError, create_binding, retire_binding
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import CodeBinding, CodeBindingStatus
from nptc_shared.sctid import InvalidSCTIDError

_VALID_CODE = "391483001"
_VALID_FSN = "Microscopy (acid fast bacilli) (procedure)"
_VALID_AU_PREFERRED_TERM = "Microscopy (acid fast bacilli)"

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_entry(session: Session, preferred_term: str = "Full blood count") -> CatalogueEntry:
    return create_entry(
        session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for FR-48 code binding test",
    )


def _new_binding(session: Session, entry: CatalogueEntry, **overrides: object) -> CodeBinding:
    defaults: dict[str, object] = {
        "code": _VALID_CODE,
        "fsn": _VALID_FSN,
        "au_preferred_term": _VALID_AU_PREFERRED_TERM,
        "reason": "Binding added for FR-48 test",
    }
    defaults.update(overrides)
    return create_binding(session, AuditContext.system(), entry=entry, **defaults)  # type: ignore[arg-type]


# --- create_binding / retire_binding ----------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_create_binding_emits_a_created_audit_event(app_session: Session) -> None:
    entry = _new_entry(app_session)
    before = _audit_event_count(app_session)

    binding = _new_binding(app_session, entry)
    app_session.flush()

    assert binding.status == str(CodeBindingStatus.ACTIVE)
    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "code_binding.created"


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retire_binding_emits_a_retired_audit_event(app_session: Session) -> None:
    entry = _new_entry(app_session)
    binding = _new_binding(app_session, entry)
    app_session.flush()
    before = _audit_event_count(app_session)

    retire_binding(
        app_session,
        AuditContext.system(),
        binding=binding,
        reason="Withdrawn - no longer requestable",
    )
    app_session.flush()

    assert binding.status == str(CodeBindingStatus.RETIRED)
    assert binding.retirement_reason == "Withdrawn - no longer requestable"
    assert binding.replaced_by_binding_id is None
    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "code_binding.retired"


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retire_binding_with_a_successor_populates_replaced_by(app_session: Session) -> None:
    entry = _new_entry(app_session)
    superseded = _new_binding(app_session, entry)
    app_session.flush()

    retire_binding(
        app_session,
        AuditContext.system(),
        binding=superseded,
        reason="Superseded following inactivation",
    )
    successor = _new_binding(
        app_session,
        entry,
        code="71388002",
        fsn="Procedure (procedure)",
        au_preferred_term="Procedure",
        reason="Replacement binding added",
    )
    app_session.flush()

    superseded.replaced_by_binding_id = successor.id
    app_session.flush()

    assert superseded.replaced_by_binding_id == successor.id


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retiring_an_already_retired_binding_raises(app_session: Session) -> None:
    entry = _new_entry(app_session)
    binding = _new_binding(app_session, entry)
    app_session.flush()
    retire_binding(app_session, AuditContext.system(), binding=binding, reason="First retirement")
    app_session.flush()

    with pytest.raises(CodeBindingAlreadyRetiredError):
        retire_binding(
            app_session, AuditContext.system(), binding=binding, reason="Second retirement"
        )


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_create_binding_rejects_a_malformed_code(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(InvalidSCTIDError):
        _new_binding(app_session, entry, code="12345")


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_create_binding_rejects_a_verhoeff_failing_code(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(InvalidSCTIDError):
        _new_binding(app_session, entry, code="391483002")


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_fsn_and_au_preferred_term_are_independent(app_session: Session) -> None:
    """Updating one leaves the other untouched - they are stored and
    compared separately, per PRD SS6.4."""
    entry = _new_entry(app_session)
    binding = _new_binding(app_session, entry)
    app_session.flush()

    binding.fsn = "Microscopy (acid fast bacilli) (procedure)"
    app_session.flush()

    assert binding.au_preferred_term == _VALID_AU_PREFERRED_TERM


# --- FR-82: no cleaning hook of any kind ------------------------------------


def test_code_binding_model_has_no_cleaning_hook_over_served_labels() -> None:
    """A served label is stored exactly as served (FR-82) - no
    `clean_term`/`normalise_for_comparison`/`strip_semantic_tag` reference
    anywhere in the model that owns `fsn`/`au_preferred_term`."""
    import nptc.db.models.code_binding as module

    source = inspect.getsource(module)
    assert "clean_term" not in source
    assert "normalise_for_comparison" not in source
    assert "strip_semantic_tag" not in source


# --- FR-83: exactly one call site --------------------------------------------

_STRIP_NAMES = frozenset({"strip_semantic_tag", "semantic_tag", "render_display_term"})

_POSITIVE_CONTROL_SOURCE = """
from nptc_shared.terminology import strip_semantic_tag


def rogue_helper(fsn: str) -> str:
    return strip_semantic_tag(fsn)
"""


def _referenced_names(source: str) -> set[str]:
    """Every `Name`/`Attribute` identifier referenced anywhere in `source`
    matching one of `_STRIP_NAMES` - a plain AST walk, not a substring
    search, so a comment or docstring mentioning the name in prose does
    not itself count."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _STRIP_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _STRIP_NAMES:
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name in _STRIP_NAMES or name in _STRIP_NAMES:
                    found.add(alias.name)
    return found


def test_guard_flags_a_known_violation() -> None:
    """Positive control (mirrors `test_sql_parameterisation.py`'s own
    precedent) - proves the walker can actually fail, so a refactor that
    quietly makes it match nothing doesn't rot into a test that always
    passes."""
    assert _referenced_names(_POSITIVE_CONTROL_SOURCE)


@pytest.mark.parametrize(
    "path",
    sorted((REPO_ROOT / "backend" / "src").rglob("*.py")),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_semantic_tag_functions_are_referenced_only_within_exports(path: Path) -> None:
    if any(parent.name == "exports" for parent in path.parents):
        pytest.skip("the export renderer package is the one legitimate call site")

    source = path.read_text(encoding="utf-8")
    referenced = _referenced_names(source)
    assert not referenced, f"{path}: unexpected reference to {referenced}"
