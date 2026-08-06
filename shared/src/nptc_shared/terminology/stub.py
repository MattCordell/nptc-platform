"""The in-memory ``TerminologyClient`` stub (FR-53).

Never opens a socket (NFR-37). It answers a request in this order: an exact
response a test seeded (``seed_expansion``/``seed_lookup``/``seed_subsumes``/
``seed_validate_code``); then, for ``expand`` only, a small recognised subset
of ECL evaluated against a seeded concept table (``add_concept``); then it
**raises**. It never invents an empty or default-valued result for something
it was not taught - an unseeded request silently reading as "nothing matched"
is exactly the FR-54 hazard this package exists to avoid, reproduced inside
the test suite, where it would be even harder to notice.

The ECL subset (``_evaluate_ecl``) is not an ECL engine: it recognises a
disjunction of literal codes (what ``snomed.ecl_set_of`` emits), ``<<X``/``<X``
against a seeded concept's ``parents``, and one top-level ``A MINUS B`` over
those. That is exactly enough to make FR-84's
``(codes) MINUS <<71388002`` idiom usable in a test without hand-seeding a
distinct expansion for every chunk.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nptc_shared.sctid import has_valid_format
from nptc_shared.terminology.errors import TerminologyError
from nptc_shared.terminology.models import (
    AU_LANGUAGE_TAG,
    FSN_USE_CODE,
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

if TYPE_CHECKING:
    from nptc_shared.terminology.client import TerminologyClient

__all__ = [
    "StubConcept",
    "StubEclNotSupportedError",
    "StubNotSeededError",
    "StubRequest",
    "StubTerminologyClient",
]


class StubNotSeededError(TerminologyError):
    """Raised when the stub was asked for something no test seeded it with.

    Never answered with an empty or default-valued result instead - see the
    module docstring.
    """


class StubEclNotSupportedError(StubNotSeededError):
    """The stub's ECL subset does not cover this expression. It is not an
    ECL engine - see the module docstring for exactly what it recognises."""


@dataclass(frozen=True, slots=True)
class StubConcept:
    """One concept, described the way a test wants to, not as FHIR serialises it."""

    code: str
    fsn: str
    preferred_terms: Mapping[str, str] = field(default_factory=dict)
    synonyms: tuple[str, ...] = ()
    active: bool = True
    module_id: str = "32506021000036107"
    parents: tuple[str, ...] = ()
    editions: tuple[str, ...] = ("au", "int")
    properties: tuple[ConceptProperty, ...] = ()


@dataclass(frozen=True, slots=True)
class StubRequest:
    """One call made against the stub, for #27-style "one request, not N"
    assertions to work identically against the stub and the real client."""

    operation: Operation
    detail: str


def _edition_key(edition: Edition | None) -> str | None:
    return edition.label if edition is not None else None


class StubTerminologyClient:
    """In-memory ``TerminologyClient`` (FR-53). See the module docstring."""

    def __init__(
        self,
        *,
        concepts: Iterable[StubConcept] = (),
        resolved_version: Mapping[str, str] | None = None,
    ) -> None:
        self._concepts: dict[str, StubConcept] = {concept.code: concept for concept in concepts}
        self._resolved_version: dict[str, str] = dict(resolved_version or {})
        self._expansions: dict[tuple[str, str | None], Expansion] = {}
        self._lookups: dict[tuple[str, str | None], LookupResult] = {}
        self._subsumes: dict[tuple[str, str, str | None], SubsumptionOutcome] = {}
        self._validate_code: dict[
            tuple[str, str | None, str | None, str | None], ValidationResult
        ] = {}
        self._errors: dict[tuple[Operation, str | None], TerminologyError] = {}
        self._requests: list[StubRequest] = []

    # -- seeding -----------------------------------------------------------

    def add_concept(self, concept: StubConcept) -> None:
        self._concepts[concept.code] = concept

    def seed_expansion(
        self, ecl: str, expansion: Expansion, *, edition: Edition | None = None
    ) -> None:
        self._expansions[(ecl, _edition_key(edition))] = expansion

    def seed_lookup(
        self, code: str, result: LookupResult, *, edition: Edition | None = None
    ) -> None:
        self._lookups[(code, _edition_key(edition))] = result

    def seed_subsumes(
        self,
        code_a: str,
        code_b: str,
        outcome: SubsumptionOutcome,
        *,
        edition: Edition | None = None,
    ) -> None:
        self._subsumes[(code_a, code_b, _edition_key(edition))] = outcome

    def seed_validate_code(
        self,
        code: str,
        result: ValidationResult,
        *,
        display: str | None = None,
        value_set_url: str | None = None,
        edition: Edition | None = None,
    ) -> None:
        self._validate_code[(code, display, value_set_url, _edition_key(edition))] = result

    def seed_error(
        self, operation: Operation, error: TerminologyError, *, key: str | None = None
    ) -> None:
        self._errors[(operation, key)] = error

    # -- introspection -------------------------------------------------------

    @property
    def requests(self) -> tuple[StubRequest, ...]:
        return tuple(self._requests)

    def reset(self) -> None:
        """Clears the request log only - seeded data is untouched, so a test
        can reset the call count mid-way through without re-seeding."""
        self._requests = []

    # -- TerminologyClient ------------------------------------------------

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        self._requests.append(StubRequest(Operation.EXPAND, ecl))
        self._raise_if_seeded_error(Operation.EXPAND, ecl)

        seeded = self._expansions.get((ecl, edition.label), self._expansions.get((ecl, None)))
        if seeded is not None:
            return seeded

        codes = sorted(self._evaluate_ecl(ecl, edition))
        if active_only:
            # A code with no concept info at all is kept - the stub has no
            # basis to call it inactive, the same "unknown means visible"
            # rule _visible_in_edition applies to edition membership.
            codes = [
                c for c in codes if (concept := self._concepts.get(c)) is None or concept.active
            ]
        total = len(codes)
        page = codes[offset : offset + count] if count is not None else codes[offset:]
        concepts = tuple(
            _expanded_concept_from_code(
                code, self._concepts.get(code), include_designations=include_designations
            )
            for code in page
        )
        return Expansion(
            concepts=concepts,
            total=total,
            offset=offset,
            resolved_versions=self._resolved_versions_for(edition),
        )

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> LookupResult:
        self._requests.append(StubRequest(Operation.LOOKUP, code))
        self._raise_if_seeded_error(Operation.LOOKUP, code)

        seeded = self._lookups.get((code, edition.label), self._lookups.get((code, None)))
        if seeded is not None:
            return seeded

        concept = self._concepts.get(code)
        if concept is not None and edition.label in concept.editions:
            return _lookup_result_from_concept(
                concept,
                display_language=display_language,
                resolved_version=self._resolved_versions_for(edition),
            )
        raise StubNotSeededError(
            f"stub was not seeded for CodeSystem/$lookup(code={code!r}, edition={edition.label!r})",
            operation=Operation.LOOKUP,
        )

    def subsumes(self, code_a: str, code_b: str, *, edition: Edition) -> SubsumptionOutcome:
        detail = f"{code_a},{code_b}"
        self._requests.append(StubRequest(Operation.SUBSUMES, detail))
        self._raise_if_seeded_error(Operation.SUBSUMES, detail)

        seeded = self._subsumes.get(
            (code_a, code_b, edition.label), self._subsumes.get((code_a, code_b, None))
        )
        if seeded is not None:
            return seeded

        if code_a == code_b:
            return SubsumptionOutcome.EQUIVALENT
        ancestors_of_a = self._ancestors(code_a)
        ancestors_of_b = self._ancestors(code_b)
        if code_b in ancestors_of_a:
            return SubsumptionOutcome.SUBSUMED_BY
        if code_a in ancestors_of_b:
            return SubsumptionOutcome.SUBSUMES
        if code_a in self._concepts and code_b in self._concepts:
            return SubsumptionOutcome.NOT_SUBSUMED
        raise StubNotSeededError(
            f"stub was not seeded for CodeSystem/$subsumes(codeA={code_a!r}, codeB={code_b!r})",
            operation=Operation.SUBSUMES,
        )

    def validate_code(
        self,
        code: str,
        *,
        edition: Edition,
        display: str | None = None,
        value_set_url: str | None = None,
    ) -> ValidationResult:
        operation = (
            Operation.VALUE_SET_VALIDATE_CODE
            if value_set_url is not None
            else Operation.CODE_SYSTEM_VALIDATE_CODE
        )
        self._requests.append(StubRequest(operation, code))
        self._raise_if_seeded_error(operation, code)

        for key in (
            (code, display, value_set_url, edition.label),
            (code, None, value_set_url, edition.label),
            (code, display, value_set_url, None),
            (code, None, value_set_url, None),
        ):
            seeded = self._validate_code.get(key)
            if seeded is not None:
                return seeded

        if value_set_url is not None:
            # The stub has no ECL engine to evaluate value-set membership -
            # unlike the code-system check below, there is no safe derived
            # answer here. Silently falling through to the code-system logic
            # would make an FR-10 binding check always pass against the stub.
            raise StubNotSeededError(
                f"stub was not seeded for ValueSet/$validate-code(code={code!r}, "
                f"value_set_url={value_set_url!r}, edition={edition.label!r}) - seed the "
                "response explicitly, the stub cannot evaluate value-set membership",
                operation=operation,
            )

        concept = self._concepts.get(code)
        if concept is None or edition.label not in concept.editions:
            raise StubNotSeededError(
                f"stub was not seeded for CodeSystem/$validate-code(code={code!r}, "
                f"edition={edition.label!r})",
                operation=operation,
            )
        known_designations = {concept.fsn, *concept.preferred_terms.values(), *concept.synonyms}
        result = display is None or display in known_designations
        message = (
            None
            if result
            else f"The code {code!r} was found, but the display {display!r} did not match any designation"
        )
        return ValidationResult(
            code=code,
            result=result,
            display=display,
            message=message,
            resolved_version=self._resolved_version.get(edition.label),
        )

    # -- helpers -----------------------------------------------------------

    def _raise_if_seeded_error(self, operation: Operation, key: str) -> None:
        error = self._errors.get((operation, key), self._errors.get((operation, None)))
        if error is not None:
            raise error

    def _resolved_versions_for(self, edition: Edition) -> tuple[str, ...]:
        version = self._resolved_version.get(edition.label)
        if version is None:
            return ()
        return (version,)

    def _ancestors(self, code: str) -> frozenset[str]:
        seen: set[str] = set()
        frontier = list(self._concepts[code].parents) if code in self._concepts else []
        while frontier:
            parent = frontier.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if parent in self._concepts:
                frontier.extend(self._concepts[parent].parents)
        return frozenset(seen)

    def _visible_in_edition(self, code: str, edition: Edition) -> bool:
        """True unless the stub was explicitly told ``code`` is excluded from
        ``edition`` - a code with no ``StubConcept`` entry at all (e.g. the
        FR-84 procedure root, typically referenced but never itself seeded)
        is assumed visible, since the stub has no basis to exclude it."""
        concept = self._concepts.get(code)
        if concept is None:
            return True
        return edition.label in concept.editions

    def _descendants_or_self(
        self, root: str, *, edition: Edition, include_self: bool
    ) -> frozenset[str]:
        descendants = {
            code
            for code, concept in self._concepts.items()
            if edition.label in concept.editions and root in self._ancestors(code)
        }
        if include_self and self._visible_in_edition(root, edition):
            descendants = descendants | {root}
        return frozenset(descendants)

    def _evaluate_ecl(self, ecl: str, edition: Edition) -> frozenset[str]:
        text = ecl.strip()
        if " MINUS " in text:
            left, right = text.split(" MINUS ", 1)
            return self._evaluate_term(left.strip(), edition) - self._evaluate_term(
                right.strip(), edition
            )
        return self._evaluate_term(text, edition)

    def _evaluate_term(self, term: str, edition: Edition) -> frozenset[str]:
        term = term.strip()
        if term.startswith("(") and term.endswith(")"):
            return self._evaluate_term(term[1:-1], edition)
        if term.startswith("<<"):
            return self._descendants_root(term[2:], edition=edition, include_self=True)
        if term.startswith("<"):
            return self._descendants_root(term[1:], edition=edition, include_self=False)
        pieces = [piece.strip() for piece in term.split(" OR ") if piece.strip()]
        for piece in pieces:
            if not has_valid_format(piece):
                raise StubEclNotSupportedError(
                    f"the stub's ECL subset does not recognise {term!r} - it is not an ECL engine",
                    operation=Operation.EXPAND,
                )
        return frozenset(piece for piece in pieces if self._visible_in_edition(piece, edition))

    def _descendants_root(
        self, raw_root: str, *, edition: Edition, include_self: bool
    ) -> frozenset[str]:
        root = raw_root.strip()
        if not has_valid_format(root):
            # A well-formed "<<X"/"<X" has exactly one code after the
            # operator - anything else (a second operator, an OR, stray
            # punctuation) means this ECL is more than the stub's subset.
            raise StubEclNotSupportedError(
                f"the stub's ECL subset does not recognise {raw_root!r} as a single code "
                "after '<'/'<<' - it is not an ECL engine",
                operation=Operation.EXPAND,
            )
        return self._descendants_or_self(root, edition=edition, include_self=include_self)


def _expanded_concept_from_code(
    code: str, concept: StubConcept | None, *, include_designations: bool
) -> ExpandedConcept:
    if concept is None:
        return ExpandedConcept(code=code, system=SNOMED_SYSTEM)
    designations: tuple[Designation, ...] = ()
    if include_designations:
        designations = (
            Designation(
                value=concept.fsn, language="en", use_system=SNOMED_SYSTEM, use_code=FSN_USE_CODE
            ),
            *(
                Designation(value=term, language=language)
                for language, term in concept.preferred_terms.items()
            ),
        )
    display = next(iter(concept.preferred_terms.values()), concept.fsn)
    return ExpandedConcept(
        code=code, system=SNOMED_SYSTEM, display=display, designations=designations
    )


if TYPE_CHECKING:

    def _conforms(client: StubTerminologyClient) -> TerminologyClient:
        """Compile-time proof of Protocol conformance - see the identical
        note in ``ontoserver.py``."""
        return client


def _lookup_result_from_concept(
    concept: StubConcept, *, display_language: str | None, resolved_version: tuple[str, ...]
) -> LookupResult:
    designations = [
        Designation(
            value=concept.fsn, language="en", use_system=SNOMED_SYSTEM, use_code=FSN_USE_CODE
        ),
        *(
            Designation(value=term, language=language)
            for language, term in concept.preferred_terms.items()
        ),
        *(Designation(value=synonym, language="en") for synonym in concept.synonyms),
    ]
    properties = (
        ConceptProperty(
            code="inactive", value="false" if concept.active else "true", value_type="boolean"
        ),
        ConceptProperty(code="moduleId", value=concept.module_id, value_type="code"),
        *concept.properties,
    )
    display = concept.preferred_terms.get(display_language or AU_LANGUAGE_TAG)
    if display is None:
        display = next(iter(concept.preferred_terms.values()), concept.fsn)
    return LookupResult(
        code=concept.code,
        system=SNOMED_SYSTEM,
        name="SNOMED CT",
        display=display,
        resolved_version=resolved_version[0] if resolved_version else None,
        designations=tuple(designations),
        properties=properties,
    )
