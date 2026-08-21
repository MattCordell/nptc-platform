"""FR-05/FR-08 collision detection tests (issue #49).

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_business_key.py`'s own module docstring for why.

The PRD Appendix A.5 fixtures are used verbatim as the regression cases:
`'Adrenal Ab'` (error severity, one entry's preferred term colliding with
another entry's synonym) and `'ADA2'` on three adenosine deaminase
entries (warning severity).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.catalogue.collisions import (
    CollisionSeverity,
    DesignationCollisionError,
    acknowledge_collision,
    warning_collisions,
)
from nptc.catalogue.designations import add_designation, add_synonyms
from nptc.catalogue.entries import EntryChanges, create_entry, save_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc_shared.similarity import collision_key

_NBSP = chr(0x00A0)
_NNBSP = chr(0x202F)


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_entry(
    session: Session,
    preferred_term: str = "Full blood count",
    *,
    status: CatalogueEntryStatus | str = CatalogueEntryStatus.DRAFT,
) -> CatalogueEntry:
    return create_entry(
        session,
        AuditContext.system(),
        preferred_term=preferred_term,
        status=status,
        reason="Created for FR-49 collision test",
    )


#: A random UUID by default - none of these tests need it to reference a
#: real `app_user` row, except `test_acknowledged_warning_does_not_
#: recur_for_that_entry`, which passes `user_id=None` explicitly instead
#: (matching `AuditContext.system()`'s own no-actor posture) to avoid the
#: `acknowledged_by_user_id` FK requiring a real row just for that.
def _principal(*, roles: frozenset[Role], user_id: uuid.UUID | None) -> Principal:
    return Principal(
        user_id=user_id,
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


# --- FR-05 / NFR-38 test 4: error severity, the PRD A.5 fixture -------------


@pytest.mark.req("FR-05")
@pytest.mark.req("NFR-38")
@pytest.mark.integration
def test_synonym_matching_another_entrys_preferred_term_is_rejected(
    app_session: Session,
) -> None:
    """PRD Appendix A.5's own regression fixture: `'Adrenal Ab'` is the
    preferred term of one entry (row 46) and simultaneously a synonym of
    `'21-Hydroxylase Ab'` (row 10) - rejected at error severity, and the
    report names the colliding entry rather than leaving a bare 409."""
    adrenal_ab_entry = _new_entry(app_session, "Adrenal Ab")
    other_entry = _new_entry(app_session, "21-Hydroxylase Ab")
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(DesignationCollisionError) as exc_info:
        add_designation(
            app_session,
            AuditContext.system(),
            entry=other_entry,
            term="Adrenal Ab",
            reason="Adding a synonym that collides with another entry",
        )

    collision = exc_info.value.collisions[0]
    assert collision.severity is CollisionSeverity.ERROR
    assert collision.business_key == adrenal_ab_entry.business_key
    assert collision.preferred_term == "Adrenal Ab"
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_the_reverse_direction_is_also_rejected(app_session: Session) -> None:
    """The symmetric case: setting an entry's *preferred term* to a term
    that is already an active synonym on another live entry is exactly
    the same ordering hazard FR-05 names, whichever side is edited
    second."""
    synonym_holder = _new_entry(app_session, "21-Hydroxylase Ab")
    add_designation(
        app_session,
        AuditContext.system(),
        entry=synonym_holder,
        term="Adrenal Ab",
        reason="Adding the synonym first",
    )
    other_entry = _new_entry(app_session, "Something else")
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(DesignationCollisionError) as exc_info:
        save_entry(
            app_session,
            AuditContext.system(),
            business_key=other_entry.business_key,
            expected_row_version=other_entry.row_version,
            changes=EntryChanges(preferred_term="Adrenal Ab"),
            reason="Renaming to a term that collides with another entry's synonym",
        )

    assert exc_info.value.collisions[0].business_key == synonym_holder.business_key
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_create_entry_rejects_a_preferred_term_colliding_with_another_entrys_synonym(
    app_session: Session,
) -> None:
    synonym_holder = _new_entry(app_session, "21-Hydroxylase Ab")
    add_designation(
        app_session,
        AuditContext.system(),
        entry=synonym_holder,
        term="Adrenal Ab",
        reason="Adding the synonym first",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(DesignationCollisionError):
        _new_entry(app_session, "Adrenal Ab")

    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_non_en_au_preferred_variant_does_not_collide_across_languages(
    app_session: Session,
) -> None:
    """`CatalogueEntry.preferred_term` is always en-AU
    (`ck_designation_no_en_au_preferred`) - a non-en-AU preferred variant
    (issue #47's own mi-NZ example) folding to the same key as an
    unrelated entry's en-AU preferred term must not collide just because
    the two surface forms match; they are not comparable designations at
    all once language is taken into account."""
    _new_entry(app_session, "Adrenal Ab")
    other_entry = _new_entry(app_session, "21-Hydroxylase Ab")
    app_session.flush()

    add_designation(
        app_session,
        AuditContext.system(),
        entry=other_entry,
        term="Adrenal Ab",
        use="preferred",
        language="mi-NZ",
        reason="A non-en-AU preferred variant that happens to share a surface form",
    )


# --- FR-05: normalisation (principal failure mode) --------------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
@pytest.mark.parametrize(
    "variant",
    [
        "Adrenal Ab" + _NBSP,
        "Adrenal" + _NNBSP + "Ab",
        "ADRENAL AB",
        "Adrenal Ab.",
        "Adrenal-Ab",
    ],
    ids=["trailing-nbsp", "narrow-nbsp", "case", "trailing-punctuation", "hyphen-for-space"],
)
def test_collision_survives_normalisation_variants(app_session: Session, variant: str) -> None:
    """A trailing non-breaking space, case change, or punctuation
    difference must not hide a collision - PRD A.1's own whitespace
    defects would otherwise let exactly these variants through."""
    adrenal_ab_entry = _new_entry(app_session, "Adrenal Ab")
    other_entry = _new_entry(app_session, "21-Hydroxylase Ab")
    app_session.flush()

    with pytest.raises(DesignationCollisionError) as exc_info:
        add_designation(
            app_session,
            AuditContext.system(),
            entry=other_entry,
            term=variant,
            reason="Adding a normalised-variant synonym that still collides",
        )

    assert exc_info.value.collisions[0].business_key == adrenal_ab_entry.business_key


# --- FR-05: candidate scope --------------------------------------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
@pytest.mark.parametrize(
    "status", [CatalogueEntryStatus.DEPRECATED, CatalogueEntryStatus.WITHDRAWN]
)
def test_a_deprecated_or_withdrawn_entrys_preferred_term_does_not_collide(
    app_session: Session, status: CatalogueEntryStatus
) -> None:
    _new_entry(app_session, "Adrenal Ab", status=status)
    other_entry = _new_entry(app_session, "21-Hydroxylase Ab")
    app_session.flush()

    add_designation(
        app_session,
        AuditContext.system(),
        entry=other_entry,
        term="Adrenal Ab",
        reason="This must not collide with a deprecated/withdrawn entry",
    )


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_draft_entry_participates_as_a_collision_candidate(app_session: Session) -> None:
    """Broader than FR-05's literal "a different active entry" - a draft
    colliding with another live entry is the same hazard the moment
    either is published, so it is caught before that point rather than
    at it."""
    draft_entry = _new_entry(app_session, "Adrenal Ab", status=CatalogueEntryStatus.DRAFT)
    other_entry = _new_entry(app_session, "21-Hydroxylase Ab")
    app_session.flush()

    with pytest.raises(DesignationCollisionError) as exc_info:
        add_designation(
            app_session,
            AuditContext.system(),
            entry=other_entry,
            term="Adrenal Ab",
            reason="A draft entry is still a live collision candidate",
        )

    assert exc_info.value.collisions[0].business_key == draft_entry.business_key


# --- FR-05: warning severity, the PRD A.5 'ADA2' fixture ---------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_ada2_on_three_entries_warns_but_permits_every_save(app_session: Session) -> None:
    """PRD Appendix A.5's other fixture: `'ADA2'` genuinely belongs to
    three adenosine deaminase entries, disambiguated by specimen - each
    save succeeds and writes its own audit event; the warning is visible
    via `warning_collisions`, not raised."""
    blood = _new_entry(app_session, "Adenosine deaminase")
    csf = _new_entry(app_session, "Adenosine deaminase CSF")
    pleural = _new_entry(app_session, "Adenosine deaminase pleural fluid")
    app_session.flush()
    before = _audit_event_count(app_session)

    add_designation(
        app_session, AuditContext.system(), entry=blood, term="ADA2", reason="First ADA2 synonym"
    )
    add_designation(
        app_session, AuditContext.system(), entry=csf, term="ADA2", reason="Second ADA2 synonym"
    )
    add_designation(
        app_session, AuditContext.system(), entry=pleural, term="ADA2", reason="Third ADA2 synonym"
    )
    app_session.flush()

    assert _audit_event_count(app_session) == before + 3

    warnings = warning_collisions(app_session, entry=blood, terms=["ADA2"])
    other_keys = {c.business_key for c in warnings}
    assert other_keys == {csf.business_key, pleural.business_key}
    assert all(c.severity is CollisionSeverity.WARNING for c in warnings)


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_acknowledged_warning_does_not_recur_for_that_entry(app_session: Session) -> None:
    blood = _new_entry(app_session, "Adenosine deaminase")
    csf = _new_entry(app_session, "Adenosine deaminase CSF")
    fourth = _new_entry(app_session, "Adenosine deaminase, some other specimen")
    add_designation(
        app_session, AuditContext.system(), entry=blood, term="ADA2", reason="First ADA2 synonym"
    )
    add_designation(
        app_session, AuditContext.system(), entry=csf, term="ADA2", reason="Second ADA2 synonym"
    )
    app_session.flush()
    assert warning_collisions(app_session, entry=blood, terms=["ADA2"])

    # `user_id=None` - matching `AuditContext.system()`'s own no-actor
    # posture, and sidestepping the need for a real `app_user` row just to
    # exercise the FK: `acknowledged_by_user_id` is nullable for exactly
    # this reason (see the model's own docstring).
    reviewer = _principal(roles=frozenset({Role.REVIEWER}), user_id=None)
    acknowledge_collision(
        app_session,
        AuditContext.system(),
        acknowledger=reviewer,
        entry=blood,
        term_key=collision_key("ADA2"),
        language="en-AU",
        reason="Genuinely ambiguous abbreviation, disambiguated by specimen",
    )
    app_session.flush()

    assert warning_collisions(app_session, entry=blood, terms=["ADA2"]) == ()
    # csf's own acknowledgement is separate - it still sees the warning.
    assert warning_collisions(app_session, entry=csf, terms=["ADA2"])

    # A fourth entry later adding the same synonym still warns once, on
    # its own save - acknowledging on `blood` did not silence it globally.
    add_designation(
        app_session, AuditContext.system(), entry=fourth, term="ADA2", reason="Fourth ADA2 synonym"
    )
    app_session.flush()
    assert warning_collisions(app_session, entry=fourth, terms=["ADA2"])


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_acknowledge_without_the_permission_is_refused(app_session: Session) -> None:
    """FR-44: checked against a permission, never a role name - Member
    does not hold `VALIDATION_ACKNOWLEDGE`. The negative case gets its own
    test, not just the positive path."""
    entry = _new_entry(app_session, "Adenosine deaminase")
    app_session.flush()
    before = _audit_event_count(app_session)
    member = _principal(roles=frozenset({Role.MEMBER}), user_id=uuid.uuid4())

    with pytest.raises(PermissionDeniedError):
        acknowledge_collision(
            app_session,
            AuditContext.system(),
            acknowledger=member,
            entry=entry,
            term_key=collision_key("ADA2"),
            language="en-AU",
            reason="Attempting to acknowledge without the permission",
        )

    assert _audit_event_count(app_session) == before


# --- add_synonyms dedupes by collision key, not just clean_term -------------


@pytest.mark.req("FR-04")
@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_add_synonyms_deduplicates_by_collision_key(app_session: Session) -> None:
    entry = _new_entry(app_session)

    created = add_synonyms(
        app_session,
        AuditContext.system(),
        entry=entry,
        terms=["ADA2", "ada2", "ADA2."],
        reason="A batch that is one synonym under the FR-05 comparison fold",
    )
    app_session.flush()

    assert [d.term for d in created] == ["ADA2"]
