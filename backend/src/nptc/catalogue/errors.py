"""The conflict error surfaced by a stale `row_version` save (issue #46,
FR-38, NFR-38 test 8).

`EntryVersionConflictError` is deliberately **not** a subclass of
`nptc.auth.errors_authorisation.AuthorisationError`: that hierarchy's own
docstring scopes it to "we know who you are; you may not do this" -
credential/permission refusals - and a version conflict is neither. It gets
its own small hierarchy here, with the same `http_status: ClassVar[int]`
convention so a future router's exception handler can follow the same
"read the ClassVar, don't match on subclass" pattern `nptc.api.errors`
already uses.

FR-38's rationale is explicit that silent last-write-wins is unacceptable
"because it produces an audit trail that records a change that was
immediately and invisibly discarded" - so this error's `conflicts` payload
exists to let the caller actually reconcile, not just retry blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


@dataclass(frozen=True)
class FieldConflict:
    """One field the caller tried to change, whose value has moved since
    they loaded the entry."""

    field: str
    submitted: object
    current: object


@dataclass(frozen=True)
class ConflictReport:
    """Everything a caller needs to reconcile a rejected save - the
    acceptance criterion's "the caller is shown the conflicting changes",
    not a bare 409."""

    business_key: str
    expected_row_version: int
    current_row_version: int
    conflicts: tuple[FieldConflict, ...] = field(default_factory=tuple)
    #: Display name only, never the internal UUID (NFR-04/NFR-26) - sourced
    #: from the audit log's own already-redacted actor rendering.
    changed_by: str | None = None
    changed_at: datetime | None = None


class EntryVersionConflictError(RuntimeError):
    """Raised by `nptc.catalogue.entries.save_entry`/`save_entries` when
    the caller's `expected_row_version` no longer matches the stored row -
    either because another save already landed (the common case, caught
    before any mutation is attempted) or because a concurrent save won the
    genuine race between load and flush (caught via SQLAlchemy's own
    `StaleDataError`, which `version_id_col` raises as the backstop for
    exactly that race). Both paths raise this one type so a caller need
    not distinguish them."""

    http_status: ClassVar[int] = 409

    def __init__(self, report: ConflictReport) -> None:
        super().__init__(
            f"stale row_version for {report.business_key}: expected "
            f"{report.expected_row_version}, current {report.current_row_version}"
        )
        self.report = report


class ImmutableFieldEditError(RuntimeError):
    """Raised when `EntryChanges` (or a caller bypassing it) attempts to
    change `business_key` - see `nptc.db.models.catalogue_entry`'s own
    `ImmutableFieldError` for the ORM-level guard this backs up at the
    service-layer boundary."""
