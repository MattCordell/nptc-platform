"""Code shared between the backend API and the P0 seeding transform.

Exists for one reason (see ADR-0001 and PRD FR-74): the transform must not have a
second, divergent implementation of anything the backend also validates. SCTID
parsing and Verhoeff check-digit validation (FR-06) is implemented in
``nptc_shared.sctid``, landed with backlog issue P0-10. The terminology client
contract, stub and Ontoserver implementation (FR-53) are written once, here, in
``nptc_shared.terminology``, and imported by both ``backend`` and ``transform``
(ADR-0003), landed with backlog issue P0-4 - as is the batch validation sweep
that drives it (FR-52, FR-84, FR-99), ``nptc_shared.terminology.sweep``, landed
with backlog issue P0-5. Unicode whitespace normalisation (PRD Appendix A.1) is
implemented in ``nptc_shared.text``, landed with backlog issue P0-2.
"""

__version__ = "0.0.0"
