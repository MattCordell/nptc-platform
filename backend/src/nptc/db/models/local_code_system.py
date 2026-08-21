"""The `local_code_system` table: a governed vocabulary the platform owns
outright because SNOMED CT has no concept for it (issue #56, FR-90). See
PRD SS6.6.

**Why this exists at all.** PRD SS6.6 verifies that RCPA's `Discipline`
values cannot be expressed as a single coherent SNOMED value set: three of
the six disciplines match `<394595002` exactly, `Microbiology` is
ambiguous between two candidates, and `Molecular`/`Serology` have no match
in the specialty hierarchy at all - their nearest neighbours
(`708179009`/`708188000`) are healthcare *service* concepts, confirmed
not-subsumed by `check_subsumption`. `Subgroup` has never been governed at
all (FR-92). FR-90 answers both by making each column a code system the
platform itself governs, "owned by RCPA-QAP" - `owner` below records that
fact rather than assuming it.

**Not a value set.** `code_binding.py`'s `system` column names a SNOMED CT
edition served by Ontoserver; a row here is the platform's own record,
validated internally (PRD line 415: "Local code systems ... are validated
internally against the platform's own `LocalCode` table, because Ontoserver
does not hold them"). `nptc.registry.handlers.LocalCodeLookup` is the read
path a `binding_target = 'local_code_system'` property (ADR-0013) will use
once #53 wires it up - this module only owns storage.

**Version history is deferred, not silently dropped.** FR-90's third
bullet asks for "version history tied to catalogue releases", but
`release` does not exist yet (`nptc.releases` is a P4 stub - see
`docs/architecture/data-model.md`'s "Property registry" section for the
same kind of forward reference). `status`/`deprecated_at` on `local_code`
plus the NFR-08 audit trail this module writes through make past state
reconstructable in the meantime; pinning a code's state to a specific
release is filed as a follow-up rather than attempted here, matching
`designation_collision_acknowledgement`'s own "narrow table now, expected
to be subsumed later" precedent.

**`key` follows `property_definition.key`'s exact pattern** (ADR-0012),
deliberately - `discipline` and `subgroup` are conceptually registry
properties (PRD line 436: both are `origin = system` properties, seeded at
install) even though `nptc.registry` itself is still a stub, so the two
vocabularies cannot drift into incompatible naming rules later.

**Never deleted, only deprecated** - the same "retire, don't remove"
discipline every other governed table in this schema uses
(`nptc.db.roles.REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL`).
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

#: Matches `property_definition.key`'s own pattern (ADR-0012) - see the
#: module docstring for why the two vocabularies are kept in lockstep.
#: Exported (not `_`-prefixed) so `nptc.catalogue.local_codes.
#: create_local_code_system` can validate a candidate key in Python before
#: it ever reaches `_KEY_CHECK_SQL` below - built from `KEY_PATTERN.
#: pattern`, matching `designation.py`'s own `_LANGUAGE_CHECK_SQL`/
#: `LANGUAGE_TAG_PATTERN` precedent, so the two can never silently diverge.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class LocalCodeSystemStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


#: Plain string literals, never built from the `StrEnum` above - matches
#: every other model's own precedent, enforced by
#: `test_sql_parameterisation.py`'s AST guard.
_KEY_CHECK_SQL = f"key ~ '{KEY_PATTERN.pattern}'"
_URI_NOT_BLANK_SQL = "length(btrim(uri)) > 0"
_TITLE_NOT_BLANK_SQL = "length(btrim(title)) > 0"
_OWNER_NOT_BLANK_SQL = "length(btrim(owner)) > 0"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"


class LocalCodeSystem(Base):
    __tablename__ = "local_code_system"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
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
        """A code system's `key` is never renamed - `nptc.db.roles.
        GRANT_LOCAL_CODE_SYSTEM_UPDATE_SQL`'s column exclusion is the
        actual database invariant; this is the fail-loud Python-level
        layer, matching `CatalogueEntry._validate_business_key_immutable`."""
        if "key" in self.__dict__ and self.__dict__["key"] is not None:
            raise ImmutableFieldError(
                f"LocalCodeSystem.key is immutable and cannot be reassigned "
                f"from {self.__dict__['key']!r} to {value!r}"
            )
        return value
