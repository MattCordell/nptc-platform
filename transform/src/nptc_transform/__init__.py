"""The P0 seeding transform (PRD section 12).

Converts the published SPIA Requesting workbook into either a validated import
dataset or a detailed, classified defect report (FR-70, FR-71). Depends only on
``nptc_shared`` - never on the backend package - so it can run standalone,
offline against the FR-53 terminology stub (NFR-37), with no application
database required (FR-73).

This module and ``cli.py`` are scaffolding for Foundation issues F-1/F-2: enough
of a real CLI to prove the workspace and tooling work end to end. The actual
transform (workbook reader, classification engine, reports) lands with backlog
issues P0-1 through P0-10.
"""

__version__ = "0.0.0"
