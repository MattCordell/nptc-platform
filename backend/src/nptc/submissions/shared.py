"""Checks every kind of submission runs the same way (FR-26, FR-63, FR-82).

A new test and an amendment both take free text and an optional SNOMED CT code. One home for each
check means the two kinds cannot drift apart: a note that one accepts is a note the other accepts,
and a code the terminology server rules out is ruled out for both.
"""

from __future__ import annotations

from nptc.submissions.errors import (
    CodeRefusal,
    FreeTextField,
    FreeTextRefusedError,
    SubmissionCodeRefusedError,
)
from nptc.terminology.concepts import resolve_concept
from nptc.terminology.errors import ConceptNotFoundError
from nptc_shared.terminology import TerminologyClient
from nptc_shared.text import find_invisible_characters, normalise_for_comparison

__all__ = ["clean_free_text", "resolve_code"]

#: Line breaks and tabs are formatting in a multi-line note, not a defect.
_NOTE_FORMATTING = frozenset({chr(10), chr(13), chr(9)})


def clean_free_text(text: str | None, *, field: FreeTextField, multiline: bool) -> str | None:
    """`text` normalised like a term, or `None` if nothing is left. Refuses any invisible
    character, except the formatting a multi-line note may carry."""
    if text is None:
        return None
    normalised = normalise_for_comparison(text)
    allowed = _NOTE_FORMATTING if multiline else frozenset()
    refused = tuple(
        found.codepoint
        for found in find_invisible_characters(normalised)
        if normalised[found.offset] not in allowed
    )
    if refused:
        raise FreeTextRefusedError(field, refused)
    return normalised or None


def resolve_code(client: TerminologyClient, code: str) -> tuple[str, str]:
    """The code and the FSN the server returned for it, or a refusal. The edition's own answer
    decides: an inactive or status-less concept is refused, never stored on a guess."""
    try:
        concept = resolve_concept(client, code)
    except ConceptNotFoundError:
        raise SubmissionCodeRefusedError(CodeRefusal.NOT_FOUND) from None
    if concept.active is None:
        raise SubmissionCodeRefusedError(CodeRefusal.STATUS_NOT_REPORTED)
    if not concept.active:
        raise SubmissionCodeRefusedError(CodeRefusal.INACTIVE)
    if concept.fsn is None or not concept.fsn.strip():
        raise SubmissionCodeRefusedError(CodeRefusal.NO_FSN)
    return concept.code, concept.fsn
