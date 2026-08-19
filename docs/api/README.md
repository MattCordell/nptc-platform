# API contract

`openapi.json` is the OpenAPI document `nptc.api.app.create_app()` serves, committed so
that a change to the HTTP surface is visible in a diff rather than only at runtime. It is
also what the frontend's generated client will be built from (issue #147).

It is **generated, not hand-edited**. `backend/tests/test_openapi_document.py` fails if it
drifts from the app, and its failure message carries the regeneration command.

The running app serves the same document at `/api/v1/openapi.json`, with Swagger UI at
`/api/v1/docs`.
