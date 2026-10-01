"""The one `/api/v1` prefix every router mounts under.

A module of its own because routers need it too (`bind_code` in
`catalogue_bindings.py` builds a `Location` header from it), and importing it
from `nptc.api.app` would be circular: that module imports the routers.
"""

from __future__ import annotations

API_PREFIX = "/api/v1"
