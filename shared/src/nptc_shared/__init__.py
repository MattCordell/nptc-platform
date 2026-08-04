"""Code shared between the backend API and the P0 seeding transform.

Exists for one reason (see ADR-0001 and PRD FR-74): the transform must not have a
second, divergent implementation of anything the backend also validates. SCTID
parsing and Verhoeff check-digit validation (FR-06), Unicode whitespace
normalisation (PRD Appendix A.1), and the terminology client contract (FR-53) are
written once, here, and imported by both ``backend`` and ``transform``.

This module is scaffolding: it exists so the workspace, the lint/type/test
tooling, and CI (Foundation issues F-1/F-2) have something real to run against.
The actual SCTID/Verhoeff library lands with backlog issue P0-10.
"""

__version__ = "0.0.0"
