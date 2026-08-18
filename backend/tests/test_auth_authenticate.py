"""`nptc.auth.authentication.authenticate` (issue #43): the join from a
verified token to #42's `resolve_user_for_claims`.

`Session(bind=app_db)` joins the existing testcontainers fixture
connection, the same recipe `test_auth_identity_resolution.py` uses -
marked `@pytest.mark.integration` for the real Postgres it needs, even
though the token itself is verified against a local, offline stub IdP.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.authentication import authenticate
from nptc.auth.errors import TokenError
from nptc.auth.identity import LinkOutcome
from nptc.auth.jwks import SigningKeys
from nptc.auth.tokens import TokenVerifier

_support_spec = importlib.util.spec_from_file_location(
    "_test_auth_authenticate_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
running_stub_idp = _support.running_stub_idp
generate_rsa_key = _support.generate_rsa_key
mint_token = _support.mint_token


def _user_and_identity_counts(app_db: Connection) -> tuple[int, int]:
    users = app_db.execute(text("SELECT count(*) FROM app_user")).scalar_one()
    identities = app_db.execute(text("SELECT count(*) FROM user_identity")).scalar_one()
    return users, identities


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_a_verified_token_for_an_unseen_subject_creates_a_user(app_db: Connection) -> None:
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        verifier = TokenVerifier(
            issuer=stub.issuer_url, audience="nptc-api", keys=SigningKeys(stub.jwks_url)
        )
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url, subject="issue-43-subject-1")
        session = Session(bind=app_db)

        resolution = authenticate(
            session,
            token,
            verifier=verifier,
            trusted_issuers=frozenset(),
            audit=AuditContext.system(),
        )

    assert resolution.outcome == LinkOutcome.CREATED
    assert resolution.user is not None


@pytest.mark.req("NFR-07")
@pytest.mark.integration
def test_an_invalid_token_raises_and_leaves_no_rows_behind(app_db: Connection) -> None:
    """The concrete form of "no claim is trusted before the signature":
    an invalid token must not create or touch a single row."""
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        verifier = TokenVerifier(
            issuer=stub.issuer_url, audience="nptc-api", keys=SigningKeys(stub.jwks_url)
        )
        wrong_key = generate_rsa_key()
        token = mint_token(
            wrong_key, kid="key-1", issuer=stub.issuer_url, subject="issue-43-subject-2"
        )
        session = Session(bind=app_db)

        before = _user_and_identity_counts(app_db)

        with pytest.raises(TokenError):
            authenticate(
                session,
                token,
                verifier=verifier,
                trusted_issuers=frozenset(),
                audit=AuditContext.system(),
            )

        after = _user_and_identity_counts(app_db)

    assert before == after
