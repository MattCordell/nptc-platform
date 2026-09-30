"""The `property_value` table: one row per value, keyed by `(entry_id, property_key, ordinal)`
(FR-09, FR-10). ADR-0012 records the design.

**One row per value, not a JSON array column.** `(entry_id, property_key, ordinal)` is the primary
key: it is what every write and FK needs to address a value by, and it subsumes the uniqueness this
table requires, so there is no surrogate id.

**The FK targets `property_definition(key)`, not a surrogate id**, because FR-12 rules out the usual
objection to a natural key (that it might change). The FK is a secondary backstop: it blocks
deleting or renaming a `property_definition` row while a dependent value exists. The column-level
grants in `nptc.db.roles` are what make FR-11 and FR-12 unconditional.

**`ordinal` is zero-based.** Its uniqueness through the PK closes only the trivial race of two
inserts landing on one slot. It does not enforce cardinality's upper bound; validation does.

**`justification` supports FR-10's extensible-strength case**: a coded value bound to an
`extensible` value set may carry free text explaining an out-of-valueset choice.

**`value` is plain `JSONB`, not wrapped in `sqlalchemy.ext.mutable`**: an in-place mutation
(`instance.value["x"] = 1`) is invisible to the unit of work and does not persist. Every write MUST
replace the whole attribute (`instance.value = {**instance.value, "x": 1}`).
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nptc.db.base import Base

__all__ = ["PropertyValue"]

#: A plain string literal: `test_sql_parameterisation.py`'s AST guard forbids SQL built from runtime
#: data.
_ORDINAL_CHECK_SQL = "ordinal >= 0"


class PropertyValue(Base):
    __tablename__ = "property_value"

    # nptc.audit.policy (NFR-08): every real column classified.
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
        # `property_key` is only the second column of the composite PK, so the PK gives it no index
        # of its own. Without this index, FK maintenance on `property_definition` and the
        # deprecation workflow's "which entries use this property" lookup would be sequential scans.
        Index("ix_property_value_property_key", "property_key"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        primary_key=True,
        active_history=True,
    )
    property_key: Mapped[str] = mapped_column(Text, primary_key=True, active_history=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True, active_history=True)
    # No `| None`: the column is `NOT NULL`, and SQLAlchemy's JSONB serialises Python `None` as SQL
    # `NULL` (not `'null'::jsonb`), so a `None` would pass `mypy --strict` and then fail at flush. A
    # property with no value is an absent row (FR-09), never `None`. `int` is listed beside `float`
    # because the column holds arbitrary JSON scalars and mypy accepts `int` only through
    # numeric-tower promotion.
    value: Mapped[dict[str, object] | list[object] | str | int | float | bool] = mapped_column(
        JSONB, nullable=False, active_history=True
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
