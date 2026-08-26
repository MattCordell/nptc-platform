# API contract

`openapi.json` is the OpenAPI document `nptc.api.app.create_app()` serves, committed so
that a change to the HTTP surface is visible in a diff rather than only at runtime. It is
also what the frontend's generated client will be built from (issue #147).

It is **generated, not hand-edited**. Regenerate it with:

```powershell
uv run python scripts/generate_openapi.py
```

`scripts/generate_openapi.py --check` is the CI gate
([`.github/workflows/openapi.yml`](../../.github/workflows/openapi.yml), issue #143) and
a pre-commit hook - it fails if the committed file is not exactly what
`nptc.api.app.create_app()` would serve right now. `backend/tests/test_openapi_document.py`
carries the same drift check plus three more: the document validates against the OpenAPI
3.1 meta-schema, every SNOMED CT code field is declared `type: string` at the schema
level (FR-06), and the running app's served document is byte-identical to the committed
one.

The running app serves the same document at `/api/v1/openapi.json`, with Swagger UI at
`/api/v1/docs`.
