"""The FR-53 terminology client contract.

Two implementations satisfy this Protocol, and one shared test suite runs
over both (``shared/tests/test_terminology_contract.py``): ``OntoserverClient``
(``nptc_shared.terminology.ontoserver``, httpx-backed) and
``StubTerminologyClient`` (``nptc_shared.terminology.stub``, in-memory, never
opens a socket - NFR-37).

Nothing here is Ontoserver-specific. Every operation is FHIR R4 as specified,
so the endpoint can be repointed at NCTS production or a self-hosted instance
by configuration alone (FR-53; PRD Section 15.2's accepted risk around
``tx.ontoserver.csiro.au``).

Failure is always an exception (see ``errors.py``), never a degraded-but-
plausible value. That is this package's whole contribution to FR-54; the
degradation policy belongs to the caller.
"""

from __future__ import annotations

from typing import Protocol

from nptc_shared.terminology.models import (
    Edition,
    Expansion,
    LookupResult,
    SubsumptionOutcome,
    ValidationResult,
)


class TerminologyClient(Protocol):
    """FHIR terminology operations, behind an interface (FR-53)."""

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
        filter: str | None = None,
    ) -> Expansion:
        """Expands the SNOMED implicit value set for ``ecl`` against ``edition``.

        The batch primitive FR-52 mandates: one request resolves a whole chunk
        of codes. It is also FR-84's hierarchy check, via
        ``nptc_shared.terminology.snomed.ecl_set_of`` and a ``MINUS <<71388002``
        clause.

        ``filter`` is FHIR ``$expand``'s server-side text filter, a
        case-insensitive partial match against each candidate's display. It
        narrows a value-set-bound concept picker to what the caller typed
        (FR-10) without pulling the whole expansion client-side.

        Exactly one request per call: this method never pages. Check
        ``result.is_complete`` and re-call with an advanced ``offset`` if it is
        ``False``, because a server-side page-size ceiling can cap ``concepts``
        below ``total`` and a truncated page looks like a short result.
        """
        ...

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> LookupResult:
        """``CodeSystem/$lookup`` for one code.

        FR-52's second pass only: the delta a bulk ``expand`` could not settle.
        Calling this once per catalogue code is what FR-52 forbids. Raises when
        the code does not exist in ``edition`` rather than returning a
        default-valued result (FR-54).

        Pass ``display_language=AU_LANGUAGE_TAG`` to get ``display`` as the AU
        preferred term (FR-82). The FSN is ``result.fully_specified_name``
        whatever ``display_language`` is.

        ``properties`` names the concept properties to return in addition to the
        designations: an implementation must still return the designations when
        ``properties`` is non-empty and does not list ``designation``.
        """
        ...

    def subsumes(self, code_a: str, code_b: str, *, edition: Edition) -> SubsumptionOutcome:
        """``CodeSystem/$subsumes`` for a single, ad-hoc pair.

        For interactive checks (FR-26) and one-off verification only. FR-84's
        catalogue-wide hierarchy check must not use it: that is one batch
        ``expand`` of ``(codes) MINUS <<71388002``. A loop of these over the
        catalogue is the anti-pattern FR-52 and FR-84 name.
        """
        ...

    def validate_code(
        self,
        code: str,
        *,
        edition: Edition,
        display: str | None = None,
        value_set_url: str | None = None,
    ) -> ValidationResult:
        """``$validate-code``, against the code system or a value set.

        With ``value_set_url`` unset this is ``CodeSystem/$validate-code``;
        with ``display`` set it is FR-97's designation reconciliation probe. A
        stored label that matches no designation on the bound concept comes
        back ``result=False`` with the server's own message (PRD Appendix A.11,
        row 22). With ``value_set_url`` set it is ``ValueSet/$validate-code``,
        FR-10's coded-property binding check.

        Single-code by nature, so not the way to validate the catalogue
        (FR-52). One call per row is legitimate only in the seeding
        transform's designation-reconciliation pass, where the delta is the
        workload.
        """
        ...
