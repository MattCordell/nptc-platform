"""The `code`, `fsn`, `disciplines` and `specimens` fields every list, search and detail row carries.

They exist so the public catalogue screen can show a row's discipline, specimen and FSN
without a request per row. The failure modes asserted here are the ones a row-level lookup
gets wrong: a retired code or FSN shown as current, a missing code shown as anything but
`null`, a second discipline dropped, a semantic tag left on the FSN, a specimen trimmed to
nothing, and a page whose statement count grows with its size.
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
_SNOMED = "http://snomed.info/sct"


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


def _specimen_values(entry_id: Any) -> list[PropertyValue]:
    """Stored out of order, so a loader that ignored `ordinal` reorders them. Beside "Urine
    specimen" and "Serum specimen" sit a second value that trims to the same "Serum", the root
    concept's bare "Specimen", and a value with no display."""
    stored = [
        (2, {"system": _SNOMED, "code": "119361006", "display": "Serum"}),
        (0, {"system": _SNOMED, "code": "122575003", "display": "Urine specimen"}),
        (4, {"system": _SNOMED, "code": "309051001"}),
        (1, {"system": _SNOMED, "code": "119364003", "display": "Serum specimen"}),
        (3, {"system": _SNOMED, "code": "123038009", "display": "Specimen"}),
    ]
    return [
        PropertyValue(entry_id=entry_id, property_key="specimen", ordinal=ordinal, value=value)
        for ordinal, value in stored
    ]


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
            *_specimen_values(bound.id),
        ]
    )
    session.flush()
    return seeded


#: `fsn` is the active binding's, tag removed; the retired binding's FSN never appears.
_EXPECTED: dict[str, tuple[str | None, str | None, list[str], list[str]]] = {
    "bound": (
        _ACTIVE_CODE,
        "Microscopy (acid fast bacilli)",
        ["Chemical pathology", "haem"],
        ["Urine", "Serum", "Specimen", "309051001"],
    ),
    "retired_only": (None, None, [], []),
    "unbound": (None, None, [], []),
}


def _assert_rows(seeded: _Seeded, items: list[dict[str, Any]], *, summary: bool = True) -> None:
    """`summary` is `False` for a detail, which carries `code` and `disciplines` but neither
    `fsn` nor `specimens`."""
    by_key = {item["business_key"]: item for item in items}
    for handle, (code, fsn, disciplines, specimens) in _EXPECTED.items():
        row = by_key[getattr(seeded, handle)]
        assert row["code"] == code, handle
        assert row["disciplines"] == disciplines, handle
        if summary:
            assert row["fsn"] == fsn, handle
            assert row["specimens"] == specimens, handle
        else:
            assert "fsn" not in row and "specimens" not in row, handle


@pytest.mark.req("FR-20")
@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_anonymous_list_rows_carry_the_code_fsn_disciplines_and_specimens(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    response = api.get("/catalogue/entries", params={"after": seeded.before_all, "limit": 3})

    assert response.status_code == 200, response.text
    _assert_rows(seeded, response.json()["items"])
    # FR-06: the code is a JSON string on the wire, never a number.
    assert f'"code":"{_ACTIVE_CODE}"' in response.text


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_anonymous_search_hits_carry_the_code_fsn_disciplines_and_specimens(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    response = api.get("/catalogue/search", params={"q": f"Ferritin {seeded.token}"})

    assert response.status_code == 200, response.text
    _assert_rows(seeded, response.json()["items"])


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_detail_carries_the_same_fields_as_its_row(api: ApiTestApp, seeded: _Seeded) -> None:
    items = [api.get(f"/catalogue/entries/{key}").json() for key in seeded.keys]

    _assert_rows(seeded, items, summary=False)
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
    _assert_rows(seeded, found.json()["items"], summary=False)
    _assert_rows(seeded, details, summary=False)


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


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_the_list_declares_which_labels_it_stripped_and_which_it_did_not(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    items = api.get("/catalogue/entries", params={"after": seeded.before_all, "limit": 3}).json()[
        "items"
    ]
    row = next(item for item in items if item["business_key"] == seeded.bound)

    assert row["label_provenance"] == {
        "preferred_term": {"designation": "au_preferred_term", "semantic_tag": "not_applicable"},
        "fsn": {"designation": "fsn", "semantic_tag": "stripped"},
        "specimens": {"designation": "au_preferred_term", "semantic_tag": "not_applicable"},
    }
    # The binding keeps its tag and says so: the strip is the summary's, never the stored value's.
    detail = api.get(f"/catalogue/entries/{seeded.bound}").json()
    active = next(b for b in detail["bindings"] if b["status"] == "active")
    assert active["fsn"] == "Microscopy (acid fast bacilli) (procedure)"
    assert active["label_provenance"]["fsn"]["semantic_tag"] == "intact"


@pytest.mark.req("FR-83")
@pytest.mark.integration
def test_a_stored_fsn_with_no_tag_fails_the_list_rather_than_showing_it(
    api: ApiTestApp,
) -> None:
    """FR-82 makes this unreachable, so reaching it means stored data broke the guarantee. An
    untagged value may already have been stripped, so the list refuses it instead of showing it."""
    session = api.session
    base = random.randrange(100_000_000, 999_000_000)
    key = f"NPTC-{base}"
    entry = CatalogueEntry(
        business_key=key,
        preferred_term=f"Untagged {base}",
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    session.add(entry)
    session.flush()
    session.add(
        CodeBinding(
            entry_id=entry.id,
            code=_ACTIVE_CODE,
            fsn="Microscopy without a tag",
            au_preferred_term=None,
            edition_hint="au",
            status=CodeBindingStatus.ACTIVE.value,
        )
    )
    session.flush()

    response = api.get("/catalogue/entries", params={"after": f"NPTC-{base - 1}", "limit": 1})

    assert response.status_code == 422, response.text
    assert "Microscopy without a tag" not in response.text
    # The entry still opens, so it can be repaired.
    assert api.get(f"/catalogue/entries/{key}").status_code == 200
