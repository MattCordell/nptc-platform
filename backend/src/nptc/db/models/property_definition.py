"""The `property_definition` table: the property registry's storage
envelope (issue #51, FR-09, FR-10). See ADR-0012 for the full design
record - this model implements that ADR's fixed schema, it does not choose
one.

**A conventional, fully-constrained relational table, not a document.**
`datatype` is plain `TEXT` with no `CHECK` and no Postgres `ENUM` - FR-77's
extension point, so admitting a new datatype never touches this table.
`cardinality`, `scope`, `origin`, `status` and the binding fields are
closed, stable vocabularies and get named `CHECK`s instead.

**FR-10's binding is four real columns, not a JSONB sub-document**, with a
`CHECK` making a code-without-a-binding unrepresentable: see
`_BINDING_REQUIRED_CHECK_SQL` below. Open-ended per-datatype parameters (a
string's max length, a decimal's range) go in the handler-owned
`constraints` JSONB column instead - this model only reserves the column;
issue #137's ADR owns its interior.

**`row_version` is owned by exactly one write path**: SQLAlchemy's
mapper-level optimistic concurrency (`version_id_col`) on this model's
mapped `UPDATE` - never a migration, a manual bump, or a database trigger
(PRD Section 14.1). `backend/tests/test_sql_parameterisation.py`'s
`VERSIONED_TABLE_MODELS` guard covers this table for exactly that reason.

**`key` is immutable and the table has no `DELETE` grant at all** -
`nptc.db.roles.GRANT_PROPERTY_DEFINITION_UPDATE_SQL`/
`REVOKE_PROPERTY_DEFINITION_DELETE_SQL` make FR-11/FR-12 privilege-level
invariants, not application conventions, matching `catalogue_entry.
business_key`'s own precedent. The `@validates("key")` guard below is a
second, fail-loud Python-level layer.

**`status`/`deprecated_at` are linked by a `CHECK`** so a deprecated
definition with no recorded timestamp (or an active one with a stale one)
cannot exist - the same make-it-unrepresentable trick the binding `CHECK`
uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = [
    "BindingStrength",
    "BindingTarget",
    "PropertyCardinality",
    "PropertyDefinition",
    "PropertyOrigin",
    "PropertyScope",
    "PropertyStatus",
]


class PropertyCardinality(StrEnum):
    ZERO_OR_ONE = "0..1"
    ONE = "1..1"
    ZERO_OR_MANY = "0..*"
    ONE_OR_MANY = "1..*"


class PropertyScope(StrEnum):
    SUBMISSION = "submission"
    MAINTENANCE = "maintenance"
    BOTH = "both"


class PropertyOrigin(StrEnum):
    SYSTEM = "system"
    ADMIN = "admin"


class PropertyStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class BindingTarget(StrEnum):
    VALUE_SET = "value_set"
    LOCAL_CODE_SYSTEM = "local_code_system"


class BindingStrength(StrEnum):
    REQUIRED = "required"
    EXTENSIBLE = "extensible"
    EXAMPLE = "example"


#: Plain string literals, never built from the `StrEnum`s above -
#: `test_sql_parameterisation.py`'s AST guard forbids SQL built from
#: runtime data, matching every other model's own precedent.
_KEY_CHECK_SQL = "key ~ '^[a-z][a-z0-9_]{0,62}$'"
_CARDINALITY_CHECK_SQL = "cardinality IN ('0..1','1..1','0..*','1..*')"
_SCOPE_CHECK_SQL = "scope IN ('submission','maintenance','both')"
_ORIGIN_CHECK_SQL = "origin IN ('system','admin')"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"
_BINDING_TARGET_CHECK_SQL = "binding_target IN ('value_set','local_code_system')"
_STRENGTH_CHECK_SQL = "strength IN ('required','extensible','example')"
#: Makes "a code datatype always has a binding" a schema invariant (ADR-0012).
#: Only sound because `datatype` is `NOT NULL` - a nullable `datatype` would
#: let Postgres treat the comparison as `NULL`, which a `CHECK` treats as a
#: pass, silently reopening the hole this constraint exists to close.
_BINDING_REQUIRED_CHECK_SQL = "(datatype = 'code') = (binding_target IS NOT NULL)"
#: `IS DISTINCT FROM`, not `<>` - a `NULL` `binding_target` (any non-`code`
#: property) must not make the comparison itself `NULL` and mask a real
#: violation (ADR-0012).
_VALUE_SET_URI_REQUIRED_CHECK_SQL = (
    "binding_target IS DISTINCT FROM 'value_set' OR value_set_uri IS NOT NULL"
)
_DEPRECATED_AT_CHECK_SQL = "(status = 'deprecated') = (deprecated_at IS NOT NULL)"


class PropertyDefinition(Base):
    __tablename__ = "property_definition"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "key",
            "label",
            "datatype",
            "cardinality",
            "scope",
            "required_for_submission",
            "required_for_publication",
            "binding_target",
            "value_set_uri",
            "strength",
            "edition",
            "filterable",
            "origin",
            "status",
            "display_order",
            "constraints",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "index_seq", "created_at", "updated_at", "row_version", "deprecated_at"}
    )

    __table_args__ = (
        CheckConstraint(_KEY_CHECK_SQL, name="key"),
        CheckConstraint(_CARDINALITY_CHECK_SQL, name="cardinality"),
        CheckConstraint(_SCOPE_CHECK_SQL, name="scope"),
        CheckConstraint(_ORIGIN_CHECK_SQL, name="origin"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_BINDING_TARGET_CHECK_SQL, name="binding_target"),
        CheckConstraint(_STRENGTH_CHECK_SQL, name="strength"),
        CheckConstraint(_BINDING_REQUIRED_CHECK_SQL, name="binding_required_for_code"),
        CheckConstraint(_VALUE_SET_URI_REQUIRED_CHECK_SQL, name="value_set_uri_required"),
        CheckConstraint(_DEPRECATED_AT_CHECK_SQL, name="deprecated_at_required"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Identity, not a `serial` default, mirroring `audit_event.sequence` -
    # an identity column's backing sequence isn't ACL-checked against the
    # inserting role, so `INSERT` on the table alone suffices (ADR-0012).
    # Used by #54's generated index names (`ix_propval_p{index_seq}_{slot}`)
    # to avoid ever embedding `key` in an identifier.
    index_seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, active_history=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    datatype: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    cardinality: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    required_for_submission: Mapped[bool] = mapped_column(
        Boolean, nullable=False, active_history=True
    )
    required_for_publication: Mapped[bool] = mapped_column(
        Boolean, nullable=False, active_history=True
    )
    binding_target: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    value_set_uri: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    strength: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    edition: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, active_history=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, active_history=True)
    # Handler-owned (issue #137's ADR); this model only reserves the column.
    constraints: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), active_history=True
    )
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # Must follow the `row_version` column definition above - see
    # `CatalogueEntry`'s identical placement note.
    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012

    @validates("key")
    def _validate_key_immutable(self, _key: str, value: str) -> str:
        if "key" in self.__dict__ and self.__dict__["key"] is not None:
            raise ImmutableFieldError(
                "PropertyDefinition.key is immutable (FR-12) and cannot be "
                f"reassigned from {self.__dict__['key']!r} to {value!r}"
            )
        return value
