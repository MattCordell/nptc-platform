"""The `entry_seed_provenance` table: where each seeded entry came from (FR-76, ADR-0010, ADR-0042).

The workbook's hand-typed `Version` and `History` cells cannot be editable fields once release
membership generates them (FR-59), but they are real provenance, so ADR-0010 keeps them verbatim
beside the sheet and row they came from. They live here, not as nullable columns on
`catalogue_entry`, because that table would then carry columns that are null for every entry
created after cutover.

**One row per seeded entry, never edited or removed.** `entry_id` is the primary key, so an entry
has at most one provenance row. `nptc.db.roles.REVOKE_ENTRY_SEED_PROVENANCE_WRITE_SQL` makes
"insert once" a privilege-level guarantee, which is what stops any other write path from touching
a value ADR-0010 calls "never an editable field". An entry created through the application has no
row here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["EntrySeedProvenance"]

#: Plain string literals, never built from runtime data: `test_sql_parameterisation.py`'s AST guard
#: forbids an f-string as a SQL call's first argument.
_SOURCE_SHEET_NOT_BLANK_SQL = "length(btrim(source_sheet)) > 0"
_SOURCE_ROW_SQL = "source_row >= 1"


class EntrySeedProvenance(Base):
    __tablename__ = "entry_seed_provenance"

    # nptc.audit.policy (NFR-08): every real column classified. `entry_id` is the primary key and
    # so the audit event's `entity_id`; `created_at` is bookkeeping.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"seed_import_id", "source_sheet", "source_row", "legacy_version", "legacy_history"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"entry_id", "created_at"})

    __table_args__ = (
        CheckConstraint(_SOURCE_SHEET_NOT_BLANK_SQL, name="source_sheet_not_blank"),
        CheckConstraint(_SOURCE_ROW_SQL, name="source_row"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalogue_entry.id"), primary_key=True
    )
    seed_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_import.id"), nullable=False, active_history=True
    )
    source_sheet: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False, active_history=True)
    legacy_version: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    legacy_history: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
