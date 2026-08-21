"""The `catalogue_entry` table: the platform's central entity (issue #46,
FR-03, FR-38).

**`business_key` is minted in Python (`nptc.catalogue.entries.
allocate_business_key`), never as a column `server_default`.** A
`DEFAULT nextval(...)` expression would let the format (`NPTC-` plus a
zero-padded sequence, FR-03) live only in a migration's DDL string, with no
single Python source of truth `format_business_key`/parsing code could
share - and every seeded row (ADR-0010: the P0 transform mints its own
keys) would still need the default suppressed by supplying an explicit
value. Minting in the service layer keeps one function
(`nptc.catalogue.entries.format_business_key`) as the only place the format
is spelled out, shared by the mint path, the seed-reconciliation path
(`advance_sequence_past`), and the `CHECK` constraint's regular expression.

**`row_version` is owned by exactly one write path: SQLAlchemy's
mapper-level optimistic concurrency (`version_id_col`) on this table's
mapped `UPDATE`** - not a migration, not a manual bump, and not
database-generated (Postgres has no built-in per-row version counter, and a
trigger-based one is banned by PRD Section 14.1). This is the same
precedent ADR-0012 already fixed for `property_definition.row_version`; see
that ADR for why a Core `sqlalchemy.update(...)`/`delete(...)` statement
against this table bypasses `version_id_col` enforcement even though it
still goes through the ORM `Session` - `backend/tests/
test_sql_parameterisation.py`'s AST guard extends to `catalogue_entry` for
exactly this reason.

**`status` is `TEXT` + `CHECK`, not a native `ENUM`**, matching
`app_user.status`'s own precedent (`ALTER TYPE ... ADD VALUE` cannot run
inside a transaction, and Alembic autogenerate mishandles the create/drop
pair on downgrade).

**`business_key` is immutable, enforced at two independent layers.** The
database layer is the real guarantee: `nptc_app`'s column-level `UPDATE`
grant (`nptc.db.roles.GRANT_CATALOGUE_ENTRY_UPDATE_SQL`) excludes
`business_key` (and `id`/`created_at`), exactly as `app_user`'s own
column-level grant excludes `id`/`created_at` - a database invariant, not
an application convention. The `@validates` guard below is a second,
Python-level layer that fails loudly and immediately on a reassignment
attempt, rather than surfacing as an opaque `InsufficientPrivilege` only at
flush time.

**Never reissued (FR-03).** Three facts combine to guarantee this: the
minting sequence is monotonic and never rolled back by the application (a
`nextval()` consumed by a rolled-back transaction is simply a gap, which
FR-03 permits); `business_key` is `UNIQUE`; and there is no `DELETE`/
`TRUNCATE` grant on this table at all (deprecation/withdrawal is a `status`
transition, never a row removal - see `nptc.db.roles.
REVOKE_CATALOGUE_ENTRY_DELETE_SQL`), so no key is ever freed to be
reissued in the first place.

**`preferred_term` is cleaned at entry (FR-63), and `length` is computed
from it, never stored (FR-85/FR-24, issue #47).** This is the field FR-85
is actually about - PRD §6.5: "it is simply the character count of the
RCPA preferred term" - not any `designation` row (ADR-0022 is explicit
that the catalogue's own en-AU preferred term is never duplicated into
`designation` at all). The `@validates("preferred_term")` guard below
calls the same `nptc.catalogue.term_hygiene.clean_term` `Designation.term`
uses, so a trailing non-breaking space (PRD Appendix A.1) is collapsed
here exactly as it would be on a synonym row, and `length` is a bare
Python `@property` with deliberately no setter and no backing column -
see `nptc.db.models.designation.Designation.length` for the same
computation applied to a designation's own term.

**`preferred_term_key` is FR-05's comparison form, stored and indexed
(issue #49).** The same `@validates("preferred_term")` hook that cleans
the term also derives `preferred_term_key` via
`nptc_shared.similarity.collision_key`, so there is no code path that can
set one without the other - a stored, indexed column rather than a
per-save recomputation, matching `Designation.term_key`'s own treatment.
It is deliberately not `Designation.length`'s "bare property, no column"
pattern: FR-05 detection needs an indexed equality lookup across
`catalogue_entry`, and `nptc.catalogue.collisions` is the only reader.
Never independently meaningful once `preferred_term` is set, so it is
`__audit_ignored__`, matching `row_version`'s own treatment.
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


#: Plain string literal, never built from `CatalogueEntryStatus` -
#: `test_sql_parameterisation.py`'s AST guard forbids SQL built from
#: runtime data, and there is no runtime data here to justify the risk.
_STATUS_CHECK_SQL = "status IN ('draft','active','deprecated','withdrawn')"

#: `6,` (not `6`) so the format survives a catalogue passing 999,999
#: entries without a migration - matched against
#: `nptc.catalogue.entries.BUSINESS_KEY_PATTERN`, the single Python source
#: of truth this constraint mirrors.
_BUSINESS_KEY_CHECK_SQL = "business_key ~ '^NPTC-[0-9]{6,}$'"


class ImmutableFieldError(RuntimeError):
    """Raised by the `business_key` `@validates` guard below when anything
    other than the initial assignment tries to change it - see the module
    docstring's "two independent layers" note. This is the fail-loud
    Python-level layer; `nptc.db.roles.GRANT_CATALOGUE_ENTRY_UPDATE_SQL`'s
    column exclusion is the actual database invariant."""


class CatalogueEntry(Base):
    __tablename__ = "catalogue_entry"

    # nptc.audit.policy (issue #37, NFR-08): every real column must be
    # classified. `business_key` is auditable (not ignored) so a CREATED
    # event records the minted key - it can never appear in an UPDATE diff
    # because it is immutable (see the module docstring), so classifying it
    # here costs nothing on the update path. `row_version` is ignored: it
    # is bookkeeping for FR-38, never itself a "changed field" a reviewer
    # would want to see, exactly like `User.id`/`created_at`/`updated_at`.
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
        # FR-05, issue #49: an indexed lookup for a cross-entry collision -
        # `nptc.catalogue.collisions` filters by `status` in the query
        # itself, so a plain btree (not partial) index is sufficient here,
        # matching `Designation.term_key`'s own treatment.
        Index("ix_catalogue_entry_preferred_term_key", "preferred_term_key"),
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
    # FR-05/issue #49: derived from `preferred_term` by the same
    # `@validates` hook below - never independently assignable through the
    # ORM. Indexed (see the migration) so `nptc.catalogue.collisions` can
    # look up a collision by equality rather than scanning every entry.
    # `server_default=''` exists only so a raw INSERT that bypasses the ORM
    # (every `backend/tests/test_db_*.py` constraint/privilege test) still
    # satisfies `NOT NULL` - see `Designation.term_key`'s identical comment.
    preferred_term_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # A quoted literal, not the bare `CatalogueEntryStatus.DRAFT` value -
    # matches `app_user.status`'s own precedent (an unquoted server_default
    # string is rendered verbatim as SQL, and `DEFAULT draft` with no
    # quotes is not valid DDL for a text column).
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
    # FR-38: bumped by SQLAlchemy's own version_id_col machinery on every
    # mapped UPDATE - see the module docstring for why nothing else is ever
    # allowed to touch it.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # Must follow the `row_version` column definition above: this binds
    # SQLAlchemy's optimistic-concurrency machinery to the just-defined
    # `MappedColumn`, which the declarative process resolves once mapping
    # completes - defining it earlier in the class body (before the name
    # `row_version` exists) is a `NameError`. Deliberately no `ClassVar`
    # annotation: mypy treats the base class's own `__mapper_args__` as an
    # instance variable and flags a `ClassVar`-annotated override here,
    # even though this is SQLAlchemy's own documented pattern for
    # `version_id_col`. The bare assignment satisfies mypy; the `noqa`
    # below silences the ruff mutable-default-value lint that a bare dict
    # literal class attribute would otherwise trigger.
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
