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
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.entries import create_entry
from nptc.db.models.user import User
from nptc_shared.terminology import AU_LANGUAGE_TAG, StubConcept


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
    for template, methods in api.app.openapi()["paths"].items():
        if not template.startswith(f"{API_PREFIX}/catalogue"):
            continue
        if "get" not in methods:
            # issue #219's write routes (bind/retire/replace) live under
            # this same prefix but take no GET - covered by their own
            # hygiene test below instead, since a GET-only scanner cannot
            # fill their `{code}` parameter or supply a request body.
            continue
        if "catalogue-admin" in methods["get"].get("tags", ()):
            # issue #228's admin read route is a GET under this same prefix,
            # but - unlike the public routes this scanner exists to cover -
            # it 401s an anonymous caller by design. Its own hygiene
            # coverage is `test_admin_entry_response_contains_no_uuid_and_
            # no_unquoted_code` below, which authenticates first.
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


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_the_public_detail_carries_row_version_too(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`EntryDetail` is one shape across the public route and the #228 admin
    one, and issue #227 put `row_version` on it. Asserted *here*, on the
    public route, and not only on the admin one: if someone later narrows
    the public model to hide the field, that invariant is what breaks, and
    this is the test that should say so.

    It is deliberately published (see `catalogue.py`'s own module rule and
    `docs/architecture/public-api.md`): FR-38's counter names no row, so it
    is not the class of internal identifier that rule excludes. A read-only
    consumer can ignore it."""
    body = api.get(f"/catalogue/entries/{seeded.canonical}").json()

    assert body["row_version"] >= 1


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


# --- issue #219's write routes: the same two invariants ------------------


#: The exact hazard this module's own docstring names: `replaced_by_binding_id`
#: was the column that had to be resolved away for the *read* routes, and
#: issue #219's replacement route is the first write route with the same
#: shape - `superseded.replaced_by_binding_id` is set by `link_replacement`
#: inside the same request. This test proves the write response never
#: serves the id it just wrote, not merely the id an unrelated read never
#: had.
@pytest.mark.req("NFR-04")
@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_binding_write_responses_contain_no_uuid_and_no_unquoted_code(
    api: ApiTestApp,
) -> None:
    token = api.token(subject="sub-write-hygiene")
    api.get("/auth/me", token=token)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    admin_token = api.token(subject="sub-write-hygiene", extra_claims={"acr": "2"})

    business_key = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term="Write hygiene fixture",
        reason="Created for issue #219 hygiene test",
    ).business_key
    api.session.flush()

    bind_response = api.post(
        f"/catalogue/entries/{business_key}/bindings",
        token=admin_token,
        json={
            "code": _seed.ACTIVE_CODE,
            "fsn": _seed.ACTIVE_FSN,
            "reason": "Bound for the write-hygiene test.",
        },
    )
    replace_response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{_seed.ACTIVE_CODE}/replacement",
        token=admin_token,
        json={
            "successor": {"code": _seed.RETIRED_CODE, "fsn": _seed.RETIRED_FSN},
            "reason": "Replaced for the write-hygiene test.",
        },
    )

    for response in (bind_response, replace_response):
        assert response.status_code in (200, 201), response.text
        found_uuid = _UUID_RE.search(response.text)
        assert found_uuid is None, f"{response.request.url}: {found_uuid and found_uuid.group()}"
        found_number = _UNQUOTED_LONG_NUMBER_RE.search(response.text)
        assert found_number is None, (
            f"{response.request.url}: unquoted long number {found_number and found_number.group()!r}"
        )


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_designation_write_responses_contain_no_uuid(api: ApiTestApp) -> None:
    """The designation analogue of the binding test above (issue #224).
    No `_UNQUOTED_LONG_NUMBER_RE` check here - a designation carries no
    SNOMED CT code, so that half of the binding test's assertion has
    nothing to scan for."""
    token = api.token(subject="sub-designation-write-hygiene")
    api.get("/auth/me", token=token)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    admin_token = api.token(subject="sub-designation-write-hygiene", extra_claims={"acr": "2"})

    business_key = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term="Designation write hygiene fixture",
        reason="Created for issue #224 hygiene test",
    ).business_key
    api.session.flush()

    add_response = api.post(
        f"/catalogue/entries/{business_key}/designations",
        token=admin_token,
        json={"terms": ["FBC"], "reason": "Added for the designation write-hygiene test."},
    )
    amend_response = api.post(
        f"/catalogue/entries/{business_key}/designations/amendment",
        token=admin_token,
        json={
            "term": "FBC",
            "new_term": "Full Blood Count",
            "reason": "Amended for the designation write-hygiene test.",
        },
    )
    retire_response = api.post(
        f"/catalogue/entries/{business_key}/designations/retirement",
        token=admin_token,
        json={
            "term": "Full Blood Count",
            "reason": "Retired for the designation write-hygiene test.",
        },
    )
    ack_response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=admin_token,
        json={
            "term": "Full Blood Count",
            "reason": "Acknowledged for the designation write-hygiene test.",
        },
    )

    for response in (add_response, amend_response, retire_response, ack_response):
        assert response.status_code in (200, 201), response.text
        found_uuid = _UUID_RE.search(response.text)
        assert found_uuid is None, f"{response.request.url}: {found_uuid and found_uuid.group()}"


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_property_value_write_response_contains_no_uuid(api: ApiTestApp) -> None:
    """The property-value write analogue of the binding/designation write
    hygiene tests above (issue #248)."""
    token = api.token(subject="sub-property-write-hygiene")
    api.get("/auth/me", token=token)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    admin_token = api.token(subject="sub-property-write-hygiene", extra_claims={"acr": "2"})

    key = f"hygiene_property_{uuid.uuid4().hex[:8]}"
    create_response = api.post(
        "/registry/properties",
        token=admin_token,
        json={
            "key": key,
            "label": "Hygiene property",
            "datatype": "string",
            "cardinality": "0..1",
            "scope": "both",
            "display_order": 0,
            "reason": "Created for issue #248 write-hygiene test.",
        },
    )
    assert create_response.status_code == 201, create_response.text

    entry = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term="Property write hygiene fixture",
        reason="Created for issue #248 hygiene test",
    )
    api.session.flush()

    save_response = api.request(
        "PUT",
        f"/catalogue/entries/{entry.business_key}/properties/{key}",
        token=admin_token,
        json={
            "values": [{"value": "a recorded value"}],
            "reason": "Saved for the property write-hygiene test.",
            "expected_row_version": entry.row_version,
        },
    )

    assert save_response.status_code == 200, save_response.text
    found_uuid = _UUID_RE.search(save_response.text)
    assert found_uuid is None, f"{save_response.request.url}: {found_uuid and found_uuid.group()}"


# --- issue #228's admin read route: the same two invariants, on a draft --


@pytest.mark.req("NFR-04")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_admin_entry_response_contains_no_uuid_and_no_unquoted_code(api: ApiTestApp) -> None:
    """`_catalogue_paths` above deliberately excludes this route (it 401s
    an anonymous caller by design), so it needs its own authenticated
    coverage of the same two acceptance criteria the public routes get -
    on a `draft` entry specifically, which is the whole point of issue
    #228: a replaced binding's `replaced_by_binding_id` *is* a UUID in the
    database whether the entry is published or not, and this route is the
    first place that column is ever rendered for an unpublished entry."""
    token = api.token(subject="sub-admin-read-hygiene")
    api.get("/auth/me", token=token)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    admin_token = api.token(subject="sub-admin-read-hygiene", extra_claims={"acr": "2"})

    business_key = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term="Admin read hygiene fixture",
        reason="Created for issue #228 hygiene test",
    ).business_key
    api.session.flush()

    api.post(
        f"/catalogue/entries/{business_key}/bindings",
        token=admin_token,
        json={
            "code": _seed.ACTIVE_CODE,
            "fsn": _seed.ACTIVE_FSN,
            "reason": "Bound for the admin-read hygiene test.",
        },
    )
    api.post(
        f"/catalogue/entries/{business_key}/bindings/{_seed.ACTIVE_CODE}/replacement",
        token=admin_token,
        json={
            "successor": {"code": _seed.RETIRED_CODE, "fsn": _seed.RETIRED_FSN},
            "reason": "Replaced for the admin-read hygiene test.",
        },
    )

    response = api.get(f"/catalogue/admin/entries/{business_key}", token=admin_token)
    assert response.status_code == 200, response.text
    body = response.json()
    # Still a draft: the whole reason this route exists is to serve an
    # entry the public route would 404.
    assert body["status"] == "draft"

    found_uuid = _UUID_RE.search(response.text)
    assert found_uuid is None, f"response body contains a UUID: {found_uuid and found_uuid.group()}"
    found_number = _UNQUOTED_LONG_NUMBER_RE.search(response.text)
    assert found_number is None, (
        f"response body contains an unquoted long number "
        f"{found_number and found_number.group()!r} - a code serialised as a JSON number (FR-06)"
    )
    assert f'"{_seed.RETIRED_CODE}"' in response.text


# --- issue #240's terminology lookup route: the same two invariants ------


@pytest.mark.req("NFR-04")
@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_terminology_lookup_response_contains_no_uuid_and_no_unquoted_code(
    api: ApiTestApp,
) -> None:
    """`_catalogue_paths` above only ever walks `{API_PREFIX}/catalogue*`
    paths, so `/terminology/concepts/{code}` - deliberately its own prefix,
    per that router's own module docstring - needs its own authenticated
    coverage of the same two acceptance criteria (issue #240). The response
    carries an SCTID (`code`), so the unquoted-number half is a live
    assertion here too, not a formality."""
    token = api.token(subject="sub-terminology-hygiene")
    api.get("/auth/me", token=token)  # bootstraps the default Provisional grant

    api.terminology.add_concept(
        StubConcept(
            code=_seed.ACTIVE_CODE,
            fsn=_seed.ACTIVE_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
        )
    )

    response = api.get(f"/terminology/concepts/{_seed.ACTIVE_CODE}", token=token)
    assert response.status_code == 200, response.text

    found_uuid = _UUID_RE.search(response.text)
    assert found_uuid is None, f"response body contains a UUID: {found_uuid and found_uuid.group()}"
    found_number = _UNQUOTED_LONG_NUMBER_RE.search(response.text)
    assert found_number is None, (
        f"response body contains an unquoted long number "
        f"{found_number and found_number.group()!r} - a code serialised as a JSON number (FR-06)"
    )
    assert f'"{_seed.ACTIVE_CODE}"' in response.text
