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
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.bindings import (
    CodeBindingAlreadyActiveError,
    CodeBindingAlreadyRetiredError,
    CodeBindingCodeAlreadyBoundError,
    CodeBindingNotRetiredError,
    CodeBindingSelfSupersessionError,
    InvalidCodeBindingEditionHintError,
    InvalidCodeBindingSystemError,
    create_binding,
    link_replacement,
    retire_binding,
)
from nptc.catalogue.entries import allocate_business_key, create_entry
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
def test_link_replacement_populates_replaced_by_and_emits_an_audit_event(
    app_session: Session,
) -> None:
    """The full three-step replacement sequence the module docstring
    describes: retire, create the successor, then link - each its own
    auditable write."""
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
    before = _audit_event_count(app_session)

    link_replacement(
        app_session,
        AuditContext.system(),
        superseded=superseded,
        successor=successor,
        reason="Linking superseded binding to its replacement",
    )
    app_session.flush()

    assert superseded.replaced_by_binding_id == successor.id
    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "code_binding.replacement_linked"


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_link_replacement_refuses_a_still_active_superseded_binding(
    app_session: Session,
) -> None:
    entry = _new_entry(app_session)
    superseded = _new_binding(app_session, entry)
    successor_entry = _new_entry(app_session, preferred_term="Other entry")
    successor = _new_binding(
        app_session,
        successor_entry,
        code="71388002",
        fsn="Procedure (procedure)",
        au_preferred_term="Procedure",
    )
    app_session.flush()

    with pytest.raises(CodeBindingNotRetiredError):
        link_replacement(
            app_session,
            AuditContext.system(),
            superseded=superseded,
            successor=successor,
            reason="Attempting to link before retiring",
        )


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_link_replacement_refuses_an_unflushed_successor(app_session: Session) -> None:
    """A `successor` with `id is None` (not yet flushed) would otherwise
    silently write `NULL` into `replaced_by_binding_id` - `CodeBinding.id`
    has no Python-side default, only `server_default=func.
    gen_random_uuid()`, so nothing else catches this before the database
    round-trip."""
    entry = _new_entry(app_session)
    superseded = _new_binding(app_session, entry)
    app_session.flush()
    retire_binding(app_session, AuditContext.system(), binding=superseded, reason="Superseded")

    unflushed_successor = CodeBinding(
        entry_id=entry.id,
        system="http://snomed.info/sct",
        code="71388002",
        fsn="Procedure (procedure)",
        au_preferred_term="Procedure",
    )
    assert unflushed_successor.id is None

    with pytest.raises(ValueError, match="has not been flushed"):
        link_replacement(
            app_session,
            AuditContext.system(),
            superseded=superseded,
            successor=unflushed_successor,
            reason="Attempting to link an unflushed successor",
        )


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_link_replacement_refuses_self_supersession(app_session: Session) -> None:
    entry = _new_entry(app_session)
    binding = _new_binding(app_session, entry)
    app_session.flush()
    retire_binding(app_session, AuditContext.system(), binding=binding, reason="Superseded")

    with pytest.raises(CodeBindingSelfSupersessionError):
        link_replacement(
            app_session,
            AuditContext.system(),
            superseded=binding,
            successor=binding,
            reason="Attempting to name itself as its own replacement",
        )


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_create_binding_flushes_an_unflushed_entry_before_binding_it(
    app_session: Session,
) -> None:
    """A transient `entry` (added to the session, never flushed) has no
    identity yet. `CodeBinding(entry_id=entry.id, ...)` evaluates
    `entry.id` immediately, not lazily - so without flushing `entry`
    first, the constructed row would carry `entry_id=None` forever (a
    plain Python attribute, never retroactively fixed once `entry` is
    flushed later), and would fail `code_binding.entry_id`'s `NOT NULL`
    constraint at flush. `create_binding`'s own pre-check for an existing
    active binding has the same hazard one step earlier: it would bake a
    stale `entry_id IS NULL` predicate into its query. `create_binding`
    must therefore flush a not-yet-flushed `entry` before doing either -
    `_new_entry` (used by every other test here) goes through
    `nptc.catalogue.entries.create_entry`, which always flushes
    internally as part of its own audit write, so this is the one test
    that builds `entry` directly to exercise the transient case at all."""
    entry = CatalogueEntry(
        business_key=allocate_business_key(app_session), preferred_term="Unflushed entry"
    )
    app_session.add(entry)
    assert sa_inspect(entry).identity is None

    binding = _new_binding(app_session, entry)
    app_session.flush()

    assert entry.id is not None
    assert binding.entry_id == entry.id


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
    compared separately, per PRD SS6.4. Assigns `fsn` a genuinely
    different value (a prior version of this test assigned the value it
    already held, so it would have passed even if the two columns were
    coupled)."""
    entry = _new_entry(app_session)
    binding = _new_binding(app_session, entry)
    app_session.flush()

    binding.fsn = "Body structure (body structure)"
    app_session.flush()

    assert binding.fsn == "Body structure (body structure)"
    assert binding.au_preferred_term == _VALID_AU_PREFERRED_TERM


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_create_binding_rejects_a_second_active_binding_on_the_same_entry(
    app_session: Session,
) -> None:
    """The most common real conflict on this table (FR-08) - checked
    before insert, so it raises a domain error rather than
    `ix_code_binding_one_active_per_entry`'s raw `IntegrityError`."""
    entry = _new_entry(app_session)
    _new_binding(app_session, entry)
    app_session.flush()

    with pytest.raises(CodeBindingAlreadyActiveError):
        _new_binding(app_session, entry, code="71388002", fsn="Procedure (procedure)")


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_create_binding_rejects_a_code_already_bound_to_a_different_entry(
    app_session: Session,
) -> None:
    """Issue #49's blocking severity - the code side of "one active
    binding" that the previous test's entry-side check doesn't cover."""
    first_entry = _new_entry(app_session)
    second_entry = _new_entry(app_session, preferred_term="Something else")
    _new_binding(app_session, first_entry)
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(CodeBindingCodeAlreadyBoundError):
        _new_binding(app_session, second_entry)

    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_code_is_rebindable_once_the_first_binding_is_retired(app_session: Session) -> None:
    first_entry = _new_entry(app_session)
    second_entry = _new_entry(app_session, preferred_term="Something else")
    binding = _new_binding(app_session, first_entry)
    app_session.flush()
    retire_binding(app_session, AuditContext.system(), binding=binding, reason="Superseded")

    rebound = _new_binding(app_session, second_entry)

    assert rebound.code == _VALID_CODE


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_create_binding_rejects_an_unknown_edition_hint(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(InvalidCodeBindingEditionHintError):
        _new_binding(app_session, entry, edition_hint="made_up_edition")


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_create_binding_rejects_a_blank_system(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(InvalidCodeBindingSystemError):
        _new_binding(app_session, entry, system="   ")


def _referenced_names(source: str, names: frozenset[str]) -> set[str]:
    """Every `Name`/`Attribute` identifier referenced anywhere in `source`
    matching one of `names` - a plain AST walk, not a substring search, so
    a comment or docstring mentioning the name in prose does not itself
    count."""
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


# --- FR-82: no cleaning hook of any kind ------------------------------------

_CLEANING_HOOK_NAMES = frozenset({"clean_term", "normalise_for_comparison", "strip_semantic_tag"})


def test_code_binding_model_has_no_cleaning_hook_over_served_labels() -> None:
    """A served label is stored exactly as served (FR-82) - no
    `clean_term`/`normalise_for_comparison`/`strip_semantic_tag` reference
    anywhere in the model that owns `fsn`/`au_preferred_term`. An AST
    walk, not a substring search - reuses `_referenced_names` so a future
    docstring that merely *mentions* one of these names in prose (as this
    module's own docstring does) can never make this test flap."""
    import nptc.db.models.code_binding as module

    source = inspect.getsource(module)
    assert not _referenced_names(source, _CLEANING_HOOK_NAMES)


# --- FR-83: exactly one call site (plus the pre-existing FR-97 sites) -------

_STRIP_NAMES = frozenset({"strip_semantic_tag", "semantic_tag", "render_display_term"})

_POSITIVE_CONTROL_SOURCE = """
from nptc_shared.terminology import strip_semantic_tag


def rogue_helper(fsn: str) -> str:
    return strip_semantic_tag(fsn)
"""

#: Every legitimate reference outside `backend/src/nptc/exports` - the
#: shared package's own re-export of the functions (`__init__.py`,
#: an `ImportFrom` this walker matches), and the two FR-97
#: seeding-reconciliation call sites (ADR-0006) that predate this issue.
#: `nptc_shared.terminology.snomed` itself is deliberately not listed:
#: `def semantic_tag(...)`/`def strip_semantic_tag(...)` are `FunctionDef`
#: nodes, not `Name`/`Attribute` references, so defining a function never
#: trips this walker in the first place - nothing to allowlist. Explicit
#: and exhaustive, not a directory-wide exemption - a new call site
#: anywhere else, in any of the three source trees, still fails this test.
_ALLOWED_REFERENCES = frozenset(
    {
        REPO_ROOT / "shared" / "src" / "nptc_shared" / "terminology" / "__init__.py",
        REPO_ROOT / "shared" / "src" / "nptc_shared" / "terminology" / "sweep.py",
        REPO_ROOT / "transform" / "src" / "nptc_transform" / "designation_check.py",
    }
)

#: The three polyglot-monorepo source trees this guard actually needs to
#: cover - `nptc_shared`/`nptc_transform` can call `strip_semantic_tag`
#: exactly as easily as `backend/src` can, so scoping the walk to
#: `backend/src` alone (as an earlier version of this guard did) would
#: leave FR-83's guarantee unchecked outside the backend entirely.
_SOURCE_TREES = ("backend/src", "transform/src", "shared/src")


def test_guard_flags_a_known_violation() -> None:
    """Positive control (mirrors `test_sql_parameterisation.py`'s own
    precedent) - proves the walker can actually fail, so a refactor that
    quietly makes it match nothing doesn't rot into a test that always
    passes."""
    assert _referenced_names(_POSITIVE_CONTROL_SOURCE, _STRIP_NAMES)


def _all_source_files() -> list[Path]:
    return sorted(path for tree in _SOURCE_TREES for path in (REPO_ROOT / tree).rglob("*.py"))


@pytest.mark.parametrize("path", _all_source_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_semantic_tag_functions_are_referenced_only_at_known_sites(path: Path) -> None:
    if any(parent.name == "exports" for parent in path.parents):
        pytest.skip("the export renderer package is the one legitimate FR-83 call site")
    if path in _ALLOWED_REFERENCES:
        pytest.skip(
            "a pre-existing FR-97 seeding-reconciliation site (ADR-0006), or the "
            "shared package's own definition/re-export"
        )

    source = path.read_text(encoding="utf-8")
    referenced = _referenced_names(source, _STRIP_NAMES)
    assert not referenced, f"{path}: unexpected reference to {referenced}"


def test_allowed_references_list_is_not_stale() -> None:
    """Every path in `_ALLOWED_REFERENCES` must actually exist and must
    actually reference one of `_STRIP_NAMES` - otherwise the allowlist is
    silently over-broad, exempting a path that no longer needs it."""
    for path in _ALLOWED_REFERENCES:
        assert path.is_file(), f"{path} no longer exists - remove it from _ALLOWED_REFERENCES"
        assert _referenced_names(path.read_text(encoding="utf-8"), _STRIP_NAMES), (
            f"{path} no longer references {sorted(_STRIP_NAMES)} - remove it from "
            "_ALLOWED_REFERENCES"
        )
