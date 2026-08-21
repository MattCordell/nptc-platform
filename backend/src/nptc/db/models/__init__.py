"""Import-aggregator so ``Base.metadata`` is complete for Alembic autogenerate.

Every model module must be imported here for its side effect of registering
its table with ``nptc.db.base.Base.metadata`` - a model that exists but
isn't imported through this package is invisible to autogenerate and to
``compare_metadata()``.
"""

from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding, CodeBindingEditionHint, CodeBindingStatus
from nptc.db.models.designation import Designation, DesignationStatus, DesignationUse
from nptc.db.models.designation_collision_acknowledgement import (
    DesignationCollisionAcknowledgement,
)
from nptc.db.models.local_code import LocalCode, LocalCodeStatus
from nptc.db.models.local_code_snomed_map import LocalCodeSnomedMap, SnomedMapMatchStrength
from nptc.db.models.local_code_system import LocalCodeSystem, LocalCodeSystemStatus
from nptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
    PropertyStatus,
)
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity
from nptc.db.models.user_role import UserRole

__all__ = [
    "AuditEvent",
    "BindingStrength",
    "BindingTarget",
    "CatalogueEntry",
    "CatalogueEntryStatus",
    "CodeBinding",
    "CodeBindingEditionHint",
    "CodeBindingStatus",
    "Designation",
    "DesignationCollisionAcknowledgement",
    "DesignationStatus",
    "DesignationUse",
    "LocalCode",
    "LocalCodeSnomedMap",
    "LocalCodeStatus",
    "LocalCodeSystem",
    "LocalCodeSystemStatus",
    "PropertyCardinality",
    "PropertyDefinition",
    "PropertyOrigin",
    "PropertyScope",
    "PropertyStatus",
    "PropertyValue",
    "SnomedMapMatchStrength",
    "User",
    "UserIdentity",
    "UserRole",
    "UserStatus",
]
