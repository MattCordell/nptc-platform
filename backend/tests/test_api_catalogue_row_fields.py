"""The `code` and `disciplines` fields every list, search and detail row carries.

They exist so the public catalogue screen can show a row's SNOMED CT code and
discipline without a request per row. The failure modes asserted here are the
ones a row-level lookup gets wrong: a retired code shown as current, a missing
code shown as anything but `null`, a second discipline dropped, and a page whose
statement count grows with its size.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding, CodeBindingStatus
from nptc.db.models.property_value import PropertyValue


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

#: Verhoeff-valid SCTIDs (`code_binding.code`'s `CHECK` rejects anything else),
#: the same ones `public_catalogue_support.py` binds.
_ACTIVE_CODE = "391483001"
_RETIRED_CODE = "71388002"
_OTHER_RETIRED_CODE = "394596001"

_DISCIPLINE_SYSTEM = "https://example.org/nptc/discipline"


@dataclass(frozen=True)
class _Seeded:
    token: str
    before_all: str
    bound: str
    retired_only: str
    unbound: str

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.bound, self.retired_only, self.unbound)


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> _Seeded:
    """Three active entries in a nine-digit key block, so a cursor of
    `before_all` scopes a listing to them alone."""
    session = api.session
    seed_system_properties(session)
    base = random.randrange(100_000_000, 999_000_000)
    token = f"rowfields{base}"
    seeded = _Seeded(
        token=token,
        before_all=f"NPTC-{base - 1}",
        bound=f"NPTC-{base}",
        retired_only=f"NPTC-{base + 1}",
        unbound=f"NPTC-{base + 2}",
    )
    entries = {
        key: CatalogueEntry(
            business_key=key,
            preferred_term=f"Ferritin {token} {suffix}",
            status=CatalogueEntryStatus.ACTIVE.value,
        )
        for key, suffix in zip(seeded.keys, ("bound", "retired", "unbound"), strict=True)
    }
    session.add_all(entries.values())
    session.flush()

    bound = entries[seeded.bound]
    active = CodeBinding(
        entry_id=bound.id,
        code=_ACTIVE_CODE,
        fsn="Microscopy (acid fast bacilli) (procedure)",
        au_preferred_term=None,
        edition_hint="au",
        status=CodeBindingStatus.ACTIVE.value,
    )
    session.add(active)
    session.flush()
    retired_at = datetime.now(UTC)
    session.add_all(
        [
            CodeBinding(
                entry_id=bound.id,
                code=_RETIRED_CODE,
                fsn="Procedure (procedure)",
                au_preferred_term=None,
                edition_hint="int",
                status=CodeBindingStatus.RETIRED.value,
                retirement_reason="Rebound to a successor.",
                replaced_by_binding_id=active.id,
                retired_at=retired_at,
            ),
            CodeBinding(
                entry_id=entries[seeded.retired_only].id,
                code=_OTHER_RETIRED_CODE,
                fsn="Poikilocytosis (finding)",
                au_preferred_term=None,
                edition_hint="int",
                status=CodeBindingStatus.RETIRED.value,
                retirement_reason="Withdrawn with no successor.",
                retired_at=retired_at,
            ),
            # Stored out of display order, so a loader that ignored `ordinal`
            # would return them reversed.
            PropertyValue(
                entry_id=bound.id,
                property_key="discipline",
                ordinal=1,
                # No `display`: the row falls back to the code.
                value={"system": _DISCIPLINE_SYSTEM, "code": "haem"},
            ),
            PropertyValue(
                entry_id=bound.id,
                property_key="discipline",
                ordinal=0,
                value={
                    "system": _DISCIPLINE_SYSTEM,
                    "code": "chem",
                    "display": "Chemical pathology",
                },
            ),
        ]
    )
    session.flush()
    return seeded


_EXPECTED: dict[str, tuple[str | None, list[str]]] = {
    "bound": (_ACTIVE_CODE, ["Chemical pathology", "haem"]),
    "retired_only": (None, []),
    "unbound": (None, []),
}


def _assert_rows(seeded: _Seeded, items: list[dict[str, Any]]) -> None:
    by_key = {item["business_key"]: item for item in items}
    for handle, (code, disciplines) in _EXPECTED.items():
        row = by_key[getattr(seeded, handle)]
        assert row["code"] == code, handle
        assert row["disciplines"] == disciplines, handle


@pytest.mark.req("FR-20")
@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_anonymous_list_rows_carry_the_active_code_and_disciplines(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    response = api.get("/catalogue/entries", params={"after": seeded.before_all, "limit": 3})

    assert response.status_code == 200, response.text
    _assert_rows(seeded, response.json()["items"])
    # FR-06: the code is a JSON string on the wire, never a number.
    assert f'"code":"{_ACTIVE_CODE}"' in response.text


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_anonymous_search_hits_carry_the_active_code_and_disciplines(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    response = api.get("/catalogue/search", params={"q": f"Ferritin {seeded.token}"})

    assert response.status_code == 200, response.text
    _assert_rows(seeded, response.json()["items"])


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_detail_carries_the_same_fields_as_its_row(api: ApiTestApp, seeded: _Seeded) -> None:
    items = [api.get(f"/catalogue/entries/{key}").json() for key in seeded.keys]

    _assert_rows(seeded, items)
    # The retired code is still published, in `bindings`, just never as the row's code.
    bound = items[0]
    assert {b["code"] for b in bound["bindings"]} == {_ACTIVE_CODE, _RETIRED_CODE}


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_admin_rows_carry_the_same_fields(api: ApiTestApp, seeded: _Seeded) -> None:
    token = api.admin_token(subject="sub-row-fields-admin")

    found = api.get(
        "/catalogue/admin/search", token=token, params={"q": f"Ferritin {seeded.token}"}
    )
    details = [
        api.get(f"/catalogue/admin/entries/{key}", token=token).json() for key in seeded.keys
    ]

    assert found.status_code == 200, found.text
    _assert_rows(seeded, found.json()["items"])
    _assert_rows(seeded, details)


@pytest.mark.req("FR-20")
@pytest.mark.integration
@pytest.mark.parametrize("path", ["/catalogue/entries", "/catalogue/search"])
def test_row_fields_cost_a_fixed_number_of_statements_whatever_the_page_size(
    api: ApiTestApp,
    seeded: _Seeded,
    app_db: Connection,
    capture_statements: Any,
    path: str,
) -> None:
    """A per-row lookup passes every assertion above on a page of three and
    fails only here: one row and three rows must cost the same."""
    params: dict[str, Any] = (
        {"after": seeded.before_all}
        if path == "/catalogue/entries"
        else {"q": f"Ferritin {seeded.token}"}
    )
    counts = []
    for limit in (1, 3):
        with capture_statements(app_db) as statements:
            response = api.get(path, params={**params, "limit": limit})
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == limit
        counts.append(len(statements))

    assert counts[0] == counts[1], counts
