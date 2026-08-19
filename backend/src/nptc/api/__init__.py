"""Routers, dependencies and OpenAPI wiring.

``app.py`` holds the application factory, ``dependencies.py`` the
request-scoped dependencies joining #43's token verification to #44's
permission checks, ``errors.py`` the auth-error-to-HTTP mapping, and
``routers/`` one module per resource group.
"""
