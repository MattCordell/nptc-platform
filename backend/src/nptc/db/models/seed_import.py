"""The `seed_import` table: the one record of the baseline seeding run (FR-76, ADR-0042).

FR-76 calls the seeded catalogue a synthetic baseline release. The `release` table is P4's
(FR-57, FR-58, FR-61), so until then the baseline is recorded here rather than as a placeholder
`Release` row, which would fix P4's shape early. P4 promotes this row into a real release.

**At most one row, by construction.** `singleton` is `TRUE` on every row and unique, so a second
seeding run cannot record itself. The loader also refuses a non-empty catalogue before it writes
anything (`nptc.catalogue.seed_import`).

**Never edited or removed.** `nptc.db.roles.REVOKE_SEED_IMPORT_WRITE_SQL` makes "insert once" a
privilege-level guarantee, as for `designation_collision_acknowledgement`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["SeedImport"]

#: Plain string literals, never built from runtime data: `test_sql_parameterisation.py`'s AST guard
#: forbids an f-string as a SQL call's first argument.
_SINGLETON_SQL = "singleton"
_RELEASE_NAME_NOT_BLANK_SQL = "length(btrim(release_name)) > 0"
_SOURCE_SHA256_SQL = "source_sha256 ~ '^[0-9a-f]{64}$'"
_ENTRY_COUNT_SQL = "entry_count > 0"


class SeedImport(Base):
    __tablename__ = "seed_import"

    # nptc.audit.policy (NFR-08): every real column classified. Nothing here is a user reference or
    # privacy-sensitive: the filename is a basename (ADR-0010) and the digest names a file.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "release_name",
            "release_note",
            "source_filename",
            "source_sha256",
            "dataset_schema_version",
            "entry_count",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "singleton", "created_at"}
    )

    __table_args__ = (
        CheckConstraint(_SINGLETON_SQL, name="singleton"),
        CheckConstraint(_RELEASE_NAME_NOT_BLANK_SQL, name="release_name_not_blank"),
        CheckConstraint(_SOURCE_SHA256_SQL, name="source_sha256"),
        CheckConstraint(_ENTRY_COUNT_SQL, name="entry_count"),
        Index("ix_seed_import_singleton", "singleton", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    singleton: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), active_history=True
    )
    release_name: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    release_note: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    source_filename: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    dataset_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, active_history=True
    )
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
