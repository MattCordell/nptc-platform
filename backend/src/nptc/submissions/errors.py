"""Refusals specific to creating a submission (FR-26, FR-35)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import ClassVar

from nptc.db.models.catalogue_entry import CatalogueEntryStatus
from nptc.submissions.duplicates import DuplicateMatch

__all__ = [
    "AmendmentEntryNotActiveError",
    "AmendmentRefusal",
    "AmendmentRefusedError",
    "CodeRefusal",
    "FreeTextField",
    "FreeTextRefusedError",
    "SubmissionCodeRefusedError",
    "SubmissionDuplicatesFoundError",
]


class CodeRefusal(StrEnum):
    """Why a supplied SNOMED CT code cannot be stored on a submission. A fixed set, so the API can
    name the reason to the caller without echoing anything the caller typed."""

    NOT_FOUND = "not-found"
    INACTIVE = "inactive"
    #: The server did not say whether the code is active (hazard H-05). An unresolved status is
    #: not "active", so the code is refused rather than stored on a guess.
    STATUS_NOT_REPORTED = "status-not-reported"
    #: The server knows the code but returned no fully specified name to store beside it.
    NO_FSN = "no-fsn"


class SubmissionCodeRefusedError(ValueError):
    """The terminology server answered, and the answer rules the code out. Distinct from an
    outage, which is `nptc.terminology.errors.TerminologyUnavailableError` (503)."""

    http_status: ClassVar[int] = 422

    def __init__(self, reason: CodeRefusal) -> None:
        self.reason = reason
        super().__init__(f"SNOMED CT code refused: {reason.value}")


class FreeTextField(StrEnum):
    """The free-text fields of a submission. A fixed set, so the API can name which one was refused
    without echoing anything the caller typed."""

    NOTES = "notes"
    ORGANISATION = "organisation"


class FreeTextRefusedError(ValueError):
    """A free-text field carries an invisible character with no single deterministic repair, such
    as a zero-width space or a text-direction override (FR-63). The message names the field and the
    codepoints, never the characters themselves."""

    http_status: ClassVar[int] = 422

    def __init__(self, field: FreeTextField, codepoints: tuple[str, ...]) -> None:
        self.field = field
        super().__init__(f"{field.value} contains an invisible character: {', '.join(codepoints)}")


class SubmissionDuplicatesFoundError(Exception):
    """The submission matches an active catalogue entry or an open submission, and the request did
    not confirm it is a different test (FR-25). Carries the matches so the submitter can review
    them. The message names none of the submitted text."""

    http_status: ClassVar[int] = 409

    def __init__(self, matches: Sequence[DuplicateMatch]) -> None:
        self.matches = tuple(matches)
        super().__init__(f"submission matches {len(self.matches)} existing record(s)")


class AmendmentEntryNotActiveError(Exception):
    """The entry an amendment names exists but is not `active`, so it cannot be amended (FR-35). The
    status is carried so the response can name it; the message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, status: CatalogueEntryStatus) -> None:
        self.status = status
        super().__init__(f"amendment refused, entry is {status.value}")


class AmendmentRefusal(StrEnum):
    """Why an amendment proposes no change. A fixed set, so the API can name the reason without
    echoing anything the caller typed."""

    #: No synonym and no code were sent.
    NOTHING_PROPOSED = "nothing-proposed"
    #: Synonyms were sent, and the entry already has every one, and no code was sent.
    NOTHING_NEW = "nothing-new"
    #: The code is the one the entry already carries, and no synonym is left to propose.
    CODE_ALREADY_BOUND = "code-already-bound"


class AmendmentRefusedError(ValueError):
    """The amendment would leave the entry as it is (FR-35)."""

    http_status: ClassVar[int] = 422

    def __init__(self, reason: AmendmentRefusal) -> None:
        self.reason = reason
        super().__init__(f"amendment refused: {reason.value}")
