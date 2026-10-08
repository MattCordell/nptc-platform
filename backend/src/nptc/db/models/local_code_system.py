"""The `local_code_system` table: a governed vocabulary the platform owns because SNOMED CT has no
concept for it (FR-90). See PRD SS6.6.

**Why this exists.** PRD SS6.6 verifies that RCPA's `Discipline` values cannot form one coherent
SNOMED value set. Three of the six disciplines match `<394595002` exactly, `Microbiology` is
ambiguous between two candidates, and `Molecular` and `Serology` have no match in the specialty
hierarchy: their nearest neighbours (`708179009`/`708188000`) are healthcare *service* concepts,
confirmed not subsumed by `check_subsumption`. `Subgroup` has never been governed at all (FR-92).
FR-90 answers both by making each column a code system the platform governs, "owned by RCPA-QAP";
`owner` records that fact rather than assuming it.

**Not a value set.** `code_binding.system` names a SNOMED CT edition served by Ontoserver. A row
here is the platform's own record, validated internally against `LocalCode` because Ontoserver does
not hold it (PRD line 415). `nptc.registry.handlers.LocalCodeLookup` is the read path for a
`binding_target = 'local_code_system'` property (ADR-0013); this module owns storage only.

**Version history is deferred.** FR-90's "version history tied to catalogue releases" needs
`release`, which does not exist yet (`nptc.releases` is a P4 stub). `status` and `deprecated_at` on
`local_code`, plus the NFR-08 audit trail, make past state reconstructable meanwhile.

**`key` follows `property_definition.key`'s pattern** (ADR-0012). `discipline` and `subgroup` are
`origin = system` registry properties (PRD line 436), so the two vocabularies share naming rules and
cannot drift apart.

**Never deleted, only deprecated** (`nptc.db.roles.REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL`), like every
other governed table in this schema.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = ["KEY_PATTERN", "LocalCodeSystem", "LocalCodeSystemStatus"]

#: Matches `property_definition.key`'s pattern (ADR-0012). Exported so
#: `nptc.catalogue.local_codes.create_local_code_system` can validate a key in Python before it
#: reaches `_KEY_CHECK_SQL`, which is built from `KEY_PATTERN.pattern` so the two cannot diverge.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class LocalCodeSystemStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


#: Plain literals, never built from the `StrEnum` above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_KEY_CHECK_SQL = f"key ~ '{KEY_PATTERN.pattern}'"
_URI_NOT_BLANK_SQL = "length(btrim(uri)) > 0"
_TITLE_NOT_BLANK_SQL = "length(btrim(title)) > 0"
_OWNER_NOT_BLANK_SQL = "length(btrim(owner)) > 0"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"


class LocalCodeSystem(Base):
    __tablename__ = "local_code_system"

    # nptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"key", "uri", "title", "description", "owner", "status"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_KEY_CHECK_SQL, name="key"),
        CheckConstraint(_URI_NOT_BLANK_SQL, name="uri_not_blank"),
        CheckConstraint(_TITLE_NOT_BLANK_SQL, name="title_not_blank"),
        CheckConstraint(_OWNER_NOT_BLANK_SQL, name="owner_not_blank"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, active_history=True)
    uri: Mapped[str] = mapped_column(Text, unique=True, nullable=False, active_history=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("key")
    def _validate_key_immutable(self, _key: str, value: str) -> str:
        """A code system's `key` is never renamed.
        `nptc.db.roles.GRANT_LOCAL_CODE_SYSTEM_UPDATE_SQL`'s column exclusion is the database
        invariant; this is the fail-loud Python layer.
        """
        if "key" in self.__dict__ and self.__dict__["key"] is not None:
            raise ImmutableFieldError(
                f"LocalCodeSystem.key is immutable and cannot be reassigned "
                f"from {self.__dict__['key']!r} to {value!r}"
            )
        return value
