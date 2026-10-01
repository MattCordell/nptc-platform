"""`CatalogueEntry.length` (FR-85): computed from the preferred term, never
stored, and computed once per term value.

Transient entries throughout - no row is needed to exercise a property.
"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import Table, update
from sqlalchemy.orm import Session

import nptc.db.models.catalogue_entry as catalogue_entry_module
from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import create_entry
from nptc.catalogue.term_hygiene import preferred_term_length
from nptc.db.models.catalogue_entry import CatalogueEntry


def _counting_length(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _counted(term: str) -> int:
        calls.append(term)
        return preferred_term_length(term)

    monkeypatch.setattr(catalogue_entry_module, "preferred_term_length", _counted)
    return calls


@pytest.mark.req("FR-85")
def test_length_is_the_length_of_the_cleaned_preferred_term() -> None:
    nbsp = chr(0x00A0)
    entry = CatalogueEntry(preferred_term=f"  Full blood count{nbsp}")

    assert entry.length == len("Full blood count")


@pytest.mark.req("FR-85")
def test_reading_an_unchanged_term_twice_computes_the_length_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = CatalogueEntry(preferred_term="Iron")
    calls = _counting_length(monkeypatch)

    assert entry.length == entry.length == 4
    assert len(calls) == 1


@pytest.mark.req("FR-85")
def test_reassigning_the_preferred_term_changes_the_length() -> None:
    entry = CatalogueEntry(preferred_term="Iron")
    assert entry.length == 4

    entry.preferred_term = "Full blood count"

    assert entry.length == len("Full blood count")


@pytest.mark.req("FR-85")
def test_a_term_replaced_without_the_validator_never_serves_a_stale_length() -> None:
    """A reload or refresh writes the attribute state directly, bypassing
    `@validates`, so the cache must be keyed on the term, not cleared by the
    validator."""
    entry = CatalogueEntry(preferred_term="Iron")
    assert entry.length == 4

    entry.__dict__["preferred_term"] = "Full blood count"

    assert entry.length == len("Full blood count")


@pytest.mark.req("FR-85")
@pytest.mark.integration
def test_a_refresh_after_an_out_of_band_update_never_serves_a_stale_length(
    app_session: Session,
) -> None:
    """The real mechanism behind the test above: `session.refresh` reloads
    the column without running `@validates`."""
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Iron",
        reason="Created for FR-85 refresh test",
    )
    app_session.flush()
    assert entry.length == 4
    table = cast(Table, CatalogueEntry.__table__)

    app_session.execute(
        update(table)
        .where(table.c.business_key == entry.business_key)
        .values(preferred_term="Full blood count")
    )
    app_session.refresh(entry)

    assert entry.preferred_term == "Full blood count"
    assert entry.length == len("Full blood count")
