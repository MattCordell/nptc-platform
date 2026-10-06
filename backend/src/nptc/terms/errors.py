"""The refusals the terms feature raises. Each carries an `http_status` that
`nptc.api.errors` maps (`test_api_error_table.py` fails the build for one it does not)."""

from __future__ import annotations

from typing import ClassVar


class TermsVersionNotFoundError(Exception):
    """No terms file exists for the requested version. A malformed version is the same
    refusal, so a request never reaches the filesystem with a caller-built path."""

    http_status: ClassVar[int] = 404


class TermsFileMalformedError(Exception):
    """A terms file is present but its front matter is missing, or does not match its file
    name. Raised at start-up for the current version, so a bad file never reaches a user."""


class TermsAcceptanceRequiredError(Exception):
    """A signed-in user has not accepted the current terms version, and the route is not on the
    exempt list (ADR-0043)."""

    http_status: ClassVar[int] = 403


class TermsVersionStaleError(Exception):
    """The accept request named a version other than the current one, so accepting would record
    a text the user never saw (NFR-47). Carries the current version for the response."""

    http_status: ClassVar[int] = 409

    def __init__(self, *, submitted_version: str, current_version: str) -> None:
        super().__init__(
            f"terms version {submitted_version!r} is not the current version {current_version!r}"
        )
        self.submitted_version = submitted_version
        self.current_version = current_version
