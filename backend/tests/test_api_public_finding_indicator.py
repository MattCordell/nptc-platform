"""FR-18 acceptance criteria: the public open-finding indicator (issue
#141).

Every entry and finding here is created directly via `owner_engine`,
committed, and cleaned up in a `finally` block - never through
`seed_public_catalogue`'s shared, uncommitted `app_db` transaction. A
`validation_finding` row must be visible to the public API's own
connection (`app_db`, whose role has SELECT only on this table in any
case - see `nptc.db.roles.GRANT_VALIDATION_FINDING_SQL`), and two
connections each holding their own open, uncommitted transaction cannot
see each other's writes (`test_audit_tamper_detection.py`'s own docstring
explains why) - only a genuinely committed row, visible under READ
COMMITTED to any later statement on any connection, proves the indicator
end to end over real HTTP. Matches `test_catalogue_optimistic_locking.py`'s
own "plain SQL via owner_engine" precedent for the identical reason.

Business keys here occupy the `NPTC-42xxxx` block, disjoint from every
other test module's own reserved block and from the real minting
sequence (which never reaches six figures within one test run).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


def _load(name: str) -> Any:
    # `backend/tests` has no `__init__.py` (pytest's `--import-mode=
    # importlib`), so support modules are loaded by path - see
    # test_api_public_catalogue.py's own identical helper.
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


def _insert_entry(engine: Engine, *, business_key: str, preferred_term: str) -> uuid.UUID:
    with engine.connect() as connection:
        entry_id = connection.execute(
            text(
                "INSERT INTO catalogue_entry (business_key, preferred_term, status) "
                "VALUES (:business_key, :preferred_term, 'active') RETURNING id"
            ),
            {"business_key": business_key, "preferred_term": preferred_term},
        ).scalar_one()
        connection.commit()
    return entry_id


def _insert_finding(engine: Engine, *, entry_id: uuid.UUID, status: str) -> None:
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO validation_finding (entry_id, finding_type, severity, status) "
                "VALUES (:entry_id, 'code_inactive', 'error', :status)"
            ),
            {"entry_id": entry_id, "status": status},
        )
        connection.commit()


def _cleanup(engine: Engine, *, business_key: str) -> None:
    with engine.connect() as connection:
        connection.execute(
            text(
                "DELETE FROM validation_finding WHERE entry_id = "
                "(SELECT id FROM catalogue_entry WHERE business_key = :key)"
            ),
            {"key": business_key},
        )
        connection.execute(
            text("DELETE FROM catalogue_entry WHERE business_key = :key"),
            {"key": business_key},
        )
        connection.commit()


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_entry_with_open_finding_shows_the_indicator(api: ApiTestApp, owner_engine: Engine) -> None:
    business_key = "NPTC-420001"
    entry_id = _insert_entry(owner_engine, business_key=business_key, preferred_term="Ferritin")
    try:
        _insert_finding(owner_engine, entry_id=entry_id, status="open")

        body = api.get(f"/catalogue/entries/{business_key}").json()

        assert body["has_open_finding"] is True
    finally:
        _cleanup(owner_engine, business_key=business_key)


#: One dedicated business key per non-open status, distinct from every
#: other business key in this module.
_NOT_OPEN_BUSINESS_KEYS = {
    "acknowledged": "NPTC-421001",
    "resolved": "NPTC-421002",
    "superseded": "NPTC-421003",
}


@pytest.mark.req("FR-18")
@pytest.mark.integration
@pytest.mark.parametrize("status", sorted(_NOT_OPEN_BUSINESS_KEYS))
def test_entry_with_no_open_finding_does_not_show_the_indicator(
    api: ApiTestApp, owner_engine: Engine, status: str
) -> None:
    business_key = _NOT_OPEN_BUSINESS_KEYS[status]
    entry_id = _insert_entry(owner_engine, business_key=business_key, preferred_term="Ferritin")
    try:
        _insert_finding(owner_engine, entry_id=entry_id, status=status)

        body = api.get(f"/catalogue/entries/{business_key}").json()

        assert body["has_open_finding"] is False
    finally:
        _cleanup(owner_engine, business_key=business_key)


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_entry_with_no_finding_at_all_does_not_show_the_indicator(
    api: ApiTestApp, owner_engine: Engine
) -> None:
    business_key = "NPTC-420002"
    _insert_entry(owner_engine, business_key=business_key, preferred_term="Ferritin")
    try:
        body = api.get(f"/catalogue/entries/{business_key}").json()

        assert body["has_open_finding"] is False
    finally:
        _cleanup(owner_engine, business_key=business_key)


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_anonymous_request_reports_the_indicator_but_nothing_else_about_the_finding(
    api: ApiTestApp, owner_engine: Engine
) -> None:
    """The raw response *text*, not a parsed model - matching
    `test_api_public_response_hygiene.py`'s own whole-body convention, the
    only way to catch a field nobody thought to write an assertion for."""
    business_key = "NPTC-420003"
    entry_id = _insert_entry(owner_engine, business_key=business_key, preferred_term="Ferritin")
    try:
        _insert_finding(owner_engine, entry_id=entry_id, status="open")

        response = api.get(f"/catalogue/entries/{business_key}")
        body_text = response.text

        assert '"has_open_finding":true' in body_text.replace(" ", "")
        assert "code_inactive" not in body_text
        assert '"error"' not in body_text
        assert "finding_type" not in body_text
        assert "severity" not in body_text
        assert str(entry_id) not in body_text
    finally:
        _cleanup(owner_engine, business_key=business_key)


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_the_indicator_appears_on_list_results(api: ApiTestApp, owner_engine: Engine) -> None:
    business_key = "NPTC-420004"
    entry_id = _insert_entry(owner_engine, business_key=business_key, preferred_term="Ferritin")
    try:
        _insert_finding(owner_engine, entry_id=entry_id, status="open")

        body = api.get("/catalogue/entries?after=NPTC-420003").json()

        matching = [item for item in body["items"] if item["business_key"] == business_key]
        assert len(matching) == 1
        assert matching[0]["has_open_finding"] is True
    finally:
        _cleanup(owner_engine, business_key=business_key)


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_the_indicator_appears_on_search_results(api: ApiTestApp, owner_engine: Engine) -> None:
    business_key = "NPTC-420005"
    entry_id = _insert_entry(
        owner_engine, business_key=business_key, preferred_term="Zzyzx finding fixture"
    )
    try:
        _insert_finding(owner_engine, entry_id=entry_id, status="open")

        body = api.get("/catalogue/search?q=Zzyzx+finding+fixture").json()

        matching = [item for item in body["items"] if item["business_key"] == business_key]
        assert len(matching) == 1
        assert matching[0]["has_open_finding"] is True
    finally:
        _cleanup(owner_engine, business_key=business_key)
