"""The P0 seeding transform (PRD section 12).

Converts the published SPIA Requesting workbook into either a validated import
dataset or a detailed, classified defect report (FR-70, FR-71). Depends only on
``nptc_shared`` - never on the backend package - so it can run standalone,
offline against the FR-53 terminology stub (NFR-37), with no application
database required (FR-73).

The CLI entrypoint, the report-only guarantee (FR-70) and the
determinism/idempotency contract (FR-73) landed with P0-1/#23. The workbook
reader, cell-type capture and Appendix A.1-A.3 cell defect detection landed
with P0-2/#24. The three-band defect classification engine landed with
P0-3/#25: every finding is now classified into a band that determines
whether the import blocks. Batch terminology validation and the FR-84
hierarchy check landed with P0-5/#27: ``--check-terminology`` validates every
code binding against both editions through ``nptc_shared.terminology.sweep``,
which is the backend's engine too (FR-74), never a second one. The
SCTID/Verhoeff library (P0-10/#32) and the terminology client interface
(P0-4/#26) also landed, in ``nptc_shared``. Designation reconciliation
(P0-6/#28) and the FR-79 misspelling heuristics (P0-7/#29,
``misspelling.py``, over ``nptc_shared.similarity``) landed next. The
grouped, actionable defect report (P0-8/#30, FR-72 - ``cellref.py``,
``actions.py``, ``report_writer.py``) landed after that. Still open: import
dataset emission - including the auto-correctable band's "fixed
automatically" behaviour - (P0-9/#31).
"""

__version__ = "0.0.0"
