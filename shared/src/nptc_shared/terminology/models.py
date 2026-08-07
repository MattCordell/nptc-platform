"""Value types for the FR-53 terminology client contract.

Every field here that carries a SNOMED CT identifier is a ``str``. Wire-level
types keep the code exactly as the server returned it - a malformed code
arriving from the server must surface as a finding at the caller (FR-45), not
as a parse-time crash in this client. Where the platform is the one
*asserting* an identifier - most notably ``nptc_shared.terminology.snomed``
building an ECL query out of a chunk of catalogue codes - it validates with
``nptc_shared.sctid.has_valid_format`` rather than repeating that check here.

Designations are carried verbatim (FR-82): no stripping, no normalisation, no
semantic-tag removal. FR-83 puts the one legitimate strip in the export
renderer, which is not this package and never will be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

SNOMED_SYSTEM = "http://snomed.info/sct"

#: The SNOMED CT concept ($lookup ``use``) marking a designation as the Fully
#: Specified Name, so ``Designation.is_fully_specified_name`` can be derived
#: from the server's own coding rather than a display-string heuristic.
FSN_USE_CODE = "900000000000003001"

#: RCPA's AU language reference set (PRD FR-82). A ``$lookup``/``$expand``
#: designation carrying this as its ``language`` is the AU preferred term.
AU_LANGUAGE_TAG = "en-x-sctlang-32570271-00003610-6"

#: |Procedure (procedure)| - the root every code binding must be subsumed by
#: (FR-84).
PROCEDURE_ROOT_CODE = "71388002"


class Operation(StrEnum):
    """A FHIR terminology operation. The value doubles as its request path,
    so an error message naming the operation and the path it took never
    disagree."""

    EXPAND = "ValueSet/$expand"
    LOOKUP = "CodeSystem/$lookup"
    SUBSUMES = "CodeSystem/$subsumes"
    CODE_SYSTEM_VALIDATE_CODE = "CodeSystem/$validate-code"
    VALUE_SET_VALIDATE_CODE = "ValueSet/$validate-code"


class SubsumptionOutcome(StrEnum):
    """The four outcomes ``CodeSystem/$subsumes`` can report."""

    EQUIVALENT = "equivalent"
    SUBSUMES = "subsumes"
    SUBSUMED_BY = "subsumed-by"
    NOT_SUBSUMED = "not-subsumed"


@dataclass(frozen=True, slots=True)
class Edition:
    """A SNOMED CT edition, optionally pinned to a release (FR-48, FR-49).

    ``version`` is the release's effective time as a ``str`` ("20260531"),
    never an ``int`` - the same FR-06 discipline applied to the one other
    all-digits token in this domain, and the value is only ever concatenated
    into a URI. ``None`` means "no version parameter": FR-49's normal
    operation, where the server resolves the latest release and reports which
    one it used via ``system_version_uri`` on the response - that reported
    URI is what FR-48 requires be recorded, not this field.

    ``display_language`` is which edition's preferred term a ``display``
    value on a response actually is (FR-82) - an edition-level fact, since a
    language reference set belongs to one edition and not another.
    ``AU_LANGUAGE_TAG`` does not exist in the International edition, so it is
    set only on ``SNOMED_CT_AU``: sending it on both would leave a caller
    unable to tell "the server does not recognise this language reference set
    and silently fell back to some other preferred term" from "this really is
    the AU preferred term", which FR-97's designation reconciliation and its
    AU-preferred-term-differs report both depend on getting right.
    """

    module_id: str
    label: str
    system: str = SNOMED_SYSTEM
    version: str | None = None
    display_language: str | None = None

    @property
    def system_version_uri(self) -> str:
        """``http://snomed.info/sct/<module>[/version/<effective time>]``."""
        base = f"{self.system}/{self.module_id}"
        return base if self.version is None else f"{base}/version/{self.version}"

    def pinned_to(self, version: str) -> Edition:
        """This edition pinned to ``version`` (FR-49's reproduce-a-historical-run path)."""
        return Edition(
            module_id=self.module_id,
            label=self.label,
            system=self.system,
            version=version,
            display_language=self.display_language,
        )


#: The two editions FR-47's dual-edition validation diffs against.
SNOMED_CT_AU = Edition(module_id="32506021000036107", label="au", display_language=AU_LANGUAGE_TAG)
SNOMED_CT_INTERNATIONAL = Edition(module_id="900000000000207008", label="int")


@dataclass(frozen=True, slots=True)
class Designation:
    """One designation exactly as served (FR-82) - never trimmed or stripped."""

    value: str
    language: str | None = None
    use_system: str | None = None
    use_code: str | None = None
    use_display: str | None = None

    @property
    def is_fully_specified_name(self) -> bool:
        return self.use_system == SNOMED_SYSTEM and self.use_code == FSN_USE_CODE


@dataclass(frozen=True, slots=True)
class ConceptProperty:
    """One ``$lookup`` property, its value carried in lexical form.

    ``value`` is always a ``str`` - ``"true"``/``"false"`` for a boolean, the
    digits for a code. Keeping the lexical form is what stops a historical
    association's target (``SAME AS`` -> an SCTID, FR-46) from ever being
    routed through ``int``.
    """

    code: str
    value: str
    value_type: str


@dataclass(frozen=True, slots=True)
class ExpandedConcept:
    """One member of a ``ValueSet/$expand`` result."""

    code: str
    system: str
    display: str | None = None
    version: str | None = None
    designations: tuple[Designation, ...] = ()


@dataclass(frozen=True, slots=True)
class Expansion:
    """The result of one ``ValueSet/$expand``.

    An *empty* expansion means the server answered and nothing matched a
    request that was itself well-formed - for example, FR-84's compliance
    check when every code is in scope. A failure never produces one: see
    ``errors.py``. That distinction is FR-54's whole hazard in miniature - an
    outage that reads as a clean result is worse than an outage that reads as
    an outage.
    """

    concepts: tuple[ExpandedConcept, ...] = ()
    total: int | None = None
    offset: int | None = None
    resolved_versions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(concept.code for concept in self.concepts)

    def contains(self, code: str) -> bool:
        return code in self.codes

    @property
    def is_complete(self) -> bool:
        """False if ``concepts`` is a page, not the whole result.

        A single ``expand`` call is not guaranteed to return everything a
        chunk's ``total`` promises - a server-side page-size ceiling can cap
        ``contains`` below ``count``, and a truncated page looks identical to
        a genuinely short result unless a caller checks this. Paging with
        ``offset`` to fetch the rest is the caller's responsibility (issue
        #27's chunked sweep); this client makes exactly one request per
        ``expand`` call and never pages on its own.
        """
        return self.total is None or len(self.concepts) >= self.total


@dataclass(frozen=True, slots=True)
class LookupResult:
    """The result of one ``CodeSystem/$lookup``."""

    code: str
    system: str
    name: str | None = None
    display: str | None = None
    resolved_version: str | None = None
    designations: tuple[Designation, ...] = ()
    properties: tuple[ConceptProperty, ...] = ()

    @property
    def fully_specified_name(self) -> str | None:
        """The served FSN, semantic tag intact (FR-82).

        ``None`` if the server returned no FSN designation - never a guess,
        and never a fallback to ``display``, which is a preferred term and a
        different thing.
        """
        for designation in self.designations:
            if designation.is_fully_specified_name:
                return designation.value
        return None

    def property_values(self, code: str) -> tuple[str, ...]:
        """Every value recorded against property ``code`` (a concept can carry
        more than one, e.g. several historical association targets)."""
        return tuple(prop.value for prop in self.properties if prop.code == code)

    @property
    def inactive(self) -> bool | None:
        """Tri-state on purpose: ``None`` means the server did not report the
        ``inactive`` property, which is not the same as "active"."""
        values = self.property_values("inactive")
        if not values:
            return None
        return values[0] == "true"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The result of one ``$validate-code``."""

    code: str
    result: bool
    display: str | None = None
    message: str | None = None
    resolved_version: str | None = None
