"""The backend's consumers of the FR-53 terminology client contract.

The contract, the stub, and the Ontoserver implementation all live in
``nptc_shared.terminology`` (ADR-0003) so the backend and the P0 transform can
never diverge (FR-74). What lives here is the backend-specific use of that
client: the FR-45/FR-50 validation sweep and FR-26's live check during form
completion.
"""
