"""Issue #142's acceptance criteria, asserted on raw response text
(FR-06, NFR-04).

Two invariants, and both are asserted **whole-body against
`response.text`**, never field by field against a parsed model:

1. No internal UUID appears anywhere in any response.
2. Every SNOMED CT code is a JSON string, never a JSON number.

Per-field assertions cannot establish either one. A field-by-field check
proves things about the fields somebody thought to name, and the defect
being guarded against is exactly a field nobody thought about -
`replaced_by_binding_id` today, a `created_by` or an `entry_id` on some
future sub-resource tomorrow. Scanning the whole body has no such blind
spot.

**The endpoint list is derived from the route table**, not typed out here,
so an endpoint added to `routers/catalogue.py` next month is covered by
these assertions on the day it is added rather than the day somebody
remembers to extend a list.

**Both regexes ship with a positive control.** A regex asserted only
against bodies that are already clean is indistinguishable from a regex
that matches nothing at all - the exact way this genre of test rots into
one that always passes. `test_the_uuid_pattern_actually_matches_a_uuid` and
`test_the_unquoted_code_pattern_actually_matches_a_numeric_code` run the
same patterns over fabricated bodies that *should* fail, so a refactor that
breaks the pattern breaks this module loudly.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
_seed = _load("public_catalogue_support")

build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp
API_PREFIX = _api_support.API_PREFIX
seed_public_catalogue = _seed.seed_public_catalogue
SeededCatalogue = _seed.SeededCatalogue

#: Any RFC 4122-shaped identifier, in any casing. Deliberately not anchored
#: and deliberately not tied to a particular field name: the point is that
#: no such string appears anywhere in a body, whatever it is called.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: A JSON *number* of six or more digits - i.e. a digit run that is not
#: inside a quoted string. `(?<!")` / `(?!")` are what make that
#: distinction: `"391483001"` is a correctly-serialised SCTID and must not
#: match, while `391483001` (bare, as a JSON number) must. Six digits is
#: the floor because that is the shortest legal SCTID
#: (`nptc_shared.sctid`'s own `^[0-9]{6,18}$`).
#:
#: The `(?<![0-9.\-])`/`(?![0-9.])` guards keep this from firing on a digit
#: run that is part of a longer number or a decimal - a `score` of
#: `0.4166666666666667` contains a sixteen-digit run after the point, and is
#: not an SCTID serialised as a number.
_UNQUOTED_LONG_NUMBER_RE = re.compile(r'(?<!")(?<![0-9.\-])[0-9]{6,}(?![0-9.])(?!")')


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


def _catalogue_paths(api: ApiTestApp, seeded: SeededCatalogue) -> list[str]:
    """Every route under the catalogue prefix, with its parameters filled
    in - derived from the app's own OpenAPI document, so a new endpoint is
    covered automatically.

    The document rather than `app.routes`: FastAPI wraps an included router
    in an opaque `_IncludedRouter` entry, so walking `app.routes` and
    filtering to `APIRoute` finds nothing at all (see this PR's note on
    `route_inventory_support.py`, which had the same latent problem). The
    OpenAPI paths are the same route table, flattened, and they are what
    clients are generated from anyway.

    Fails loudly on a path parameter this function does not know how to
    fill, rather than skipping the route: silently skipping is how "every
    endpoint is covered" quietly stops being true.
    """
    paths: list[str] = []
    for template in api.app.openapi()["paths"]:
        if not template.startswith(f"{API_PREFIX}/catalogue"):
            continue
        path = template[len(API_PREFIX) :].replace("{business_key}", seeded.canonical)
        unfilled = re.findall(r"\{([^}]+)\}", path)
        assert not unfilled, f"{template}: no fixture value for path parameter(s) {unfilled}"
        if path.endswith("/search"):
            path = f"{path}?q={_seed.CANONICAL_TERM.replace(' ', '+')}&limit=200"
        elif path.endswith("/entries"):
            path = f"{path}?after={seeded.before_all}&limit=200"
        paths.append(path)
    assert len(paths) >= 6, f"expected every catalogue route to be discovered, found {paths}"
    return paths


# --- the two acceptance criteria ------------------------------------------


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_no_response_body_contains_an_internal_uuid(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The whole-body form of PRD SS6.2. The canonical entry has a retired
    binding whose `replaced_by_binding_id` *is* a UUID in the database, so
    this is a live assertion rather than a formality: a response model that
    passed the column straight through would fail here."""
    for path in _catalogue_paths(api, seeded):
        response = api.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        found = _UUID_RE.search(response.text)
        assert found is None, f"{path}: response body contains a UUID: {found and found.group()}"


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_no_response_body_serialises_a_code_as_a_json_number(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """FR-06 on the wire. An SCTID that reached a client as a JSON number
    would already have passed through a JavaScript `number` by the time
    anyone noticed, and `9999999999999999999` does not survive that trip -
    which is the whole reason this platform exists.

    The only numbers these endpoints serve are `length`, `ordinal`, `score`
    and one `positiveInt` property value, and the fixture keeps every one of
    them below six digits (see `public_catalogue_support.VOLUME_VALUE`), so
    any six-digit-or-longer bare number in a body is a code that lost its
    quotes.
    """
    for path in _catalogue_paths(api, seeded):
        response = api.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        found = _UNQUOTED_LONG_NUMBER_RE.search(response.text)
        assert found is None, (
            f"{path}: response body contains an unquoted long number "
            f"{found and found.group()!r} - a code serialised as a JSON number (FR-06)"
        )


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_the_codes_are_actually_present_so_the_scan_proves_something(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The companion the two scans above need. Both assert an *absence*, and
    an absence over an empty or error body is trivially satisfied - so this
    pins that the seeded codes really are in the binding response, quoted."""
    text = api.get(f"/catalogue/entries/{seeded.canonical}/bindings").text

    assert f'"{_seed.ACTIVE_CODE}"' in text
    assert f'"{_seed.RETIRED_CODE}"' in text


# --- positive controls for both patterns ----------------------------------


def test_the_uuid_pattern_actually_matches_a_uuid() -> None:
    assert _UUID_RE.search('{"id": "0f6c2e1a-4b7d-4c9e-8a1f-2b3c4d5e6f70"}')
    assert _UUID_RE.search('{"replaced_by_binding_id":"0F6C2E1A-4B7D-4C9E-8A1F-2B3C4D5E6F70"}')
    assert _UUID_RE.search('{"business_key": "NPTC-000247"}') is None


def test_the_unquoted_code_pattern_actually_matches_a_numeric_code() -> None:
    """The exact defect FR-06 names, and the near-misses that must not be
    mistaken for it."""
    assert _UNQUOTED_LONG_NUMBER_RE.search('{"code": 391483001}')
    assert _UNQUOTED_LONG_NUMBER_RE.search('{"items": [71388002]}')

    # Correctly quoted: not a violation.
    assert _UNQUOTED_LONG_NUMBER_RE.search('{"code": "391483001"}') is None
    # Small numbers are the fields this API legitimately serves as numbers.
    assert _UNQUOTED_LONG_NUMBER_RE.search('{"length": 27, "ordinal": 0}') is None
    # A float score's fractional digits are not an SCTID.
    assert _UNQUOTED_LONG_NUMBER_RE.search('{"score": 0.4166666666666667}') is None


# --- UI parity (issue #142's acceptance criterion) ------------------------

#: Transcribed from the routed public catalogue pages in
#: `frontend/src/router/route-tree.ts` - `/catalogue` (FR-14..16, FR-18) and
#: `/catalogue/$businessKey` (FR-17) - and from what those screens must
#: render to be the screens PRD SS6 describes. Deliberately a transcription
#: with this comment rather than something derived from the TypeScript: the
#: pages are placeholders today (#138/#140/#141 own them), so there is
#: nothing to derive from yet, and an under-specified frozenset would let
#: the API ship missing a field the UI will need.
REQUIRED_PUBLIC_FIELDS = frozenset(
    {
        # The entry itself, and FR-85's published Length.
        "business_key",
        "preferred_term",
        "length",
        "status",
        # FR-89: "accepts any specimen" as a positive statement.
        "specimen_unconstrained",
        "updated_at",
        # The three panels a detail page shows.
        "designations",
        "bindings",
        "properties",
    }
)


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_the_detail_response_carries_every_field_the_public_ui_needs(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """FR-20's "the same content as the public UI", asserted against the
    entry that carries every awkward shape at once - synonyms, several
    values for one property, and a retired binding with a reason and a
    successor.

    A superset check, not an equality check: the API is allowed to serve
    more than the UI renders (a vendor is the primary audience, and FR-20 is
    not scoped to what the SPA happens to show), but never less.
    """
    body = api.get(f"/catalogue/entries/{seeded.canonical}").json()

    missing = REQUIRED_PUBLIC_FIELDS - set(body)
    assert not missing, f"detail response is missing UI fields: {sorted(missing)}"
    # And the awkward shapes are genuinely populated, so the field check
    # above is not passing over three empty lists.
    assert len(body["designations"]) >= 2
    assert len(body["properties"]) >= 3
    assert {binding["status"] for binding in body["bindings"]} == {"active", "retired"}
