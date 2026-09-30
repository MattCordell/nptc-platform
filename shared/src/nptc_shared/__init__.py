"""Code shared between the backend API and the P0 seeding transform.

Exists for one reason (ADR-0001, PRD FR-74): the transform must not have a
second, divergent implementation of anything the backend also validates.

- ``nptc_shared.sctid``: SCTID parsing and Verhoeff check-digit validation (FR-06).
- ``nptc_shared.terminology``: the terminology client contract, stub and
  Ontoserver implementation (FR-53, ADR-0003), and the batch validation sweep
  that drives it (FR-52, FR-84, FR-99).
- ``nptc_shared.text``: Unicode whitespace normalisation (PRD Appendix A.1).
"""

__version__ = "0.0.0"
