"""The `catalogue_entry` table: the platform's central entity (FR-03, FR-38).

Rationale: `docs/architecture/data-model.md`, "`business_key` minting and
immutability" and "Optimistic locking". In short:

- `business_key` is minted in Python (`nptc.catalogue.entries.
  allocate_business_key`), never as a column `server_default`, so
  `format_business_key` is the one place the format is spelled out.
- `row_version` is owned by `version_id_col` alone (ADR-0012). A Core
  `update()` or `delete()` bypasses it, which `test_sql_parameterisation.py`
  guards against.
- `status` is `TEXT` plus `CHECK`, not a native `ENUM`, as on `app_user.status`.
- `business_key` is immutable at two layers. The column-level `UPDATE` grant in
  `nptc.db.roles` is the guarantee; the `@validates` guard below fails loudly
  before flush.
- `business_key` is never reissued (FR-03): its sequence is monotonic, it is
  `UNIQUE`, and `nptc_app` holds no `DELETE` or `TRUNCATE` on the table.
- `preferred_term` is cleaned at entry (FR-63). `length` (FR-85, FR-24) is
  computed, never stored. It counts the catalogue's own preferred term, not a
  `designation` row (ADR-0022).
- `preferred_term_key` (FR-05) is derived by the same `@validates` hook, so no
  path sets one without the other. It is stored and indexed because
  `nptc.catalogue.collisions` needs an equality lookup, and it is
  `__audit_ignored__`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.catalogue.term_hygiene import clean_term, preferred_term_length
from nptc.db.base import Base
from nptc_shared.similarity import collision_key


class CatalogueEntryStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    WITHDRAWN = "withdrawn"


#: A plain literal: `test_sql_parameterisation.py`'s AST guard forbids SQL
#: built from runtime data.
_STATUS_CHECK_SQL = "status IN ('draft','active','deprecated','withdrawn')"

#: `6,` (not `6`) so the format survives a catalogue passing 999,999 entries
#: without a migration. Mirrors `nptc.catalogue.entries.BUSINESS_KEY_PATTERN`.
_BUSINESS_KEY_CHECK_SQL = "business_key ~ '^NPTC-[0-9]{6,}$'"


class ImmutableFieldError(RuntimeError):
    """Raised by the `business_key` `@validates` guard when anything but the
    initial assignment changes it. The database invariant is the column
    exclusion in `nptc.db.roles.GRANT_CATALOGUE_ENTRY_UPDATE_SQL`."""


class CatalogueEntry(Base):
    __tablename__ = "catalogue_entry"

    # `nptc.audit.policy` (NFR-08) requires every real column to be classified.
    # `business_key` is auditable so a CREATED event records the minted key; it
    # is immutable, so it never appears in an UPDATE diff. `row_version` is
    # bookkeeping for FR-38, never a "changed field", like `User.id` and
    # `created_at`.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"business_key", "preferred_term", "status", "specimen_unconstrained"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at", "row_version", "preferred_term_key"}
    )

    __table_args__ = (
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_BUSINESS_KEY_CHECK_SQL, name="business_key"),
        # FR-05: an indexed lookup for a cross-entry collision. A plain btree
        # is enough because `nptc.catalogue.collisions` filters by `status` in
        # the query.
        Index("ix_catalogue_entry_preferred_term_key", "preferred_term_key"),
        # FR-14/FR-15: the trigram index behind public search, over
        # `nptc_search_text` (ADR-0024). Declared here as well as in the
        # migration, or `compare_metadata` proposes dropping it on every
        # autogenerate run. Not partial on `status`, because maintenance
        # search covers drafts.
        Index(
            "ix_catalogue_entry_preferred_term_trgm",
            text("nptc_search_text(preferred_term)"),
            postgresql_using="gin",
            # `postgresql_ops`, not the operator class inside the expression
            # text: alembic skips comparing an expression index whose operator
            # class is inline.
            postgresql_ops={"nptc_search_text(preferred_term)": "gin_trgm_ops"},
        ),
        # FR-14/FR-15: the full-text half of the hybrid, over
        # `nptc_search_document` (ADR-0029). It sits beside the trigram index
        # because each finds matches the other scores as near-misses; the
        # search keeps the better score.
        #
        # No `postgresql_ops`: `tsvector_ops` is GIN's default class and
        # Postgres omits a default from `indexdef`, so naming it would make the
        # declaration and the reflected index disagree. Not partial on
        # `status`, as above.
        Index(
            "ix_catalogue_entry_preferred_term_fts",
            text("nptc_search_document(preferred_term)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    business_key: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, active_history=True
    )
    preferred_term: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # FR-05: derived from `preferred_term` by the `@validates` hook below, never
    # assigned directly. `server_default=''` exists only so a raw INSERT that
    # bypasses the ORM (the `test_db_*.py` constraint tests) still satisfies
    # `NOT NULL`, as on `Designation.term_key`.
    preferred_term_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # A quoted literal: an unquoted `server_default` string is rendered
    # verbatim, and `DEFAULT draft` is not valid DDL for a text column.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'"), active_history=True
    )
    specimen_unconstrained: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # FR-38: bumped by `version_id_col` on every mapped UPDATE; nothing else
    # may touch it (see the module docstring).
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # Must follow `row_version`: `version_id_col` binds to that `MappedColumn`,
    # and the name does not exist earlier in the class body. No `ClassVar`
    # annotation, because mypy flags a `ClassVar` override of the base class's
    # `__mapper_args__`; the `noqa` silences ruff's mutable-class-attribute
    # lint.
    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012

    @validates("business_key")
    def _validate_business_key_immutable(self, _key: str, value: str) -> str:
        if "business_key" in self.__dict__ and self.__dict__["business_key"] is not None:
            raise ImmutableFieldError(
                "CatalogueEntry.business_key is immutable (FR-03) and cannot be "
                f"reassigned from {self.__dict__['business_key']!r} to {value!r}"
            )
        return value

    @validates("preferred_term")
    def _validate_preferred_term(self, _key: str, value: str) -> str:
        cleaned = clean_term(value)
        self.preferred_term_key = collision_key(cleaned)
        return cleaned

    @property
    def length(self) -> int:
        """FR-85/FR-24: the character count of `preferred_term` after the
        same whitespace cleaning applied at entry - computed here, never
        stored, never settable. See the module docstring for why this is
        the field FR-85 is actually about."""
        return preferred_term_length(self.preferred_term)
