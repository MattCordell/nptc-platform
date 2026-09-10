"""HTTP tests for `nptc.api.routers.catalogue_admin`'s all-status listing
and search (issue #266, FR-14, FR-15, FR-16, FR-36, FR-44).

`test_api_catalogue_admin_read.py` proves the single-entry admin read
(issue #228); this module proves its collection counterpart. The point of
every test here is the same one that module states: a draft, deprecated or
withdrawn entry is reachable through `GET /catalogue/admin/entries` and
`GET /catalogue/admin/search` for a caller holding
`catalogue.edit_published`, while the public `/catalogue/entries` and
`/catalogue/search` stay exactly as restricted as
`test_api_public_status_filter.py` already proves - both halves asserted in
the same test wherever practical, so a regression in either direction fails
here rather than passing on the strength of the other.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.maintenance import MAINTENANCE_STATUSES
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


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
seed_public_catalogue = _seed.seed_public_catalogue
SeededCatalogue = _seed.SeededCatalogue


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


@dataclass(frozen=True)
class SortableCatalogue:
    """A small, fully-controlled fixture for issue #287's sort tests.

    `SeededCatalogue`'s own `preferred_term`/`status`/`updated_at` values are
    not designed to prove an ordering - reusing it here would couple every
    other test in this file to a sort-specific shape, the same reason
    `seed_worked_example`/`seed_code_lookup_fixtures` are their own fixtures
    rather than additions to it.
    """

    #: Business keys in insertion (and so `business_key`) order.
    by_business_key: tuple[str, ...]
    #: In `preferred_term_key` order, tie-broken by `business_key` - proves
    #: the guaranteed tie (the two `"Same term"` rows) resolves the same way
    #: the statement's own `ORDER BY` does.
    by_preferred_term: tuple[str, ...]
    by_updated_at: tuple[str, ...]
    #: In `status` order, tie-broken by `business_key` - proves the
    #: guaranteed tie (the three `active` rows).
    by_status: tuple[str, ...]


def _seed_sortable(session: Session) -> SortableCatalogue:
    base = random.randrange(100_000_000, 999_000_000)

    def key(offset: int) -> str:
        return f"NPTC-{base + offset}"

    # (offset, preferred_term, status) - three `active` rows (a guaranteed
    # status tie among them), one each of the other three statuses, and two
    # rows sharing a `preferred_term` (a guaranteed preferred_term tie).
    rows = (
        (0, "Charlie term", CatalogueEntryStatus.WITHDRAWN.value),
        (1, "Alpha term", CatalogueEntryStatus.DRAFT.value),
        (2, "Delta term", CatalogueEntryStatus.DEPRECATED.value),
        (3, "Bravo term", CatalogueEntryStatus.ACTIVE.value),
        (4, "Same term", CatalogueEntryStatus.ACTIVE.value),
        (5, "Same term", CatalogueEntryStatus.ACTIVE.value),
    )
    entries = [
        CatalogueEntry(business_key=key(offset), preferred_term=term, status=status)
        for offset, term, status in rows
    ]
    session.add_all(entries)
    session.flush()

    # Staggered, distinct `updated_at` values, deliberately in the *reverse*
    # of `business_key` order - so a `sort=updated_at` page proves a real
    # ordering, not one that happens to coincide with `business_key`'s.
    anchor = datetime.now(UTC)
    for index, entry in enumerate(entries):
        session.execute(
            update(CatalogueEntry)
            .where(CatalogueEntry.id == entry.id)
            .values(updated_at=anchor - timedelta(seconds=index))
        )
    session.flush()

    by_business_key = tuple(entry.business_key for entry in entries)
    by_preferred_term = tuple(
        entry.business_key
        for entry in sorted(
            entries, key=lambda entry: (entry.preferred_term_key, entry.business_key)
        )
    )
    by_status = tuple(
        entry.business_key
        for entry in sorted(entries, key=lambda entry: (entry.status, entry.business_key))
    )
    return SortableCatalogue(
        by_business_key=by_business_key,
        by_preferred_term=by_preferred_term,
        by_updated_at=tuple(reversed(by_business_key)),
        by_status=by_status,
    )


@pytest.fixture
def sortable(api: ApiTestApp) -> SortableCatalogue:
    return _seed_sortable(api.session)


def _token_with_role(api: ApiTestApp, *, subject: str, role: Role, with_mfa: bool = True) -> str:
    """Matching `test_api_catalogue_admin_read.py`'s own helper of the same
    name - duplicated rather than imported, following this test tree's
    convention that a `test_*.py` module is never imported by another."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    return _token_with_role(api, subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa)


def _admin_list(api: ApiTestApp, token: str | None, **params: Any) -> Any:
    return api.get("/catalogue/admin/entries", token=token, params=params)


def _admin_search(api: ApiTestApp, token: str | None, **params: Any) -> Any:
    return api.get("/catalogue/admin/search", token=token, params=params)


# --- MAINTENANCE_STATUSES itself --------------------------------------------


def test_maintenance_statuses_is_every_catalogue_entry_status() -> None:
    """A guard on the constant, matching `test_api_public_status_filter.py`'s
    own guard on `PUBLIC_STATUSES` - if this silently narrowed, every test
    below parametrised or asserted against it would start proving less than
    it looks like it does."""
    assert set(MAINTENANCE_STATUSES) == {status.value for status in CatalogueEntryStatus}


# --- visibility parity: both surfaces asserted together ---------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_hidden_entries_are_in_the_admin_listing_and_absent_from_the_public_one(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-list-visibility")

    # No `after`: issue #287 binds the admin listing cursor to a digest, so
    # it can no longer be a hand-constructed `before_all` sentinel the way
    # the public route's own bare-`business_key` cursor still can (see
    # `public_catalogue_support.py`'s own docstring on that convention).
    # Each test's `app_db` transaction is rolled back afterwards (`conftest.
    # py`), so `seeded` is the only `catalogue_entry` data this request can
    # see regardless.
    admin_response = _admin_list(api, token, limit=200)
    assert admin_response.status_code == 200, admin_response.text
    admin_keys = {item["business_key"] for item in admin_response.json()["items"]}
    assert set(seeded.hidden) <= admin_keys
    assert seeded.canonical in admin_keys

    public_response = api.get(
        "/catalogue/entries", params={"after": seeded.before_all, "limit": 200}
    )
    assert public_response.status_code == 200, public_response.text
    public_keys = {item["business_key"] for item in public_response.json()["items"]}
    assert not public_keys & set(seeded.hidden)


@pytest.mark.req("FR-14")
@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_hidden_entries_are_in_the_admin_search_and_absent_from_the_public_one(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Searched by `CANONICAL_TERM`, which the three hidden fixtures carry
    near-copies of precisely so this query scores them above the threshold
    (see `public_catalogue_support.seed_public_catalogue`'s own note) - a
    query the hidden entries do not match would prove nothing about the
    status filter."""
    token = _admin_token(api, subject="sub-search-visibility")

    admin_response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)
    assert admin_response.status_code == 200, admin_response.text
    admin_keys = {item["business_key"] for item in admin_response.json()["items"]}
    assert set(seeded.hidden) <= admin_keys
    assert seeded.canonical in admin_keys

    public_response = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 200})
    assert public_response.status_code == 200, public_response.text
    public_keys = {item["business_key"] for item in public_response.json()["items"]}
    assert not public_keys & set(seeded.hidden)


@pytest.mark.req("FR-36")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "deprecated", "withdrawn", "active"])
def test_each_row_carries_its_own_status(
    api: ApiTestApp, seeded: SeededCatalogue, status: str
) -> None:
    """Issue #266's own acceptance criterion: `status` is present on every
    maintenance row, so a caller can tell a draft from an active entry
    without a second call. Parametrised over every `CatalogueEntryStatus`,
    driven off the enum, so a fifth status fails this test rather than
    shipping untested."""
    business_key = {
        "draft": seeded.draft,
        "deprecated": seeded.deprecated,
        "withdrawn": seeded.withdrawn,
        "active": seeded.canonical,
    }[status]
    token = _admin_token(api, subject=f"sub-status-{status}")

    response = _admin_list(api, token, limit=200)

    assert response.status_code == 200, response.text
    rows = {item["business_key"]: item["status"] for item in response.json()["items"]}
    assert rows[business_key] == status


# --- keyset paging (ADR-0024) ------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_the_listing_pages_with_no_offset_and_a_null_cursor_on_the_last_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-list-paging")

    first = _admin_list(api, token, limit=1)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"] is not None

    rest = _admin_list(api, token, after=first_body["next_cursor"], limit=200)
    assert rest.status_code == 200, rest.text
    rest_body = rest.json()
    assert first_body["items"][0]["business_key"] not in {
        item["business_key"] for item in rest_body["items"]
    }
    assert rest_body["next_cursor"] is None


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_a_malformed_listing_cursor_is_a_422(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    token = _admin_token(api, subject="sub-list-bad-cursor")

    response = _admin_list(api, token, after="not-a-business-key")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_malformed_search_cursor_is_a_422(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    token = _admin_token(api, subject="sub-search-bad-cursor")

    response = _admin_search(api, token, q=_seed.CANONICAL_TERM, after="not-a-cursor-at-all")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_search_cursor_replayed_under_a_different_query_is_a_422(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    token = _admin_token(api, subject="sub-search-cursor-mismatch")

    first = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=1)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    replayed = _admin_search(api, token, q="a completely different query", after=cursor)

    assert replayed.status_code == 422, replayed.text


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_search_cursor_does_not_cross_the_public_admin_status_boundary(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Issue #266 review: `_request_digest` binds a search cursor to the
    status scope it was scored under, not just to `q` and the filter set.
    Without that, a `next_cursor` minted by one surface would be accepted
    verbatim by the other for the same `q` and resume the `(score,
    business_key)` keyset over a *different* population - an administrator
    paging from a public cursor would silently skip every hidden entry
    scoring above it, rather than getting this refusal."""
    token = _admin_token(api, subject="sub-cursor-cross-surface")

    public_first = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 1})
    assert public_first.status_code == 200, public_first.text
    public_cursor = public_first.json()["next_cursor"]
    assert public_cursor is not None, "expected the public search to have a second page"

    admin_replay = _admin_search(api, token, q=_seed.CANONICAL_TERM, after=public_cursor)
    assert admin_replay.status_code == 422, admin_replay.text

    admin_first = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=1)
    assert admin_first.status_code == 200, admin_first.text
    admin_cursor = admin_first.json()["next_cursor"]
    assert admin_cursor is not None, "expected the admin search to have a second page"

    public_replay = api.get(
        "/catalogue/search", params={"q": _seed.CANONICAL_TERM, "after": admin_cursor}
    )
    assert public_replay.status_code == 422, public_replay.text


# --- the status facet is non-degenerate (unlike the public one) ------------


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_status_facet_has_more_than_one_bucket(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The public search's own `status` facet has exactly one bucket
    (`active`) because `PUBLIC_STATUSES` is one status - see
    `nptc.catalogue.facets._core_facets`'s own docstring. This surface's
    facet context is built from `MAINTENANCE_STATUSES` instead, so the same
    facet has real buckets to offer once more than one status is actually
    present in the matched result set."""
    token = _admin_token(api, subject="sub-status-facet")

    response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)

    assert response.status_code == 200, response.text
    facets = {facet["key"]: facet for facet in response.json()["facets"]}
    status_facet = facets["status"]
    assert status_facet["facetable"] is True
    bucket_values = {bucket["value"] for bucket in status_facet["buckets"]}
    assert len(bucket_values) > 1, status_facet
    assert bucket_values <= {status.value for status in CatalogueEntryStatus}


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_status_facet_count_matches_the_filtered_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`search_entries` and `search_facets` (`nptc.catalogue.search`) take
    `statuses` as two independent parameters with a "must agree" contract
    the type system does not enforce (issue #266 review) -
    `_matching_entry_ids`'s own docstring says the shared scored CTE exists
    so the page and every facet count answer the same question about the
    same population, and this is that parity check for the admin route,
    matching `test_api_public_search.py::test_a_facet_bucket_s_count_is_
    the_number_of_rows_that_bucket_returns`'s own pattern for the public
    one. A facet count computed against a differently-scoped population
    looks entirely plausible and is simply wrong."""
    token = _admin_token(api, subject="sub-status-facet-parity")

    search_response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)
    assert search_response.status_code == 200, search_response.text
    facets = {facet["key"]: facet for facet in search_response.json()["facets"]}
    status_facet = facets["status"]
    assert status_facet["buckets"], "expected at least one status bucket for this query"

    for bucket in status_facet["buckets"]:
        filtered = _admin_search(
            api,
            token,
            q=_seed.CANONICAL_TERM,
            limit=200,
            **{"filter.status": bucket["value"]},
        )
        assert filtered.status_code == 200, filtered.text
        keys = [item["business_key"] for item in filtered.json()["items"]]
        assert len(keys) == bucket["count"], (
            f"status facet bucket {bucket['value']!r} claims {bucket['count']} entries "
            f"and the filtered page returns {len(keys)}"
        )


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_filtering_by_a_hidden_status_is_accepted_and_narrows_the_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`?filter.status=draft` is a 422 on the public surface
    (`test_api_public_search.py::test_an_unrecognised_status_value_is_a_422_
    not_an_empty_page` proves `active` is the only value it accepts) - here
    it is a real filter, because `AdminFiltersDep` builds its facet context
    from `MAINTENANCE_STATUSES`."""
    token = _admin_token(api, subject="sub-status-filter")

    response = _admin_list(api, token, limit=200, **{"filter.status": "draft"})

    assert response.status_code == 200, response.text
    keys = {item["business_key"] for item in response.json()["items"]}
    assert keys == {seeded.draft}


# --- row_version on the wire (issue #267) -----------------------------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_the_listing_carries_row_version_per_row(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    """Issue #267: the maintenance list screen's row-selection surface locks
    on `(business_key, expected_row_version)` (FR-38, FR-39), so
    `AdminEntryPage`'s rows - unlike the public `EntryPage`'s own
    `EntrySummary` rows - carry the token without a second read."""
    token = _admin_token(api, subject="sub-list-row-version")

    response = _admin_list(api, token, limit=200)

    assert response.status_code == 200, response.text
    rows = {item["business_key"]: item["row_version"] for item in response.json()["items"]}
    assert rows[seeded.canonical] >= 1


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_the_search_carries_row_version_per_row(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    """The search counterpart of the test above - `AdminSearchHit` carries
    `row_version` the same way `AdminEntrySummary` does."""
    token = _admin_token(api, subject="sub-search-row-version")

    response = _admin_search(api, token, q=_seed.CANONICAL_TERM, limit=200)

    assert response.status_code == 200, response.text
    rows = {item["business_key"]: item["row_version"] for item in response.json()["items"]}
    assert rows[seeded.canonical] >= 1


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_the_public_listing_and_search_do_not_carry_row_version(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The other half of issue #267's decision: `row_version` is an
    admin-only field. `EntryDetail` (the single-entry route) already
    publishes it (`test_api_public_response_hygiene.py`'s own test) - this
    proves the two *collection* routes do not, so a public consumer never
    sees a per-row counter that exists only to serve #63's bulk reclassify
    (`AdminEntrySummary`'s own docstring)."""
    listing = api.get("/catalogue/entries", params={"after": seeded.before_all, "limit": 200})
    assert listing.status_code == 200, listing.text
    assert all("row_version" not in item for item in listing.json()["items"])

    search_response = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 200})
    assert search_response.status_code == 200, search_response.text
    assert all("row_version" not in item for item in search_response.json()["items"])


# --- server-side sort (issue #287) ------------------------------------------


@pytest.mark.req("FR-16")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("sort", "expected_attr"),
    [
        ("business_key", "by_business_key"),
        ("preferred_term", "by_preferred_term"),
        ("updated_at", "by_updated_at"),
        ("status", "by_status"),
    ],
)
def test_sorting_by_each_column_orders_the_page(
    api: ApiTestApp, sortable: SortableCatalogue, sort: str, expected_attr: str
) -> None:
    """Every `sort` value orders the whole page correctly, including through
    its own guaranteed tie (`preferred_term`'s two `"Same term"` rows,
    `status`'s three `active` rows) - `business_key` is always the
    tie-break, matching `build_listing_statement`'s own `ORDER BY <sort>,
    business_key`."""
    token = _admin_token(api, subject=f"sub-sort-{sort}")

    response = _admin_list(api, token, sort=sort, limit=200)

    assert response.status_code == 200, response.text
    keys = tuple(item["business_key"] for item in response.json()["items"])
    assert keys == getattr(sortable, expected_attr)


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_explicit_business_key_sort_matches_the_default(
    api: ApiTestApp, sortable: SortableCatalogue
) -> None:
    """`sort=business_key` and omitting `sort` produce byte-identical
    responses - the "default-vs-explicit parity" the plan for this issue
    calls for, proving the unified cursor grammar did not quietly special-
    case the pre-existing default."""
    token = _admin_token(api, subject="sub-sort-parity")

    default_response = _admin_list(api, token, limit=2)
    explicit_response = _admin_list(api, token, sort="business_key", limit=2)

    assert default_response.status_code == 200, default_response.text
    assert explicit_response.status_code == 200, explicit_response.text
    assert default_response.json() == explicit_response.json()


@pytest.mark.req("FR-16")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("sort", "expected_attr"),
    [
        ("business_key", "by_business_key"),
        ("preferred_term", "by_preferred_term"),
        ("updated_at", "by_updated_at"),
        ("status", "by_status"),
    ],
)
def test_paging_under_a_non_default_sort_is_stable_and_total(
    api: ApiTestApp, sortable: SortableCatalogue, sort: str, expected_attr: str
) -> None:
    """One row at a time through every `sort`, including each one's own
    guaranteed tie - no row dropped, none repeated, matching ADR-0024's
    keyset discipline for the pre-existing `business_key` ordering.

    Parametrised over every sort, not just `preferred_term`: `updated_at` is
    the one whose cursor round-trips a `datetime` rather than a bare string
    (`_format_sort_value`/`_parse_sort_value`), and `limit=200` elsewhere in
    this file never exercises that round-trip at all - a single page never
    mints or parses a cursor.
    """
    token = _admin_token(api, subject=f"sub-sort-paging-{sort}")

    seen: list[str] = []
    params: dict[str, Any] = {"sort": sort, "limit": 1}
    expected = getattr(sortable, expected_attr)
    for _ in range(len(expected) + 1):
        response = _admin_list(api, token, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["business_key"] for item in body["items"])
        after = body["next_cursor"]
        if after is None:
            break
        params["after"] = after

    assert after is None, "paging did not terminate within the fixture's own row count"
    assert tuple(seen) == expected


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_an_unrecognised_sort_value_is_a_422(api: ApiTestApp, sortable: SortableCatalogue) -> None:
    token = _admin_token(api, subject="sub-sort-unrecognised")

    response = _admin_list(api, token, sort="not-a-real-sort", limit=200)

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
@pytest.mark.parametrize("sort", ["business_key", "preferred_term", "updated_at", "status"])
def test_a_malformed_listing_cursor_is_a_422_for_every_sort(
    api: ApiTestApp, sortable: SortableCatalogue, sort: str
) -> None:
    token = _admin_token(api, subject=f"sub-sort-bad-cursor-{sort}")

    response = _admin_list(api, token, sort=sort, after="not-a-cursor-at-all", limit=200)

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_listing_cursor_replayed_under_a_different_sort_is_a_422(
    api: ApiTestApp, sortable: SortableCatalogue
) -> None:
    """The acceptance criterion this issue's plan states directly: a cursor
    minted under one `sort` means nothing replayed under another, because
    `sort_value > :after` compares against a different column."""
    token = _admin_token(api, subject="sub-sort-cursor-mismatch")

    first = _admin_list(api, token, sort="preferred_term", limit=1)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    replayed = _admin_list(api, token, sort="updated_at", after=cursor, limit=200)

    assert replayed.status_code == 422, replayed.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_listing_cursor_replayed_under_a_different_filter_set_is_a_422(
    api: ApiTestApp, sortable: SortableCatalogue
) -> None:
    """The filter-set half of the digest, proved independently of the sort
    case above (issue #287's own plan: "as an independent case, not combined
    with the sort case, so a bug checking only one axis can't hide behind
    the other")."""
    token = _admin_token(api, subject="sub-sort-filter-mismatch")

    first = _admin_list(api, token, limit=1, **{"filter.status": "active"})
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    replayed = _admin_list(api, token, after=cursor, limit=200, **{"filter.status": "draft"})

    assert replayed.status_code == 422, replayed.text


# --- authorisation (FR-44, NFR-06, NFR-20) ----------------------------------


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp, seeded: SeededCatalogue, call: Any) -> None:
    response = call(api, None, q=_seed.CANONICAL_TERM)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_no_credential_is_401_even_with_invalid_parameters(
    api: ApiTestApp, seeded: SeededCatalogue, call: Any
) -> None:
    """The permission gate must outrank parameter validation (issue #266
    review). FastAPI resolves a route's `dependencies=[_EDIT]` as part of
    the same dependency graph as its own query parameters, and today a
    sub-dependency that raises does so before parameter validation ever
    runs - so an anonymous request 401s even carrying a cursor that is not
    a well-formed cursor at all and a `limit` outside 1-200, rather than
    422. That ordering is a fact about the dependency graph, not a
    contract this route's code states anywhere, so a later refactor could
    flip it silently; a 422 here would also tell an unauthenticated caller
    something about the surface's own parameter shapes it has no
    permission to see."""
    response = call(
        api, None, q=_seed.CANONICAL_TERM, after="not a well-formed cursor at all", limit=99999
    )

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_observer_is_403_with_no_challenge(
    api: ApiTestApp, seeded: SeededCatalogue, call: Any
) -> None:
    token = _token_with_role(api, subject=f"sub-observer-{call.__name__}", role=Role.OBSERVER)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_reviewer_is_403(api: ApiTestApp, seeded: SeededCatalogue, call: Any) -> None:
    """A Reviewer holds `validation.acknowledge` but not
    `catalogue.edit_published` - proving the gate is this specific
    permission, not "any elevated role"."""
    token = _token_with_role(api, subject=f"sub-reviewer-{call.__name__}", role=Role.REVIEWER)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text


@pytest.mark.parametrize("call", [_admin_list, _admin_search])
@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(
    api: ApiTestApp, seeded: SeededCatalogue, call: Any
) -> None:
    token = _admin_token(api, subject=f"sub-admin-no-mfa-{call.__name__}", with_mfa=False)

    response = call(api, token, q=_seed.CANONICAL_TERM)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge
