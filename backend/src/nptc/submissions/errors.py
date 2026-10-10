"""Refusals specific to creating a submission (FR-26)."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

__all__ = ["CodeRefusal", "SubmissionCodeRefusedError"]


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
