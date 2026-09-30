"""Reads a Postgres constraint name back out of a SQLAlchemy `IntegrityError`.

`nptc.auth.identity._create_user`'s username-collision retry and
`nptc.catalogue.bindings.create_binding`'s lost-race translation both need the
`orig`/`diag`/`constraint_name` unwrap and the `"23505"` literal, so it lives here once.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

#: Postgres's SQLSTATE for `unique_violation`, the only `IntegrityError` class a constraint name can
#: disambiguate: a `CHECK` or `NOT NULL` violation names the column, not a row to recover from.
UNIQUE_VIOLATION_SQLSTATE = "23505"


def unique_violation_constraint(exc: IntegrityError) -> str | None:
    """The name of the unique constraint or index `exc` violated, or `None` if `exc` is not a unique
    violation. Callers match the result against the constraint names they can recover from or
    translate, and re-raise for anything else, including `None`.
    """
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != UNIQUE_VIOLATION_SQLSTATE:
        return None
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None)
