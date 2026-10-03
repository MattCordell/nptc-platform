"""Seed loader tests against Postgres (FR-76, ADR-0010, ADR-0042).

Every test requests `pristine_catalogue`: the loader refuses a catalogue holding any entry, which
is whole-table by definition, so no test can scope it to rows it created. Datasets are
built in memory by `make_dataset_document`, because the sample workbook's own dataset carries
uncoded specimens that the reader refuses by design.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.verification import verify_chain
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import SEED_IMPORT_NOTE
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN, allocate_business_key, create_entry
from nptc.catalogue.local_codes import create_local_code_unchecked
from nptc.catalogue.seed_dataset import ImportDataset, read_import_dataset
from nptc.catalogue.seed_import import (
    CatalogueNotEmptyError,
    SeedEntryError,
    SeedPrerequisiteError,
    SeedReport,
    find_unclean_preferred_terms,
    seed_baseline,
)
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation
from nptc.db.models.entry_seed_provenance import EntrySeedProvenance
from nptc.db.models.local_code import LocalCode
from nptc.db.models.local_code_system import LocalCodeSystem
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.seed_import import SeedImport

pytestmark = pytest.mark.usefixtures("pristine_catalogue")

MakeDocument = Callable[..., dict[str, Any]]
WriteDataset = Callable[[object], Path]


def _read(document: dict[str, Any], write_dataset: WriteDataset) -> ImportDataset:
    return read_import_dataset(write_dataset(document))


def _seed(
    session: Session, document: dict[str, Any], write_dataset: WriteDataset
) -> tuple[SeedReport, ImportDataset]:
    dataset = _read(document, write_dataset)
    return seed_baseline(session, dataset), dataset


def _count(session: Session, model: type) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _entry(session: Session, business_key: str) -> CatalogueEntry:
    return session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == business_key)
    ).scalar_one()


def _values(session: Session, entry: CatalogueEntry, key: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(PropertyValue)
        .where(PropertyValue.entry_id == entry.id, PropertyValue.property_key == key)
        .order_by(PropertyValue.ordinal)
    ).scalars()
    return [row.value for row in rows]


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_every_entry_is_written_with_its_designations_bindings_and_properties(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(3)
    document["entries"][0]["properties"]["usage_guidance"] = "Collect before treatment"

    report, _ = _seed(app_session, document, write_dataset)

    assert (report.entries, report.synonyms, report.code_bindings) == (3, 3, 3)
    assert report.property_values == 3 * 2 + 1
    assert _count(app_session, CatalogueEntry) == 3

    first = _entry(app_session, "NPTC-500000")
    assert first.preferred_term == "1,1,1-Trichloroethane"
    synonyms = (
        app_session.execute(select(Designation).where(Designation.entry_id == first.id))
        .scalars()
        .all()
    )
    assert [(d.term, d.use, d.language, d.status) for d in synonyms] == [
        ("1,1,1-Trichloroethane synonym", "synonym", "en-AU", "active")
    ]
    binding = app_session.execute(
        select(CodeBinding).where(CodeBinding.entry_id == first.id)
    ).scalar_one()
    assert (binding.code, binding.fsn, binding.status) == (
        "121348009",
        "1,1,1-Trichloroethane measurement",
        "active",
    )
    assert _values(app_session, first, "specimen") == [
        {"system": "http://snomed.info/sct", "code": "119364003", "display": "Serum"}
    ]
    assert _values(app_session, first, "usage_guidance") == ["Collect before treatment"]
    (discipline,) = _values(app_session, first, "discipline")
    assert (discipline["code"], discipline["display"]) == (
        "chemical_pathology",
        "Chemical pathology",
    )


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_the_dataset_status_is_used_not_the_draft_default(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _seed(app_session, make_dataset_document(1), write_dataset)

    assert _entry(app_session, "NPTC-500000").status == "active"


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_codes_reach_the_database_as_strings(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _seed(app_session, make_dataset_document(2), write_dataset)

    codes = app_session.execute(select(CodeBinding.code).order_by(CodeBinding.code)).scalars().all()
    assert len(codes) == 2
    assert all(isinstance(code, str) for code in codes)
    assert (
        app_session.execute(
            text("SELECT pg_typeof(code)::text FROM code_binding LIMIT 1")
        ).scalar_one()
        == "text"
    )


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_long_code_keeps_every_digit(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _seed(app_session, make_dataset_document(2), write_dataset)

    stored = app_session.execute(
        select(CodeBinding.code).where(CodeBinding.code == "873871000168106")
    ).scalar_one()
    assert stored == "873871000168106"


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_provenance_and_the_seed_record_are_stored_verbatim(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    document["entries"][1]["source"]["legacy_version"] = None
    document["entries"][1]["source"]["legacy_history"] = None

    _seed(app_session, document, write_dataset)

    seed = app_session.execute(select(SeedImport)).scalar_one()
    assert (seed.release_name, seed.source_filename, seed.source_sha256) == (
        "2026-06",
        "workbook.xlsx",
        "a" * 64,
    )
    assert (seed.dataset_schema_version, seed.entry_count) == (1, 2)
    rows = {
        _entry_key(app_session, row.entry_id): row
        for row in app_session.execute(select(EntrySeedProvenance)).scalars()
    }
    assert rows["NPTC-500000"].source_row == 2
    assert rows["NPTC-500000"].legacy_version == "2024-07-01T00:00:00"
    assert rows["NPTC-500000"].legacy_history == "Jul 2024 - Added by PI Pilot 22-24"
    assert rows["NPTC-500001"].legacy_version is None
    assert rows["NPTC-500001"].legacy_history is None
    assert all(row.seed_import_id == seed.id for row in rows.values())


def _entry_key(session: Session, entry_id: object) -> str:
    return session.execute(
        select(CatalogueEntry.business_key).where(CatalogueEntry.id == entry_id)
    ).scalar_one()


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_the_next_minted_key_is_above_every_seeded_key(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    report, _ = _seed(app_session, make_dataset_document(3), write_dataset)

    minted = allocate_business_key(app_session)

    assert report.highest_business_key == "NPTC-500002"
    match = BUSINESS_KEY_PATTERN.match(minted)
    assert match is not None
    assert int(match.group(1)) > 500002


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_every_write_emits_an_audit_event_attributed_to_the_system(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    baseline = app_session.execute(
        select(func.coalesce(func.max(AuditEvent.sequence), 0))
    ).scalar_one()

    _seed(app_session, make_dataset_document(3), write_dataset)

    events = app_session.execute(
        select(AuditEvent.action, AuditEvent.actor_user_id, AuditEvent.reason).where(
            AuditEvent.sequence > baseline
        )
    ).all()
    actions = [event.action for event in events]
    # Per entry: entry, synonym, binding, discipline set, specimen set, provenance.
    assert len(events) == 3 * 6 + 1
    assert actions.count("catalogue_entry.created") == 3
    assert actions.count("designation.created") == 3
    assert actions.count("code_binding.created") == 3
    assert actions.count("property_value.set") == 6
    assert actions.count("entry_seed_provenance.created") == 3
    assert actions.count("seed_import.created") == 1
    assert all(event.actor_user_id is None for event in events)
    assert all(event.reason == SEED_IMPORT_NOTE for event in events)


@pytest.mark.req("NFR-10")
@pytest.mark.integration
@pytest.mark.usefixtures("pristine_audit_event")
def test_the_audit_chain_is_intact_across_the_seeded_writes(
    app_session: Session,
    app_db: Connection,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
) -> None:
    _seed(app_session, make_dataset_document(3), write_dataset)
    app_session.flush()

    result = verify_chain(app_db)

    assert result.ok
    assert result.record_count == 3 * 6 + 1


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_a_non_empty_catalogue_is_refused_and_nothing_is_written(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="An existing entry",
        reason=SEED_IMPORT_NOTE,
        business_key="NPTC-499999",
    )
    dataset = _read(make_dataset_document(2), write_dataset)

    with pytest.raises(CatalogueNotEmptyError):
        seed_baseline(app_session, dataset)

    assert _count(app_session, CatalogueEntry) == 1
    assert _count(app_session, SeedImport) == 0
    assert _count(app_session, EntrySeedProvenance) == 0


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_a_second_run_against_a_seeded_catalogue_is_refused(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    _seed(app_session, document, write_dataset)

    with pytest.raises(CatalogueNotEmptyError):
        seed_baseline(app_session, _read(document, write_dataset))

    assert _count(app_session, CatalogueEntry) == 2
    assert _count(app_session, SeedImport) == 1


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_collision_names_the_entry_and_rolls_everything_back(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(3)
    clash = document["entries"][0]["preferred_term"]
    document["entries"][2]["preferred_term"] = clash
    document["entries"][2]["designations"][0]["term"] = clash
    dataset = _read(document, write_dataset)

    with pytest.raises(SeedEntryError) as exc_info:
        seed_baseline(app_session, dataset)
    app_session.rollback()

    error = exc_info.value
    assert error.business_key == "NPTC-500002"
    assert error.preferred_term == clash
    assert repr(clash) in str(error)
    assert error.row == 4
    assert error.cause_type == "DesignationCollisionError"
    assert "NPTC-500000" in error.detail
    assert _count(app_session, CatalogueEntry) == 0
    assert _count(app_session, SeedImport) == 0
    assert _count(app_session, Designation) == 0
    assert _count(app_session, CodeBinding) == 0
    assert _count(app_session, PropertyValue) == 0
    assert _count(app_session, EntrySeedProvenance) == 0


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_an_unknown_discipline_refuses_before_any_entry_is_written(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    document["entries"][1]["properties"]["discipline"] = [{"value": "Phrenology", "code": None}]
    dataset = _read(document, write_dataset)

    with pytest.raises(SeedPrerequisiteError) as exc_info:
        seed_baseline(app_session, dataset)
    app_session.rollback()

    assert "Phrenology" in exc_info.value.problems[0]
    assert _count(app_session, CatalogueEntry) == 0


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_discipline_matches_the_governed_code_ignoring_case(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(1)
    document["entries"][0]["properties"]["discipline"] = [{"value": "HAEMATOLOGY", "code": None}]

    _seed(app_session, document, write_dataset)

    (value,) = _values(app_session, _entry(app_session, "NPTC-500000"), "discipline")
    assert (value["code"], value["display"]) == ("haematology", "Haematology")


@pytest.mark.req("FR-92")
@pytest.mark.integration
def test_subgroup_labels_become_provisional_local_codes_once_each(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    document["entries"][0]["properties"]["subgroup"] = [
        {"value": "Coagulation", "code": None},
        {"value": "Drug measurement", "code": None},
    ]
    document["entries"][1]["properties"]["subgroup"] = [{"value": "Coagulation", "code": None}]

    report, _ = _seed(app_session, document, write_dataset)

    assert report.provisional_subgroup_codes == ("Coagulation", "Drug measurement")
    created = app_session.execute(
        select(LocalCode.code, LocalCode.display, LocalCode.provisional).where(
            LocalCode.code.in_(["Coagulation", "Drug measurement"])
        )
    ).all()
    assert sorted(created) == [
        ("Coagulation", "Coagulation", True),
        ("Drug measurement", "Drug measurement", True),
    ]
    second = _values(app_session, _entry(app_session, "NPTC-500001"), "subgroup")
    assert [value["code"] for value in second] == ["Coagulation"]


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_an_unconstrained_entry_keeps_its_flag_and_holds_no_specimen(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(1)
    document["entries"][0]["specimen_unconstrained"] = True
    document["entries"][0]["properties"]["specimen"] = []

    _seed(app_session, document, write_dataset)

    entry = _entry(app_session, "NPTC-500000")
    assert entry.specimen_unconstrained is True
    assert _values(app_session, entry, "specimen") == []


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_the_whole_table_check_finds_a_term_that_skipped_clean_term(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    """A Core insert skips `clean_term`, and only a whole-table
    check can see it."""
    _seed(app_session, make_dataset_document(2), write_dataset)
    assert find_unclean_preferred_terms(app_session) == []

    app_session.execute(
        text(
            "INSERT INTO catalogue_entry (business_key, preferred_term) "
            "VALUES ('NPTC-499998', 'Trailing space' || chr(160))"
        )
    )

    app_session.execute(
        text(
            "INSERT INTO catalogue_entry (business_key, preferred_term) "
            "VALUES ('NPTC-499997', 'Zero width' || chr(8203))"
        )
    )

    assert sorted(find_unclean_preferred_terms(app_session)) == ["NPTC-499997", "NPTC-499998"]


@pytest.mark.integration
def test_the_append_lock_is_taken_before_any_catalogue_row_is_touched(
    app_session: Session,
    app_db: Connection,
    capture_statements: Any,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
) -> None:
    dataset = _read(make_dataset_document(1), write_dataset)

    with capture_statements(app_db) as statements:
        seed_baseline(app_session, dataset)

    lock_at = next(i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql)
    first_catalogue_at = next(i for i, sql in enumerate(statements) if "catalogue_entry" in sql)
    assert lock_at < first_catalogue_at


@pytest.mark.integration
def test_the_system_properties_exist_after_a_run(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _seed(app_session, make_dataset_document(1), write_dataset)

    keys = (
        app_session.execute(
            text(
                "SELECT key FROM property_definition "
                "WHERE key IN ('discipline','subgroup','specimen','usage_guidance') ORDER BY key"
            )
        )
        .scalars()
        .all()
    )
    assert keys == ["discipline", "specimen", "subgroup", "usage_guidance"]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_synonym_equal_to_its_own_preferred_term_is_stored_as_the_workbook_has_it(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    """The platform's synonym path treats the same entry's own terms as no collision, so the
    loader neither refuses nor silently drops the row."""
    document = make_dataset_document(1)
    term = document["entries"][0]["preferred_term"]
    document["entries"][0]["designations"][1]["term"] = term

    _seed(app_session, document, write_dataset)

    synonyms = app_session.execute(select(Designation.term)).scalars().all()
    assert synonyms == [term]


def _add_local_code(
    session: Session, system_key: str, *, code: str, display: str, deprecated: bool = False
) -> LocalCode:
    system = session.execute(
        select(LocalCodeSystem).where(LocalCodeSystem.key == system_key)
    ).scalar_one()
    created = create_local_code_unchecked(
        session,
        AuditContext.system(),
        system=system,
        code=code,
        display=display,
        reason=SEED_IMPORT_NOTE,
    )
    if deprecated:
        created.status = "deprecated"
        created.deprecated_at = func.now()
        created.deprecation_reason = SEED_IMPORT_NOTE
        session.flush()
    return created


def _with_subgroup(document: dict[str, Any], label: str) -> dict[str, Any]:
    document["entries"][0]["properties"]["subgroup"] = [{"value": label, "code": None}]
    return document


@pytest.mark.req("FR-92")
@pytest.mark.integration
def test_an_existing_subgroup_code_is_reused_by_display_ignoring_case(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    """The governed codes use a slug for `code` and a readable `display` (`haematology` /
    `Haematology`), so a subgroup an administrator added first looks the same way."""
    _add_local_code(app_session, "subgroup", code="coag_slug", display="Coagulation")
    document = _with_subgroup(make_dataset_document(1), "COAGULATION")

    report, _ = _seed(app_session, document, write_dataset)

    assert report.provisional_subgroup_codes == ()
    (value,) = _values(app_session, _entry(app_session, "NPTC-500000"), "subgroup")
    assert (value["code"], value["display"]) == ("coag_slug", "Coagulation")
    matching = app_session.execute(
        select(func.count())
        .select_from(LocalCode)
        .where(func.lower(LocalCode.display) == "coagulation")
    ).scalar_one()
    assert matching == 1


@pytest.mark.req("FR-92")
@pytest.mark.integration
def test_a_deprecated_subgroup_code_refuses_up_front_and_creates_nothing(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _add_local_code(app_session, "subgroup", code="coag", display="Coagulation", deprecated=True)
    document = _with_subgroup(make_dataset_document(2), "Coagulation")
    document["entries"][1]["properties"]["subgroup"] = [{"value": "Drug measurement", "code": None}]
    dataset = _read(document, write_dataset)

    with pytest.raises(SeedPrerequisiteError) as exc_info:
        seed_baseline(app_session, dataset)

    assert "deprecated" in exc_info.value.problems[0]
    assert _count(app_session, CatalogueEntry) == 0
    created = app_session.execute(
        select(func.count()).select_from(LocalCode).where(LocalCode.code == "Drug measurement")
    ).scalar_one()
    assert created == 0


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_a_label_naming_two_active_codes_is_refused_not_resolved_silently(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _add_local_code(app_session, "discipline", code="chem_dup", display="CHEMICAL PATHOLOGY")
    dataset = _read(make_dataset_document(1), write_dataset)

    with pytest.raises(SeedPrerequisiteError) as exc_info:
        seed_baseline(app_session, dataset)

    (problem,) = exc_info.value.problems
    assert "chem_dup" in problem
    assert "chemical_pathology" in problem
    assert _count(app_session, CatalogueEntry) == 0


@pytest.mark.integration
def test_every_classification_refusal_is_reported_together(
    app_session: Session, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    _add_local_code(app_session, "subgroup", code="coag", display="Coagulation", deprecated=True)
    document = _with_subgroup(make_dataset_document(2), "Coagulation")
    document["entries"][1]["properties"]["discipline"] = [{"value": "Phrenology", "code": None}]
    dataset = _read(document, write_dataset)

    with pytest.raises(SeedPrerequisiteError) as exc_info:
        seed_baseline(app_session, dataset)

    problems = " | ".join(exc_info.value.problems)
    assert len(exc_info.value.problems) == 2
    assert "Phrenology" in problems
    assert "deprecated" in problems
