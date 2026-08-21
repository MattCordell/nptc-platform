"""The `local_code_snomed_map` table: an advisory, non-authoritative map
from a `local_code` to a SNOMED CT concept (issue #56, FR-91). See PRD
SS6.6.

**Never a `code_binding`, structurally.** `code_binding` binds a
*catalogue entry* to the code the terminology server actually serves for
it (FR-06/FR-08/FR-82) - a `code_binding` row is authoritative, revalidated
by the FR-45 sweep, and the acceptance criterion for this issue is that
the sweep must never treat a row here the same way. There is no
`entry_id` anywhere on this table, and no foreign key to
`catalogue_entry` at all - a change that starts joining this table into
sweep logic would have to invent that join from nothing, which is the
point. `backend/tests/test_registry_local_codes.py` pins this with an AST
guard: no module under `nptc.validation` or `nptc.catalogue.bindings` may
reference `LocalCodeSnomedMap`.

**Advisory in three independent, structural ways, not by convention:**
`match_strength` has no counterpart in `code_binding` at all, so a row
read out of context still announces what kind of claim it is making;
`advisory_note` is mandatory, never optional, so every row explains its
own caveat; and there is deliberately **no row** for `Molecular` or
`Serology` - PRD SS6.6's verification found no SNOMED concept that is a
genuine match for either (the nearest candidates, `1236877003` and
`708179009`/`708188000`, are a different discipline and healthcare
*service* concepts respectively, confirmed not-subsumed by
`check_subsumption`), and FR-91 requires that gap to "stay visible" rather
than be papered over with a plausible-looking wrong mapping. An absent row
is the honest representation of "no match exists", exactly as
`code_binding.py`'s FR-82 note treats an untransformed served label - the
absence itself carries the meaning.

**No uniqueness constraint on `local_code_id`.** PRD SS6.6's own
verification table records `Microbiology` as genuinely ambiguous between
two SNOMED candidates (`408454008` \\|Clinical microbiology\\| and
`394820005` \\|Medical microbiology\\|, neither named plainly
"Microbiology"). Collapsing that to one row would be exactly the kind of
approximation FR-91 forbids, so a local code may have zero, one, or
several map rows, each with its own `match_strength`.

**`code`/`system` reuse `code_binding`'s own validation, not a copy of
it** - `nptc_sctid_is_valid` (`nptc.db.functions`, issue #48/ADR-0023) is
the same database function, so a SNOMED identifier here is held to the
same format-and-Verhoeff standard as a real binding even though this row
is never itself revalidated by the sweep.

**Never edited, only replaced.** A map row records a point-in-time
editorial judgement about which concept is the nearest analogue - like
`designation_collision_acknowledgement`, there is no update path; a
revised mapping is a new row, and the sweep-exclusion guard above means an
old row growing stale carries no safety consequence the way a stale
`code_binding` would."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = ["LocalCodeSnomedMap", "SnomedMapMatchStrength"]


class SnomedMapMatchStrength(StrEnum):
    EXACT = "exact"
    NARROWER = "narrower"
    BROADER = "broader"
    AMBIGUOUS = "ambiguous"


#: Plain string literals, never built from the `StrEnum` above - matches
#: `code_binding.py`'s own precedent, enforced by
#: `test_sql_parameterisation.py`'s AST guard.
_SYSTEM_NOT_BLANK_SQL = "length(btrim(system)) > 0"
_CODE_CHECK_SQL = "nptc_sctid_is_valid(code)"
_DISPLAY_NOT_BLANK_SQL = "length(btrim(display)) > 0"
_MATCH_STRENGTH_CHECK_SQL = "match_strength IN ('exact','narrower','broader','ambiguous')"
_ADVISORY_NOTE_NOT_BLANK_SQL = "length(btrim(advisory_note)) > 0"


class LocalCodeSnomedMap(Base):
    __tablename__ = "local_code_snomed_map"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"local_code_id", "system", "code", "display", "match_strength", "advisory_note"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_SYSTEM_NOT_BLANK_SQL, name="system_not_blank"),
        CheckConstraint(_CODE_CHECK_SQL, name="code"),
        CheckConstraint(_DISPLAY_NOT_BLANK_SQL, name="display_not_blank"),
        CheckConstraint(_MATCH_STRENGTH_CHECK_SQL, name="match_strength"),
        CheckConstraint(_ADVISORY_NOTE_NOT_BLANK_SQL, name="advisory_note_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    local_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_code.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'http://snomed.info/sct'"),
        active_history=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    display: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    match_strength: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    advisory_note: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # `onupdate` is unreachable in practice - `REVOKE_LOCAL_CODE_SNOMED_MAP_
    # WRITE_SQL` revokes UPDATE outright, and the module docstring's "never
    # edited, only replaced" is the actual policy. Kept anyway, rather than
    # a bare `created_at`-only shape, so this table's column set matches
    # every sibling table's (`created_at`/`updated_at` are always a pair in
    # this schema) - a future privilege change would not also have to
    # remember to add the column back.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("local_code_id")
    def _validate_local_code_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A revised mapping is a new row, never a reparented one - see
        the module docstring's "never edited, only replaced"."""
        if "local_code_id" in self.__dict__ and self.__dict__["local_code_id"] is not None:
            raise ImmutableFieldError(
                "LocalCodeSnomedMap.local_code_id is immutable and cannot be reassigned "
                f"from {self.__dict__['local_code_id']!r} to {value!r}"
            )
        return value
