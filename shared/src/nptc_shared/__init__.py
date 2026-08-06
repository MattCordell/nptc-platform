"""Code shared between the backend API and the P0 seeding transform.

Exists for one reason (see ADR-0001 and PRD FR-74): the transform must not have a
second, divergent implementation of anything the backend also validates. SCTID
parsing and Verhoeff check-digit validation (FR-06) is implemented in
``nptc_shared.sctid``, landed with backlog issue P0-10. The terminology client
contract (FR-53) is written once, here, and imported by both ``backend`` and
``transform``. Unicode whitespace normalisation (PRD Appendix A.1) is
implemented in ``nptc_shared.text``, landed with backlog issue P0-2.
"""

__version__ = "0.0.0"
