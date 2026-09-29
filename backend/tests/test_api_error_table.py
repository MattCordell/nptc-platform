"""The plain-refusal table in `nptc.api.errors`.

No database and no `integration` mark: the handlers are exercised through a
bare `FastAPI` app with one route that raises whatever a test hands it.

The sweep is the point. A new domain exception that names an `http_status`
and gets no handler would surface as an unhandled 500 with a traceback, and
nothing else in the suite would notice until a route raised it.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import nptc
import nptc_shared
from nptc.api.dependencies import CredentialRequiredError, MalformedAuthorizationError
from nptc.api.errors import (
    _DETAIL_SERVER_MISCONFIGURED,
    _DETAIL_SIGN_IN_REQUIRED,
    _DETAIL_UNAUTHENTICATED,
    register_exception_handlers,
)
from nptc.catalogue.errors import EntryNotFoundError
from nptc.catalogue.local_codes import (
    InvalidLocalCodeSystemKeyError,
    InvalidMatchStrengthError,
    LocalCodeAlreadyDeprecatedError,
    LocalCodeSystemAlreadyDeprecatedError,
)
from nptc.catalogue.maintenance import ListingCursorMismatchError, MalformedListingCursorError
from nptc.catalogue.search import EmptySearchQueryError
from nptc.settings import AuthSettings
from nptc_shared.sctid import InvalidSCTIDError
from nptc_shared.terminology import TerminologyConfigError

_ERRORS_LOGGER = "nptc.api.errors"
_FIRST_PARTY = frozenset({"nptc", "nptc_shared"})


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app, AuthSettings(mfa_acr_values=frozenset({"2"})))
    return app


def _client_raising(exc: Exception) -> TestClient:
    app = _app()

    @app.get("/boom")
    def _boom() -> None:
        raise exc

    return TestClient(app)


def _import_first_party() -> None:
    for package in (nptc, nptc_shared):
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            importlib.import_module(module.name)


def _all_subclasses(cls: type[Exception]) -> set[type[Exception]]:
    found = {cls}
    for sub in cls.__subclasses__():
        found |= _all_subclasses(sub)
    return found


def _classes_with_http_status() -> list[type[Exception]]:
    _import_first_party()
    return sorted(
        (
            cls
            for cls in _all_subclasses(Exception)
            if cls.__module__.split(".")[0] in _FIRST_PARTY and hasattr(cls, "http_status")
        ),
        key=lambda cls: f"{cls.__module__}.{cls.__qualname__}",
    )


def _unhandled(app: FastAPI, classes: Iterable[type[Exception]]) -> list[type[Exception]]:
    """Classes no registered handler serves, looked up along the MRO the way
    Starlette does."""
    return [
        cls for cls in classes if not any(base in app.exception_handlers for base in cls.__mro__)
    ]


def test_every_exception_with_an_http_status_has_a_handler() -> None:
    classes = _classes_with_http_status()

    unhandled = _unhandled(_app(), classes)

    assert classes, "the sweep found no exception classes, so it proves nothing"
    assert not unhandled, (
        "add a row to _REFUSALS in nptc/api/errors.py (or a function in "
        "register_exception_handlers) for: "
        + ", ".join(f"{cls.__module__}.{cls.__qualname__}" for cls in unhandled)
    )


def test_the_sweep_flags_a_class_no_handler_serves() -> None:
    class Orphan(Exception):
        http_status: ClassVar[int] = 418

    class ChildOfHandled(EntryNotFoundError):
        pass

    assert _unhandled(_app(), [Orphan, ChildOfHandled]) == [Orphan]


@pytest.mark.parametrize(
    ("exc", "detail"),
    [
        (MalformedAuthorizationError("Basic abc"), _DETAIL_UNAUTHENTICATED),
        (CredentialRequiredError("no credential"), _DETAIL_SIGN_IN_REQUIRED),
    ],
)
def test_a_401_row_carries_the_bearer_challenge(exc: Exception, detail: str) -> None:
    response = _client_raising(exc).get("/boom")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": detail}


def test_a_row_with_no_log_message_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=_ERRORS_LOGGER):
        response = _client_raising(EmptySearchQueryError("   ")).get("/boom")

    assert response.status_code == 422
    assert not [r for r in caplog.records if r.name == _ERRORS_LOGGER]


def test_a_row_with_an_explicit_status_and_level(caplog: pytest.LogCaptureFixture) -> None:
    secret = "NPTC_TX_TIMEOUT_SECONDS='not-a-number'"

    with caplog.at_level(logging.DEBUG, logger=_ERRORS_LOGGER):
        response = _client_raising(TerminologyConfigError(secret)).get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": _DETAIL_SERVER_MISCONFIGURED}
    assert secret not in response.text
    [record] = [r for r in caplog.records if r.name == _ERRORS_LOGGER]
    assert record.levelno == logging.ERROR
    assert secret in record.getMessage()


def test_a_class_without_http_status_takes_its_status_from_the_row() -> None:
    response = _client_raising(InvalidSCTIDError("12345")).get("/boom")

    assert response.status_code == 422


def test_a_subclass_is_served_by_its_base_row(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=_ERRORS_LOGGER):
        base = _client_raising(MalformedListingCursorError("bad")).get("/boom")
        child = _client_raising(ListingCursorMismatchError("other sort")).get("/boom")

    assert child.status_code == base.status_code == 422
    assert child.json() == base.json()
    [_, child_record] = [r for r in caplog.records if r.name == _ERRORS_LOGGER]
    assert child_record.getMessage() == "listing cursor refused: ListingCursorMismatchError"


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (LocalCodeSystemAlreadyDeprecatedError("system 'zz-secret' is deprecated"), 409),
        (LocalCodeAlreadyDeprecatedError("code 'zz-secret' is deprecated"), 409),
        (InvalidLocalCodeSystemKeyError("key 'zz-secret' does not match"), 422),
        (InvalidMatchStrengthError("match strength 'zz-secret' is not allowed"), 422),
    ],
)
def test_local_code_refusals_answer_with_their_status_and_echo_nothing(
    exc: Exception, status: int
) -> None:
    response = _client_raising(exc).get("/boom")

    assert response.status_code == status
    assert set(response.json()) == {"detail"}
    assert "zz-secret" not in response.text
