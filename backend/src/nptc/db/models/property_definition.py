"""The `property_definition` table: the property registry's storage envelope (FR-09, FR-10).
ADR-0012 records the design; this model implements its fixed schema.

**A conventional, fully-constrained relational table, not a document.** `datatype` is plain `TEXT`
with no `CHECK` and no Postgres `ENUM`, because FR-77 makes a new datatype an extension point that
must not touch this table. `cardinality`, `scope`, `origin`, `status` and the binding fields are
closed vocabularies and get named `CHECK`s.

**FR-10's binding is four real columns, not a JSONB sub-document**, with
`_BINDING_REQUIRED_CHECK_SQL` making a code without a binding unrepresentable. Open-ended
per-datatype parameters (a string's max length, a decimal's range) go in the handler-owned
`constraints` JSONB column. This model only reserves that column; each handler's
`constraints_schema()` owns its interior (ADR-0013).

**`row_version` has exactly one write path**: SQLAlchemy's mapper-level optimistic concurrency
(`version_id_col`) on the mapped `UPDATE`, never a migration, manual bump or trigger (PRD Section
14.1). `test_sql_parameterisation.py`'s `VERSIONED_TABLE_MODELS` guard covers this table.

**`key` is immutable and the table has no `DELETE` grant.**
`nptc.db.roles.GRANT_PROPERTY_DEFINITION_UPDATE_SQL` and `REVOKE_PROPERTY_DEFINITION_DELETE_SQL`
make FR-11 and FR-12 privilege-level invariants, as for `catalogue_entry.business_key`. The
`@validates("key")` guard is a second, fail-loud Python layer.

**`status` and `deprecated_at` are linked by a `CHECK`**, so a deprecated definition without a
timestamp, or an active one with a stale one, cannot exist.

**`constraints` is plain `JSONB`, not wrapped in `sqlalchemy.ext.mutable`**: an in-place mutation
(`definition.constraints["max"] = 5`) is invisible to the unit of work and does not persist. Handler
code MUST replace the whole attribute (`definition.constraints = {**definition.constraints, "max":
5}`).
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
    ForeignKeyConstraint,
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


#: Plain literals, never built from the `StrEnum`s above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_KEY_CHECK_SQL = "key ~ '^[a-z][a-z0-9_]{0,62}$'"
_CARDINALITY_CHECK_SQL = "cardinality IN ('0..1','1..1','0..*','1..*')"
_SCOPE_CHECK_SQL = "scope IN ('submission','maintenance','both')"
_ORIGIN_CHECK_SQL = "origin IN ('system','admin')"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"
_BINDING_TARGET_CHECK_SQL = "binding_target IN ('value_set','local_code_system')"
_STRENGTH_CHECK_SQL = "strength IN ('required','extensible','example')"
#: Makes "a code datatype always has a binding" a schema invariant (ADR-0012). Sound only because
#: `datatype` is `NOT NULL`: a nullable one would make the comparison `NULL`, which a `CHECK` treats
#: as a pass.
_BINDING_REQUIRED_CHECK_SQL = "(datatype = 'code') = (binding_target IS NOT NULL)"
#: `IS DISTINCT FROM`, not `<>`, so a `NULL` `binding_target` (any non-`code` property) cannot make
#: the comparison `NULL` and mask a violation (ADR-0012).
_VALUE_SET_URI_REQUIRED_CHECK_SQL = (
    "binding_target IS DISTINCT FROM 'value_set' OR value_set_uri IS NOT NULL"
)
#: The binding CHECKs above close one direction: a code property without a binding. This closes the
#: other: every binding field is `NULL` whenever there is no `binding_target`, so a non-`code`
#: property carries no stray binding data.
_BINDING_FIELDS_REQUIRE_TARGET_CHECK_SQL = (
    "binding_target IS NOT NULL OR (value_set_uri IS NULL AND strength IS NULL "
    "AND edition IS NULL AND local_code_system_key IS NULL)"
)
_DEPRECATED_AT_CHECK_SQL = "(status = 'deprecated') = (deprecated_at IS NOT NULL)"
#: The same `IS DISTINCT FROM` shape (FR-10): names which governed `local_code_system` a
#: `binding_target = 'local_code_system'` property is bound to.
_LOCAL_CODE_SYSTEM_KEY_REQUIRED_CHECK_SQL = (
    "binding_target IS DISTINCT FROM 'local_code_system' OR local_code_system_key IS NOT NULL"
)


class PropertyDefinition(Base):
    __tablename__ = "property_definition"

    # nptc.audit.policy (NFR-08): every real column classified.
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
            "local_code_system_key",
            "filterable",
            "origin",
            "status",
            "display_order",
            "constraints",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    # `deprecated_at` is ignored: the `status` diff already shows the deprecation, so the timestamp
    # is bookkeeping that `_DEPRECATED_AT_CHECK_SQL` ties to `status`, like `row_version` and
    # `created_at`.
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
        CheckConstraint(
            _LOCAL_CODE_SYSTEM_KEY_REQUIRED_CHECK_SQL, name="local_code_system_key_required"
        ),
        CheckConstraint(
            _BINDING_FIELDS_REQUIRE_TARGET_CHECK_SQL, name="binding_fields_require_target"
        ),
        CheckConstraint(_DEPRECATED_AT_CHECK_SQL, name="deprecated_at_required"),
        ForeignKeyConstraint(
            ["local_code_system_key"],
            ["local_code_system.key"],
            name="local_code_system_key_local_code_system",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Identity, not a `serial` default, as for `audit_event.sequence`: an identity column's sequence
    # is not ACL-checked against the inserting role (ADR-0012). Generated index names
    # (`ix_propval_p{index_seq}_{slot}`) use it so `key` is never embedded in an identifier.
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
    # FR-10: names the governed `local_code_system` a `binding_target = 'local_code_system'`
    # property is bound to. A real FK, now that `local_code_system` exists.
    local_code_system_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, active_history=True
    )
    filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, active_history=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, active_history=True)
    # Handler-owned (ADR-0013); this model only reserves the column.
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

    # Must follow the `row_version` column; see `CatalogueEntry`'s identical placement.
    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012

    @validates("key")
    def _validate_key_immutable(self, _key: str, value: str) -> str:
        if "key" in self.__dict__ and self.__dict__["key"] is not None:
            raise ImmutableFieldError(
                "PropertyDefinition.key is immutable (FR-12) and cannot be "
                f"reassigned from {self.__dict__['key']!r} to {value!r}"
            )
        return value
