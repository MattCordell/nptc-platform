"""NFR-05 auto-link predicate tests (issue #42) - pure unit, no Docker.

`may_auto_link` gates whether an unrecognised OIDC subject may be linked
to an existing account automatically. Each parametrised case below is a
concrete instance of the PRD's stated failure mode: auto-linking on an
unverified email claim (or an issuer that merely looks trusted) lets
anyone who can mint such a token inherit an existing account's privileges.
"""

from __future__ import annotations

import pytest

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.linking import may_auto_link

_TRUSTED = frozenset({"https://good.example"})


def _claims(*, issuer: str, email: str | None, email_verified: object) -> OidcIdentityClaims:
    return OidcIdentityClaims(
        issuer=issuer,
        subject="subject-1",
        email=email,
        email_verified=email_verified,  # type: ignore[arg-type]
        preferred_username=None,
        display_name=None,
    )


@pytest.mark.req("NFR-05")
@pytest.mark.parametrize(
    ("issuer", "email_verified", "expected"),
    [
        ("https://good.example", True, True),
        ("https://good.example", False, False),
        ("https://untrusted.example", True, False),
        ("https://untrusted.example", False, False),
    ],
)
def test_auto_link_requires_both_trusted_issuer_and_verified_email(
    issuer: str, email_verified: bool, expected: bool
) -> None:
    claims = _claims(issuer=issuer, email="user@example.com", email_verified=email_verified)

    assert may_auto_link(claims, _TRUSTED) is expected


@pytest.mark.req("NFR-05")
@pytest.mark.parametrize(
    "issuer",
    ["https://good.example.attacker.com", "https://evil/?x=https://good.example"],
)
def test_trusted_issuer_matching_is_exact_not_prefix_or_substring(issuer: str) -> None:
    claims = _claims(issuer=issuer, email="user@example.com", email_verified=True)

    assert may_auto_link(claims, _TRUSTED) is False


@pytest.mark.req("NFR-05")
def test_email_verified_must_be_literally_true_not_merely_truthy() -> None:
    claims = _claims(
        issuer="https://good.example", email="user@example.com", email_verified="false"
    )

    assert may_auto_link(claims, _TRUSTED) is False


@pytest.mark.req("NFR-05")
def test_empty_trusted_issuer_set_never_auto_links() -> None:
    claims = _claims(issuer="https://good.example", email="user@example.com", email_verified=True)

    assert may_auto_link(claims, frozenset()) is False
