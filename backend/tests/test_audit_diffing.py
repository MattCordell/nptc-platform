"""Offline unit tests for nptc.audit.diffing (issue #37, NFR-08).

No container, no network: exercises SQLAlchemy's own attribute history and
`diff_snapshots` against an isolated in-memory mapped model, never a real
Postgres connection - `backend/tests/test_audit_diff_write_path.py` is the
integration counterpart that drives this against a real database.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from nptc.audit.diffing import ChangeKind, FieldChange, FieldDiff, diff_instance, diff_snapshots
from nptc.audit.policy import (
    AuditFieldPolicy,
    AuditPolicyError,
    DeniedAuditFieldError,
    policy_for,
)


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "widget"

    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status", "count"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"owner_name"})

    id: Mapped[int] = mapped_column(primary_key=True)
    # active_history=True on every auditable/withheld column: policy_for
    # enforces this (issue #37) since diff_instance's load_history() call
    # cannot recover a prior value reassigned before ever being loaded
    # without it - see nptc.db.models.user's own comment on the same rule.
    status: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    count: Mapped[int] = mapped_column(nullable=False, default=0, active_history=True)
    owner_name: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)


@pytest.fixture
def session() -> Session:
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://", poolclass=StaticPool)
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=True)()


def _persisted_widget(
    session: Session, *, status: str = "active", count: int = 0, owner_name: str | None = None
) -> _Widget:
    widget = _Widget(status=status, count=count, owner_name=owner_name)
    session.add(widget)
    session.commit()
    return widget


def test_single_field_edit_records_only_that_field(session: Session) -> None:
    """NFR-08, AC-1: changing one field diffs only that field."""
    widget = _persisted_widget(session)

    widget.status = "suspended"
    diff = diff_instance(widget, kind=ChangeKind.UPDATED)

    assert diff.changes == {"status": FieldChange(before="active", after="suspended")}
    assert diff.redacted == frozenset()


def test_unchanged_fields_are_absent_not_null_to_null(session: Session) -> None:
    """AC-1's principal failure mode: a field nobody touched must be
    completely absent from the diff, not present as before==after==null
    (which would look like a recorded no-op change rather than no entry at
    all)."""
    widget = _persisted_widget(session, count=5)

    widget.status = "suspended"
    diff = diff_instance(widget, kind=ChangeKind.UPDATED)

    assert "count" not in diff.changes
    assert diff.before_payload() == {"status": "active"}
    assert diff.after_payload() == {"status": "suspended"}


def test_assigning_the_same_value_is_not_a_change(session: Session) -> None:
    widget = _persisted_widget(session)

    widget.status = "active"
    diff = diff_instance(widget, kind=ChangeKind.UPDATED)

    assert diff.is_empty()


def test_diff_of_an_expired_instance_loads_its_committed_value(session: Session) -> None:
    """Proves `load_history()` is used, not `.history`: `.history` runs
    passive and returns HISTORY_BLANK (reporting "no change") for an
    expired attribute, which would silently hide a genuine edit."""
    widget = _persisted_widget(session)
    widget_id = widget.id

    widget.status = "suspended"
    session.commit()
    session.expire_all()

    reloaded = session.get(_Widget, widget_id)
    assert reloaded is not None
    reloaded.status = "closed"

    diff = diff_instance(reloaded, kind=ChangeKind.UPDATED)

    assert diff.changes["status"] == FieldChange(before="suspended", after="closed")


def test_created_has_no_before_and_omits_none_fields(session: Session) -> None:
    widget = _Widget(status="active", count=0, owner_name=None)
    session.add(widget)

    diff = diff_instance(widget, kind=ChangeKind.CREATED)

    assert diff.before_payload() is None
    assert diff.after_payload() == {"status": "active", "count": 0}


def test_deleted_has_no_after(session: Session) -> None:
    widget = _persisted_widget(session, count=3)

    diff = diff_instance(widget, kind=ChangeKind.DELETED)

    assert diff.after_payload() is None
    assert diff.before_payload() == {"status": "active", "count": 3}


def test_withheld_field_change_is_named_not_valued(session: Session) -> None:
    widget = _persisted_widget(session, owner_name="alice")

    widget.owner_name = "bob"
    diff = diff_instance(widget, kind=ChangeKind.UPDATED)

    assert "owner_name" not in diff.changes
    assert diff.redacted == frozenset({"owner_name"})
    assert diff.before_payload() == {"_redacted": ["owner_name"]}
    assert diff.after_payload() == {"_redacted": ["owner_name"]}


def test_is_empty_is_false_when_only_a_withheld_field_changed(session: Session) -> None:
    widget = _persisted_widget(session, owner_name="alice")

    widget.owner_name = "bob"
    diff = diff_instance(widget, kind=ChangeKind.UPDATED)

    assert not diff.is_empty()


# --- diff_snapshots ---------------------------------------------------------

_POLICY = AuditFieldPolicy(
    entity_type="widget",
    auditable=frozenset({"status"}),
    withheld=frozenset({"owner_name"}),
    known=frozenset({"status", "owner_name", "count"}),
)


def test_diff_snapshots_single_field_change() -> None:
    diff = diff_snapshots(
        policy=_POLICY,
        before={"status": "active"},
        after={"status": "suspended"},
        kind=ChangeKind.UPDATED,
    )

    assert diff.changes == {"status": FieldChange(before="active", after="suspended")}


def test_diff_snapshots_created_has_no_before() -> None:
    diff = diff_snapshots(
        policy=_POLICY, before=None, after={"status": "active"}, kind=ChangeKind.CREATED
    )

    assert diff.before_payload() is None
    assert diff.after_payload() == {"status": "active"}


def test_diff_snapshots_refuses_an_undeclared_key() -> None:
    with pytest.raises(AuditPolicyError):
        diff_snapshots(
            policy=_POLICY,
            before={"count": 1},
            after={"count": 2},
            kind=ChangeKind.UPDATED,
        )


def test_diff_snapshots_refuses_a_denied_key_even_if_hand_supplied() -> None:
    sneaky_policy = AuditFieldPolicy(
        entity_type="widget",
        auditable=frozenset({"status"}),
        withheld=frozenset(),
        known=frozenset({"status"}),
    )
    with pytest.raises(DeniedAuditFieldError):
        diff_snapshots(
            policy=sneaky_policy,
            before={"password": "x"},
            after={"password": "y"},
            kind=ChangeKind.UPDATED,
        )


def test_field_diff_construction_defaults() -> None:
    diff = FieldDiff(kind=ChangeKind.UPDATED)
    assert diff.is_empty()
    assert diff.before_payload() == {}
    assert diff.after_payload() == {}


def test_policy_for_is_cached_by_model_identity() -> None:
    assert policy_for(_Widget) is policy_for(_Widget)
