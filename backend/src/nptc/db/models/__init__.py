"""Import-aggregator so ``Base.metadata`` is complete for Alembic autogenerate.

Every model module must be imported here for its side effect of registering
its table with ``nptc.db.base.Base.metadata`` - a model that exists but
isn't imported through this package is invisible to autogenerate and to
``compare_metadata()``.
"""

from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.designation import Designation, DesignationStatus, DesignationUse
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity
from nptc.db.models.user_role import UserRole

__all__ = [
    "AuditEvent",
    "CatalogueEntry",
    "CatalogueEntryStatus",
    "Designation",
    "DesignationStatus",
    "DesignationUse",
    "User",
    "UserIdentity",
    "UserRole",
    "UserStatus",
]
