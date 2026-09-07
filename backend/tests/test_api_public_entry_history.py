"""FR-19 acceptance criteria: the public entry-history endpoint (issue
#141).

Setup writes go through the real service functions (`create_entry`/
`save_entry`/`add_synonyms`) on a `Session(bind=app_db)` - the identical
`app_db` connection the `api` fixture's `TestClient` runs on, joining its
already-open, externally-managed transaction rather than starting a new
one (the standard SQLAlchemy "join a session into an external
transaction" shape). `session.flush()`, never `session.commit()`: this
connection's transaction is owned by the `app_db` fixture, which rolls it
back at teardown - committing here would end that transaction out from
under the fixture and the `api` TestClient's own per-request `SAVEPOINT`,
leaving rows to leak into the next test instead of being rolled back.
Because both the setup session and the API's own session share the one
open transaction, a flushed-but-uncommitted write is visible to the API's
read with no commit needed at all - unlike
`test_api_public_finding_indicator.py`'s own `validation_finding` tests,
where `nptc_app` cannot write that table under any connection.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.designations import add_synonyms
from nptc.catalogue.entries import EntryChanges, create_entry, save_entry
from nptc.db.models.catalogue_entry import CatalogueEntryStatus


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_an_entry_never_edited_since_seeding_has_empty_history(
    api: ApiTestApp, app_db: Connection
) -> None:
    """ "Never edited since seeding" means never touched by a write
    service at all (which always leaves an audit event, even a
    `CREATED` one) - a row built directly, the way `public_catalogue_
    support.seed_public_catalogue` seeds every fixture entry."""
    session = Session(bind=app_db)
    session.execute(
        text(
            "INSERT INTO catalogue_entry (business_key, preferred_term, status) "
            "VALUES ('NPTC-430001', 'Never edited entry', 'active')"
        )
    )

    body = api.get("/catalogue/entries/NPTC-430001/history").json()

    assert body == {"items": [], "next_cursor": None}


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_shows_the_changelog_note_matching_the_audit_record(
    api: ApiTestApp, app_db: Connection
) -> None:
    session = Session(bind=app_db)
    ctx = AuditContext.system()
    entry = create_entry(
        session,
        ctx,
        preferred_term="History fixture assay",
        reason="seeded for FR-19 history test",
        status=CatalogueEntryStatus.ACTIVE,
        business_key="NPTC-430002",
    )

    updated = save_entry(
        session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="History fixture assay renamed"),
        reason="renamed per RCPA-QAP review",
    )
    session.flush()
    assert updated.preferred_term == "History fixture assay renamed"

    body = api.get(f"/catalogue/entries/{entry.business_key}/history").json()

    assert len(body["items"]) == 2
    # Most recent first.
    latest = body["items"][0]
    assert latest["action"] == "catalogue_entry.updated"
    assert latest["note"] == "renamed per RCPA-QAP review"
    assert latest["changed_fields"] == ["preferred_term"]
    assert latest["release"] is None
    assert latest["changed_by"] is None  # AuditContext.system(): no human actor

    earliest = body["items"][1]
    assert earliest["action"] == "catalogue_entry.created"
    assert earliest["note"] == "seeded for FR-19 history test"


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_includes_designation_changes(api: ApiTestApp, app_db: Connection) -> None:
    """A `designation` audit event's `entity_id` is the designation's own
    primary key, not the entry's - proving history correctly spans a
    child row, not only the entry's own."""
    session = Session(bind=app_db)
    ctx = AuditContext.system()
    entry = create_entry(
        session,
        ctx,
        preferred_term="Designation history fixture",
        reason="seeded for FR-19 history test",
        status=CatalogueEntryStatus.ACTIVE,
        business_key="NPTC-430003",
    )

    add_synonyms(
        session,
        ctx,
        entry=entry,
        terms=["Synonym for history fixture"],
        reason="added a synonym for review",
    )
    session.flush()

    body = api.get(f"/catalogue/entries/{entry.business_key}/history").json()

    actions = [item["action"] for item in body["items"]]
    assert "designation.created" in actions
    synonym_event = next(item for item in body["items"] if item["action"] == "designation.created")
    assert synonym_event["note"] == "added a synonym for review"


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_never_leaks_a_raw_value_or_internal_id(
    api: ApiTestApp, app_db: Connection
) -> None:
    """Only field *names* appear (`changed_fields`), never the actual
    before/after values, and never the entry's internal UUID - matching
    `test_api_public_response_hygiene.py`'s own whole-body convention."""
    session = Session(bind=app_db)
    ctx = AuditContext.system()
    entry = create_entry(
        session,
        ctx,
        preferred_term="Redaction fixture original term",
        reason="seeded for FR-19 history test",
        status=CatalogueEntryStatus.ACTIVE,
        business_key="NPTC-430004",
    )
    save_entry(
        session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Redaction fixture UNIQUE_CHANGED_VALUE_XYZ"),
        reason="renamed per RCPA-QAP review",
    )
    session.flush()

    response = api.get(f"/catalogue/entries/{entry.business_key}/history")
    body_text = response.text

    assert "UNIQUE_CHANGED_VALUE_XYZ" not in body_text
    assert "Redaction fixture original term" not in body_text
    assert str(entry.id) not in body_text
    assert '"changed_fields":["preferred_term"]' in body_text.replace(" ", "")


@pytest.mark.req("FR-19")
@pytest.mark.integration
def test_history_pages_with_a_keyset_cursor(api: ApiTestApp, app_db: Connection) -> None:
    session = Session(bind=app_db)
    ctx = AuditContext.system()
    entry = create_entry(
        session,
        ctx,
        preferred_term="Paging fixture",
        reason="seeded for FR-19 history test",
        status=CatalogueEntryStatus.ACTIVE,
        business_key="NPTC-430005",
    )
    save_entry(
        session,
        ctx,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(preferred_term="Paging fixture v2"),
        reason="first edit",
    )
    session.flush()

    first_page = api.get(f"/catalogue/entries/{entry.business_key}/history?limit=1").json()
    assert len(first_page["items"]) == 1
    assert first_page["items"][0]["action"] == "catalogue_entry.updated"
    assert first_page["next_cursor"] is not None

    second_page = api.get(
        f"/catalogue/entries/{entry.business_key}/history"
        f"?limit=1&before={first_page['next_cursor']}"
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["action"] == "catalogue_entry.created"
    assert second_page["next_cursor"] is None
