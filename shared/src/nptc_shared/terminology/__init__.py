"""The FR-53 terminology client contract, stub and Ontoserver implementation.

Written once here (ADR-0001, ADR-0003, PRD FR-74) so the backend and the P0
seeding transform never diverge on how a SNOMED CT terminology server is
called or how its responses are parsed. ``TerminologyClient`` (``client.py``)
is the contract; ``StubTerminologyClient`` (``stub.py``) satisfies it with no
network access (NFR-37); ``OntoserverClient`` (``ontoserver.py``) satisfies
it against a real FHIR R4 terminology server. One test suite,
``shared/tests/test_terminology_contract.py``, runs against both.

Landed with backlog issue P0-4 (GitHub issue #26).
"""

from __future__ import annotations

from nptc_shared.terminology.client import TerminologyClient
from nptc_shared.terminology.config import DEFAULT_BASE_URL, TerminologyConfig
from nptc_shared.terminology.errors import (
    OperationOutcomeIssue,
    TerminologyConfigError,
    TerminologyError,
    TerminologyOutcomeError,
    TerminologyProtocolError,
    TerminologyRateLimitError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)
from nptc_shared.terminology.models import (
    AU_LANGUAGE_TAG,
    FSN_USE_CODE,
    PROCEDURE_ROOT_CODE,
    SNOMED_CT_AU,
    SNOMED_CT_INTERNATIONAL,
    SNOMED_SYSTEM,
    ConceptProperty,
    Designation,
    Edition,
    ExpandedConcept,
    Expansion,
    LookupResult,
    Operation,
    SubsumptionOutcome,
    ValidationResult,
)
from nptc_shared.terminology.ontoserver import OntoserverClient
from nptc_shared.terminology.snomed import ecl_set_of, implicit_value_set_url
from nptc_shared.terminology.stub import StubConcept, StubTerminologyClient

__all__ = [
    "AU_LANGUAGE_TAG",
    "DEFAULT_BASE_URL",
    "FSN_USE_CODE",
    "PROCEDURE_ROOT_CODE",
    "SNOMED_CT_AU",
    "SNOMED_CT_INTERNATIONAL",
    "SNOMED_SYSTEM",
    "ConceptProperty",
    "Designation",
    "Edition",
    "ExpandedConcept",
    "Expansion",
    "LookupResult",
    "OntoserverClient",
    "Operation",
    "OperationOutcomeIssue",
    "StubConcept",
    "StubTerminologyClient",
    "SubsumptionOutcome",
    "TerminologyClient",
    "TerminologyConfig",
    "TerminologyConfigError",
    "TerminologyError",
    "TerminologyOutcomeError",
    "TerminologyProtocolError",
    "TerminologyRateLimitError",
    "TerminologyStatusError",
    "TerminologyTimeoutError",
    "TerminologyTransportError",
    "ValidationResult",
    "ecl_set_of",
    "implicit_value_set_url",
]
