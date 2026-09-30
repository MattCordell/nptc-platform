"""The `local_code_snomed_map` table: an advisory, non-authoritative map from a `local_code` to a
SNOMED CT concept (FR-91). See PRD SS6.6.

**Never a `code_binding`, structurally.** `code_binding` binds a catalogue entry to the code the
terminology server serves for it (FR-06, FR-08, FR-82). It is authoritative and the FR-45 sweep
revalidates it; a row here must never be treated that way. This table has no `entry_id` and no
foreign key to `catalogue_entry`, so a change that joins it into sweep logic would have to invent
that join. `backend/tests/test_catalogue_local_codes.py` pins this with an AST guard: no module
under `nptc.validation` or `nptc.catalogue.bindings` may reference `LocalCodeSnomedMap`.

**Advisory in three structural ways.** `match_strength` has no counterpart in `code_binding`, so a
row read out of context still says what kind of claim it makes. `advisory_note` is mandatory, so
every row explains its own caveat. There is deliberately no row for `Molecular` or `Serology`: PRD
SS6.6's verification found no SNOMED concept that genuinely matches either. The nearest candidates
(`1236877003`, and `708179009`/`708188000`) are a different discipline and healthcare *service*
concepts, confirmed not subsumed by `check_subsumption`. FR-91 requires that gap to stay visible, so
an absent row is the honest representation of "no match exists".

**No uniqueness constraint on `local_code_id`.** PRD SS6.6 records `Microbiology` as genuinely
ambiguous between `408454008` |Clinical microbiology| and `394820005` |Medical microbiology|.
Collapsing that to one row would be the approximation FR-91 forbids, so a local code may have zero,
one or several map rows, each with its own `match_strength`.

**`code` and `system` reuse `code_binding`'s validation.** `nptc_sctid_is_valid`
(`nptc.db.functions`, ADR-0023) is the same database function, so a SNOMED identifier here meets the
same format-and-Verhoeff standard as a real binding.

**Never edited, only replaced.** A row records a point-in-time editorial judgement about the nearest
analogue. As with `designation_collision_acknowledgement`, there is no update path: a revised
mapping is a new row, and the sweep-exclusion guard means a stale row carries no safety consequence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = ["LocalCodeSnomedMap", "SnomedMapMatchStrength"]


class SnomedMapMatchStrength(StrEnum):
    EXACT = "exact"
    NARROWER = "narrower"
    BROADER = "broader"
    AMBIGUOUS = "ambiguous"


#: Plain literals, never built from the `StrEnum` above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_SYSTEM_NOT_BLANK_SQL = "length(btrim(system)) > 0"
_CODE_CHECK_SQL = "nptc_sctid_is_valid(code)"
_DISPLAY_NOT_BLANK_SQL = "length(btrim(display)) > 0"
_MATCH_STRENGTH_CHECK_SQL = "match_strength IN ('exact','narrower','broader','ambiguous')"
_ADVISORY_NOTE_NOT_BLANK_SQL = "length(btrim(advisory_note)) > 0"


class LocalCodeSnomedMap(Base):
    __tablename__ = "local_code_snomed_map"

    # nptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"local_code_id", "system", "code", "display", "match_strength", "advisory_note"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_SYSTEM_NOT_BLANK_SQL, name="system_not_blank"),
        CheckConstraint(_CODE_CHECK_SQL, name="code"),
        CheckConstraint(_DISPLAY_NOT_BLANK_SQL, name="display_not_blank"),
        CheckConstraint(_MATCH_STRENGTH_CHECK_SQL, name="match_strength"),
        CheckConstraint(_ADVISORY_NOTE_NOT_BLANK_SQL, name="advisory_note_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    local_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_code.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'http://snomed.info/sct'"),
        active_history=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    display: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    match_strength: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    advisory_note: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # `onupdate` is unreachable in practice: `REVOKE_LOCAL_CODE_SNOMED_MAP_WRITE_SQL` revokes
    # UPDATE, and the module docstring's "never edited, only replaced" is the policy. It stays so
    # the column set matches every sibling table (`created_at` and `updated_at` are always a pair),
    # and a future privilege change need not re-add it.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("local_code_id")
    def _validate_local_code_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A revised mapping is a new row, never a reparented one."""
        if "local_code_id" in self.__dict__ and self.__dict__["local_code_id"] is not None:
            raise ImmutableFieldError(
                "LocalCodeSnomedMap.local_code_id is immutable and cannot be reassigned "
                f"from {self.__dict__['local_code_id']!r} to {value!r}"
            )
        return value
