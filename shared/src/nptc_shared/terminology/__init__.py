"""The FR-53 terminology client contract, stub and Ontoserver implementation.

Written once here (ADR-0001, ADR-0003, PRD FR-74) so the backend and the P0
seeding transform never diverge on how a SNOMED CT terminology server is
called or how its responses are parsed. ``TerminologyClient`` (``client.py``)
is the contract; ``StubTerminologyClient`` (``stub.py``) satisfies it with no
network access (NFR-37); ``OntoserverClient`` (``ontoserver.py``) satisfies
it against a real FHIR R4 terminology server. One test suite,
``shared/tests/test_terminology_contract.py``, runs against both.

``sweep.py`` is the one caller of that contract both the backend and the
transform share (FR-74): FR-52's chunked status resolution and FR-84's
single-request hierarchy check, over any of the three.

Landed with backlog issues P0-4 (GitHub issue #26) and P0-5 (#27).
"""

from __future__ import annotations

from nptc_shared.terminology.client import TerminologyClient
from nptc_shared.terminology.config import (
    DEFAULT_BASE_URL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_CONCURRENCY,
    TerminologyConfig,
)
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
from nptc_shared.terminology.snomed import (
    ecl_set_of,
    implicit_value_set_url,
    semantic_tag,
    strip_semantic_tag,
)
from nptc_shared.terminology.stub import StubConcept, StubTerminologyClient
from nptc_shared.terminology.sweep import (
    PROCEDURE_SEMANTIC_TAG,
    ConceptDesignations,
    ConceptTag,
    LabelConfirmation,
    SweepResult,
    TerminologySweep,
)

__all__ = [
    "AU_LANGUAGE_TAG",
    "DEFAULT_BASE_URL",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_CONCURRENCY",
    "FSN_USE_CODE",
    "PROCEDURE_ROOT_CODE",
    "PROCEDURE_SEMANTIC_TAG",
    "SNOMED_CT_AU",
    "SNOMED_CT_INTERNATIONAL",
    "SNOMED_SYSTEM",
    "ConceptDesignations",
    "ConceptProperty",
    "ConceptTag",
    "Designation",
    "Edition",
    "ExpandedConcept",
    "Expansion",
    "LabelConfirmation",
    "LookupResult",
    "OntoserverClient",
    "Operation",
    "OperationOutcomeIssue",
    "StubConcept",
    "StubTerminologyClient",
    "SubsumptionOutcome",
    "SweepResult",
    "TerminologyClient",
    "TerminologyConfig",
    "TerminologyConfigError",
    "TerminologyError",
    "TerminologyOutcomeError",
    "TerminologyProtocolError",
    "TerminologyRateLimitError",
    "TerminologyStatusError",
    "TerminologySweep",
    "TerminologyTimeoutError",
    "TerminologyTransportError",
    "ValidationResult",
    "ecl_set_of",
    "implicit_value_set_url",
    "semantic_tag",
    "strip_semantic_tag",
]
