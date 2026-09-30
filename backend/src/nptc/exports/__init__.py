"""CSV, SPIA spreadsheet and FHIR CodeSystem supplement renderers. Phase P4.

`semantic_tag.py` is the one exception, present early: FR-83's tag-stripping
call site must exist as soon as a served FSN does (`nptc.db.models.code_binding`),
so "exactly one call site" holds from the start.
"""
