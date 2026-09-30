"""The backend's consumers of the FR-53 terminology client contract.

The contract, stub and Ontoserver client live in ``nptc_shared.terminology``
(ADR-0003), shared with the P0 transform (FR-74). This package holds the
backend's own use of that client: FR-26's live concept lookup
(``concepts.resolve_concept``, ``errors``) and, still to come, the FR-45/FR-50
validation sweep.
"""
