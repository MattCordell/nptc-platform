"""Creating a submission that proposes a new test (FR-23, FR-24, FR-26, FR-27, NFR-08).

**Everything that can refuse runs before the first write.** The term and notes are cleaned, the
property values are checked against the registry, and the code is resolved through the terminology
server. Only then does the function take the audit append lock, so a refused request takes no lock
and, in particular, no request holds the lock across a call to the terminology server.

**The FSN is the server's, never the caller's (FR-82).** The request carries a code only. The stored
label is stored exactly as served.

**A submission's property values are not entry rows.** They sit in one JSONB document keyed by
property key, each key holding the same `{value, justification}` items the entry write path takes. A
later step that turns a submission into an entry copies each item into a `property_value` row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.property_values import (
    PropertyDefinitionNotFoundError,
    PropertyValidationError,
    PropertyValueInput,
    PropertyWriteIssue,
    check_property_values,
)
from nptc.catalogue.term_hygiene import clean_term
from nptc.db.models.property_definition import PropertyDefinition, PropertyScope, PropertyStatus
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.registry.definitions import DeprecatedPropertyWriteError
from nptc.registry.handlers import DatatypeRegistry
from nptc.submissions.errors import CodeRefusal, SubmissionCodeRefusedError
from nptc.terminology.concepts import resolve_concept
from nptc.terminology.errors import ConceptNotFoundError
from nptc_shared.terminology import TerminologyClient

__all__ = ["NewTestSubmissionInput", "create_new_test_submission"]

#: A submission accepts the properties whose scope reaches it. `maintenance` properties belong to
#: the published catalogue alone (FR-09).
SUBMISSION_SCOPES = frozenset({PropertyScope.SUBMISSION, PropertyScope.BOTH})


@dataclass(frozen=True)
class NewTestSubmissionInput:
    """What a submitter supplies. There is no `length` or FSN field: both are computed or served,
    never accepted (FR-24, FR-82). `organisation` of `None` means "use the profile's"; a blank
    string means "none"."""

    preferred_term: str
    synonyms: Sequence[str] = ()
    snomed_code: str | None = None
    property_values: Mapping[str, Sequence[PropertyValueInput]] = field(default_factory=dict)
    notes: str | None = None
    organisation: str | None = None


def create_new_test_submission(
    session: Session,
    ctx: AuditContext,
    *,
    content: NewTestSubmissionInput,
    profile_organisation: str | None,
    registry: DatatypeRegistry,
    terminology_client: TerminologyClient,
) -> Submission:
    """Stores a new-test submission in state `Submitted`, attributed to `ctx.actor_user_id`, and
    appends its audit event.

    Raises `nptc.catalogue.term_hygiene.TermCleaningError` for a term that is empty or carries an
    invisible character, `PropertyValidationError` carrying every property problem at once,
    `nptc.terminology.errors` and `nptc_shared.sctid.InvalidSCTIDError` for a code that is
    malformed or cannot be looked up, and `SubmissionCodeRefusedError` for a code the server
    knows but rules out. Each is raised before any row is added.
    """
    if ctx.actor_user_id is None:
        raise ValueError("a submission needs a human submitter, but the audit context has none")

    preferred_term = clean_term(content.preferred_term)
    synonyms = [clean_term(term) for term in content.synonyms]
    notes = _blank_to_none(content.notes)
    organisation = (
        profile_organisation
        if content.organisation is None
        else _blank_to_none(content.organisation)
    )

    property_values = {key: items for key, items in content.property_values.items() if items}
    _check_property_values(session, registry, property_values)

    snomed_code: str | None = None
    snomed_fsn: str | None = None
    if content.snomed_code is not None:
        snomed_code, snomed_fsn = _resolve_code(terminology_client, content.snomed_code)

    acquire_append_lock(session)
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=SubmissionState.SUBMITTED.value,
        preferred_term=preferred_term,
        synonyms=synonyms,
        snomed_code=snomed_code,
        snomed_fsn=snomed_fsn,
        property_values={
            key: [{"value": item.value, "justification": item.justification} for item in items]
            for key, items in property_values.items()
        },
        notes=notes,
        submitter_id=ctx.actor_user_id,
        organisation=organisation,
    )
    session.add(submission)
    record_change(
        session,
        ctx,
        action="submission.created",
        instance=submission,
        kind=ChangeKind.CREATED,
    )
    return submission


def _blank_to_none(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _resolve_code(client: TerminologyClient, code: str) -> tuple[str, str]:
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


def _check_property_values(
    session: Session,
    registry: DatatypeRegistry,
    property_values: Mapping[str, Sequence[PropertyValueInput]],
) -> None:
    """Raises one `PropertyValidationError` naming every problem: a key that is unknown,
    deprecated or out of scope, a value the registry's handler refuses, and a property the
    registry requires at submission (FR-24) that the request leaves out."""
    issues: list[PropertyWriteIssue] = []
    for key, items in property_values.items():
        try:
            check = check_property_values(session, key, items, registry, scopes=SUBMISSION_SCOPES)
        except PropertyDefinitionNotFoundError:
            issues.append(
                PropertyWriteIssue(
                    property_key=key,
                    label=key,
                    code="unknown-property",
                    message=f"{key} is not a known property",
                )
            )
        except DeprecatedPropertyWriteError:
            issues.append(
                PropertyWriteIssue(
                    property_key=key,
                    label=key,
                    code="deprecated-property",
                    message=f"{key} is deprecated and accepts no new values",
                )
            )
        else:
            issues.extend(check.issues)

    required = session.execute(
        select(PropertyDefinition)
        .where(
            PropertyDefinition.required_for_submission.is_(True),
            PropertyDefinition.status == PropertyStatus.ACTIVE,
            PropertyDefinition.scope.in_(SUBMISSION_SCOPES),
        )
        .order_by(PropertyDefinition.key)
    ).scalars()
    issues.extend(
        PropertyWriteIssue(
            property_key=definition.key,
            label=definition.label,
            code="required-property-missing",
            message=f"{definition.label} is required for a submission and has no value",
        )
        for definition in required
        if definition.key not in property_values
    )

    if issues:
        raise PropertyValidationError(tuple(issues))
