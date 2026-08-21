"""The `property_value` table: one row per value, keyed by
`(entry_id, property_key, ordinal)` (issue #51, FR-09, FR-10). See
ADR-0012 for the full design record.

**One row per value, not a JSON array column.** `(entry_id, property_key,
ordinal)` is the primary key - it is already exactly what every write and
every FK needs to address a value by, and a PK subsumes the uniqueness
this table requires; no separate surrogate id.

**The FK targets `property_definition(key)`, not a surrogate id** - FR-12
already rules out the usual objection to a natural key (that it might
change). That FK is a secondary backstop only: it blocks deleting or
renaming a `property_definition` row *while a dependent value exists*, not
the mechanism that makes FR-11/FR-12 unconditional (the column-level
privilege grants in `nptc.db.roles`).

**`ordinal` is zero-based** (the first value of a multi-valued property is
`ordinal = 0`). Its uniqueness (via the PK) closes only the trivial race -
two inserts cannot land on the same slot - it does **not** enforce
cardinality's upper bound; that is issue #52's job at validation time.

**`justification` supports FR-10's extensible-strength case** - a coded
value bound to an `extensible` value set may carry free text explaining an
out-of-valueset choice.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nptc.db.base import Base

__all__ = ["PropertyValue"]

#: Plain string literal, never built from runtime data -
#: `test_sql_parameterisation.py`'s AST guard, matching every other
#: model's own precedent.
_ORDINAL_CHECK_SQL = "ordinal >= 0"


class PropertyValue(Base):
    __tablename__ = "property_value"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"entry_id", "property_key", "ordinal", "value", "justification"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset()

    __table_args__ = (
        CheckConstraint(_ORDINAL_CHECK_SQL, name="ordinal_non_negative"),
        ForeignKeyConstraint(
            ["property_key"],
            ["property_definition.key"],
            name="property_key_property_definition",
        ),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        primary_key=True,
        active_history=True,
    )
    property_key: Mapped[str] = mapped_column(Text, primary_key=True, active_history=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True, active_history=True)
    value: Mapped[dict[str, object] | list[object] | str | float | bool | None] = mapped_column(
        JSONB, nullable=False, active_history=True
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
