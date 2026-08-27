"""Reading a Postgres constraint name back out of a SQLAlchemy
`IntegrityError` (issue #219 review).

Two call sites already needed this before this module existed -
`nptc.auth.identity._create_user`'s username-collision retry, and
`nptc.catalogue.bindings.create_binding`'s lost-race translation - each
with its own copy of the same four-line `orig`/`diag`/`constraint_name`
unwrap and the same `"23505"` literal. A third copy (#149) is what this
module exists to pre-empt.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

#: Postgres's SQLSTATE for `unique_violation` - the only class of
#: `IntegrityError` a constraint *name* can disambiguate between (a `CHECK`
#: or `NOT NULL` violation names the column, not a candidate row to
#: recover from).
UNIQUE_VIOLATION_SQLSTATE = "23505"


def unique_violation_constraint(exc: IntegrityError) -> str | None:
    """The name of the unique constraint/index `exc` violated, or `None` if
    `exc` is not a unique-violation at all.

    Callers match the result against the specific constraint name(s) they
    know how to recover from or translate, and re-raise `exc` for anything
    else - a `None` here (a different SQLSTATE entirely) is exactly that
    "anything else" case, not a reason to look further."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != UNIQUE_VIOLATION_SQLSTATE:
        return None
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None)
