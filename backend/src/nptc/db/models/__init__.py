"""Import-aggregator so ``Base.metadata`` is complete for Alembic autogenerate.

Every model module must be imported here for its side effect of registering
its table with ``nptc.db.base.Base.metadata`` - a model that exists but
isn't imported through this package is invisible to autogenerate and to
``compare_metadata()``.
"""

from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity

__all__ = ["AuditEvent", "User", "UserIdentity", "UserStatus"]
