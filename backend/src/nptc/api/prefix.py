"""The one `/api/v1` prefix every router in this app mounts under.

A module of its own rather than a constant on `nptc.api.app`: a router
module needs it too (`catalogue_bindings.py`'s `bind_code` builds a
`Location` header from it) and importing it from `nptc.api.app` would be
circular - that module imports the router to register it.
"""

from __future__ import annotations

API_PREFIX = "/api/v1"
