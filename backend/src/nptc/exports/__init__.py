"""CSV, SPIA spreadsheet and FHIR CodeSystem supplement renderers. Phase P4.

`semantic_tag.py` is the one exception, landed early with issue #48: FR-83's
tag-stripping call site has to exist as soon as a served FSN does
(`nptc.db.models.code_binding`), so the "exactly one call site" guarantee is
structural from the start rather than retrofitted once the renderers land.
"""
