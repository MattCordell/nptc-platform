"""The P0 seeding transform (PRD section 12).

Converts the published SPIA Requesting workbook into either a validated import
dataset or a detailed, classified defect report (FR-70, FR-71). Depends only on
``nptc_shared``, never on the backend package, so it can run standalone and
offline against the FR-53 terminology stub (NFR-37), with no application
database (FR-73).

- ``cli.py``: the entrypoint, the report-only guarantee (FR-70) and the
  determinism and idempotency contract (FR-73).
- ``workbook.py`` and ``cell_defects.py``: the reader, cell-type capture and
  Appendix A.1-A.3 defect detection.
- ``bands.py``: the defect-band classification that decides whether an import
  blocks (FR-71).
- ``terminology_check.py``: ``--check-terminology`` validates every code binding
  against both editions through ``nptc_shared.terminology.sweep``, the backend's
  engine too (FR-74).
- ``designation_check.py``, ``misspelling.py`` and ``semantic_drift.py``:
  designation reconciliation (FR-97), misspelling heuristics (FR-79) and
  specimen and timing drift (FR-75).
- ``report_writer.py``, ``actions.py`` and ``cellref.py``: the grouped,
  actionable defect report (FR-72).
- ``dataset.py`` and ``corrections.py``: import dataset emission, including the
  auto-correctable band's repairs (FR-76).
"""

__version__ = "0.0.0"
