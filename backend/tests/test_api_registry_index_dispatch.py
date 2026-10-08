"""Which registry writes queue an index reconciliation (FR-13, FR-09).

Create and deprecate always queue one, because they add or remove a property whose index may
exist. An amendment queues one only when it sets `filterable`, the one amendable field an index
depends on (`datatype` is not amendable over HTTP). A request that fails queues none. The queued run itself is covered by
`test_property_reconciler_dispatch.py`; the harness replaces the worker thread with a list
(`reconciliation_submissions` in conftest), so no reconciliation runs here.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable, Iterator
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
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

_REASON = "Created for the index dispatch route tests."


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _create(api: ApiTestApp, token: str, **overrides: object) -> Any:
    key = f"dispatch_{uuid.uuid4().hex[:8]}"
    body: dict[str, object] = {
        "key": key,
        "label": key,
        "datatype": "string",
        "cardinality": "0..1",
        "scope": "both",
        "display_order": 0,
        "reason": _REASON,
    }
    body.update(overrides)
    return api.post("/registry/properties", token=token, json=body)


def _amend(api: ApiTestApp, token: str, created: dict[str, Any], **changes: object) -> Any:
    return api.request(
        "PATCH",
        f"/registry/properties/{created['key']}",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON, **changes},
    )


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_creating_a_property_queues_a_reconciliation(
    api: ApiTestApp, reconciliation_submissions: list[Callable[[], object]]
) -> None:
    token = api.admin_token(subject="sub-dispatch-create")

    response = _create(api, token, filterable=True)

    assert response.status_code == 201, response.text
    assert len(reconciliation_submissions) == 1


@pytest.mark.req("FR-13")
@pytest.mark.integration
@pytest.mark.parametrize("filterable", [True, False])
def test_amending_filterable_queues_a_reconciliation(
    api: ApiTestApp,
    reconciliation_submissions: list[Callable[[], object]],
    filterable: bool,
) -> None:
    token = api.admin_token(subject="sub-dispatch-amend")
    created = _create(api, token, filterable=not filterable).json()
    reconciliation_submissions.clear()

    response = _amend(api, token, created, filterable=filterable)

    assert response.status_code == 200, response.text
    assert len(reconciliation_submissions) == 1


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_amending_only_the_label_queues_no_reconciliation(
    api: ApiTestApp, reconciliation_submissions: list[Callable[[], object]]
) -> None:
    token = api.admin_token(subject="sub-dispatch-label")
    created = _create(api, token).json()
    reconciliation_submissions.clear()

    response = _amend(api, token, created, label="A new label")

    assert response.status_code == 200, response.text
    assert reconciliation_submissions == []


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_deprecating_a_property_queues_a_reconciliation(
    api: ApiTestApp, reconciliation_submissions: list[Callable[[], object]]
) -> None:
    token = api.admin_token(subject="sub-dispatch-deprecate")
    created = _create(api, token, filterable=True).json()
    reconciliation_submissions.clear()

    response = api.post(
        f"/registry/properties/{created['key']}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )

    assert response.status_code == 200, response.text
    assert len(reconciliation_submissions) == 1


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_a_refused_amendment_queues_no_reconciliation(
    api: ApiTestApp, reconciliation_submissions: list[Callable[[], object]]
) -> None:
    """A stale `expected_row_version` is a 409 that rolls the request back, so nothing changed
    that an index could depend on."""
    token = api.admin_token(subject="sub-dispatch-refused")
    created = _create(api, token).json()
    reconciliation_submissions.clear()

    response = _amend(
        api, token, {**created, "row_version": created["row_version"] + 99}, filterable=True
    )

    assert response.status_code == 409, response.text
    assert reconciliation_submissions == []


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_two_rapid_amendments_queue_one_reconciliation(
    api: ApiTestApp, reconciliation_submissions: list[Callable[[], object]]
) -> None:
    token = api.admin_token(subject="sub-dispatch-rapid")
    created = _create(api, token).json()
    reconciliation_submissions.clear()

    first = _amend(api, token, created, filterable=True).json()
    second = _amend(api, token, first, filterable=False)

    assert second.status_code == 200, second.text
    assert len(reconciliation_submissions) == 1
