"""The `submission` table: a proposal from a user to add or change a test (FR-23, FR-27, FR-28).

A submission is its own record, not a `catalogue_entry` with another status (FR-29). Nothing here
is an entry until a later workflow step copies it into one, so a submitted value is never mistaken
for catalogue content.

**Immutable after insert, except `state`.** `nptc.db.roles.GRANT_SUBMISSION_UPDATE_SQL` leaves every
content column out of the `UPDATE` grant, so what the submitter sent is a privilege-level fact, not
an application convention. `row_version` sits inside the grant because `version_id_col` writes it
on every mapped update, as on `catalogue_entry`. There is no `DELETE` grant: a submission leaves the
workflow through `state`.

**`snomed_code` is `TEXT`, and `snomed_fsn` comes from the terminology server (FR-06, FR-82).** The
code passes `nptc_sctid_is_valid` (ADR-0023), and the two columns are both present or both absent,
so a stored code always carries the label the server returned. A request never supplies the FSN.

**`property_values` and `synonyms` are plain `JSONB`, not wrapped in `sqlalchemy.ext.mutable`**: an
in-place mutation is invisible to the unit of work and does not persist, so a write must replace the
whole attribute. `property_values` is keyed by property key. The `CHECK` constraints pin each
column's JSON type so a malformed document never reaches a reader.

**The reference link is stored with its check (FR-27).** `reference_url` is what the submitter
sent, `reference_checked_at` is when the server fetched it and `reference_status` is the final
HTTP status. A new test must carry all three. The check runs before the row is written, so a
stored link has always passed.

**A confirmed duplicate is stored with what the server found (FR-25).** `duplicate_matches` holds
the matches the server found when it saved the row, never a list the caller sent. It can include a
match the submitter was never shown, because a request may confirm without a prior 409 and a new
match can appear between the 409 and the resend. `duplicate_confirmed_at` is the database time of
the save, the same value as `created_at`, and says the request carried a confirmation. A submission
that matched nothing has an empty array and no time.

**`organisation` is the submitter's own copy** of the profile value, pre-filled and editable.
Closing an account clears the profile (NFR-17); the copy stays, and the audit policy withholds it
as it does on `app_user`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["CLOSED_SUBMISSION_STATES", "Submission", "SubmissionKind", "SubmissionState"]


class SubmissionKind(StrEnum):
    NEW_TEST = "new_test"
    AMENDMENT = "amendment"


class SubmissionState(StrEnum):
    SUBMITTED = "Submitted"


#: The states in which a submission no longer counts as an open proposal. "Open" is every other
#: state, so a state added later is open until it is listed here. The three are the end of the
#: submission workflow (FR-28): published, refused or taken back by the submitter. Plain strings,
#: because `SubmissionState` does not yet name them and the `state` check allows only `Submitted`.
CLOSED_SUBMISSION_STATES: Final[tuple[str, ...]] = ("Published in release", "Rejected", "Withdrawn")


#: Plain literals, never built from the `StrEnum`s above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_KIND_CHECK_SQL = "kind IN ('new_test','amendment')"
_STATE_CHECK_SQL = "state IN ('Submitted')"
_PREFERRED_TERM_NOT_BLANK_SQL = "length(btrim(preferred_term)) > 0"
_SYNONYMS_IS_ARRAY_SQL = "jsonb_typeof(synonyms) = 'array'"
_PROPERTY_VALUES_IS_OBJECT_SQL = "jsonb_typeof(property_values) = 'object'"
#: The database half of FR-06. `nptc_sctid_is_valid` is `STRICT`, so a NULL code passes, which is
#: right because the code is optional.
_SNOMED_CODE_CHECK_SQL = "nptc_sctid_is_valid(snomed_code)"
_SNOMED_FSN_NOT_BLANK_SQL = "snomed_fsn IS NULL OR length(btrim(snomed_fsn)) > 0"
_SNOMED_CODE_WITH_FSN_SQL = "(snomed_code IS NULL) = (snomed_fsn IS NULL)"
_NOTES_NOT_BLANK_SQL = "notes IS NULL OR length(btrim(notes)) > 0"
_REFERENCE_URL_NOT_BLANK_SQL = "reference_url IS NULL OR length(btrim(reference_url)) > 0"
#: The three columns describe one check, so they are all present or all absent. A new test must
#: carry one (FR-27); an amendment may leave it out.
_REFERENCE_CHECK_COMPLETE_SQL = (
    "(reference_url IS NULL) = (reference_checked_at IS NULL) "
    "AND (reference_url IS NULL) = (reference_status IS NULL)"
)
_NEW_TEST_HAS_REFERENCE_SQL = "kind <> 'new_test' OR reference_url IS NOT NULL"
_DUPLICATE_MATCHES_IS_ARRAY_SQL = "jsonb_typeof(duplicate_matches) = 'array'"
#: A confirmation time exists exactly when there is something to confirm.
_DUPLICATE_CONFIRMATION_COMPLETE_SQL = (
    "(duplicate_confirmed_at IS NULL) = (duplicate_matches = '[]'::jsonb)"
)


class Submission(Base):
    __tablename__ = "submission"

    # nptc.audit.policy (NFR-08): every real column classified. `organisation` is withheld, as on
    # `app_user` (NFR-26): a diff records that it changed, never its value.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "kind",
            "state",
            "preferred_term",
            "synonyms",
            "snomed_code",
            "snomed_fsn",
            "property_values",
            "notes",
            "reference_url",
            "reference_checked_at",
            "reference_status",
            "duplicate_confirmed_at",
            "duplicate_matches",
            "submitter_id",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"organisation"})
    # `row_version` is bookkeeping for optimistic locking, never a "changed field".
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at", "row_version"}
    )

    __table_args__ = (
        CheckConstraint(_KIND_CHECK_SQL, name="kind"),
        CheckConstraint(_STATE_CHECK_SQL, name="state"),
        CheckConstraint(_PREFERRED_TERM_NOT_BLANK_SQL, name="preferred_term_not_blank"),
        CheckConstraint(_SYNONYMS_IS_ARRAY_SQL, name="synonyms_is_array"),
        CheckConstraint(_PROPERTY_VALUES_IS_OBJECT_SQL, name="property_values_is_object"),
        CheckConstraint(_SNOMED_CODE_CHECK_SQL, name="snomed_code"),
        CheckConstraint(_SNOMED_FSN_NOT_BLANK_SQL, name="snomed_fsn_not_blank"),
        CheckConstraint(_SNOMED_CODE_WITH_FSN_SQL, name="snomed_code_with_fsn"),
        CheckConstraint(_NOTES_NOT_BLANK_SQL, name="notes_not_blank"),
        CheckConstraint(_REFERENCE_URL_NOT_BLANK_SQL, name="reference_url_not_blank"),
        CheckConstraint(_REFERENCE_CHECK_COMPLETE_SQL, name="reference_check_complete"),
        CheckConstraint(_NEW_TEST_HAS_REFERENCE_SQL, name="new_test_has_reference"),
        CheckConstraint(_DUPLICATE_MATCHES_IS_ARRAY_SQL, name="duplicate_matches_is_array"),
        CheckConstraint(
            _DUPLICATE_CONFIRMATION_COMPLETE_SQL, name="duplicate_confirmation_complete"
        ),
        # "A user's own submissions" (FR-42) and "newest first" are the two reads on this table.
        Index("ix_submission_submitter_id", "submitter_id"),
        Index("ix_submission_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__` and `__audit_withheld_fields__`:
    # SQLAlchemy knows an attribute's prior value only if it was loaded before reassignment.
    kind: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # A quoted literal: an unquoted `server_default` string is rendered verbatim as SQL.
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Submitted'"), active_history=True
    )
    preferred_term: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    synonyms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), active_history=True
    )
    snomed_code: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    snomed_fsn: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    property_values: Mapped[dict[str, list[dict[str, object]]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), active_history=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    reference_url: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    reference_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    reference_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True, active_history=True
    )
    duplicate_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    duplicate_matches: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), active_history=True
    )
    submitter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    organisation: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Bumped by `version_id_col` on every mapped UPDATE; nothing else may touch it.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # Must follow `row_version`: `version_id_col` binds to that `MappedColumn`. No `ClassVar`
    # annotation, because mypy flags a `ClassVar` override of the base class's `__mapper_args__`.
    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012
