"""The FR-52 batch validation sweep and the FR-84 hierarchy check.

Written once here, not in the transform, because FR-74 forbids a second
validation implementation for the migration path: the P0 seeding transform
(``nptc_transform.terminology_check``) and the backend's scheduled validation
sweep both drive this module, over the same ``TerminologyClient`` contract
(FR-53, ADR-0003).

**This module's whole subject is request count.** FR-52 exists because one
``$validate-code`` per code per edition is 40,000 sequential requests at the
PRD's 20,000-entry planning ceiling, and the failure mode it guards against is
not incorrectness but a design that quietly works at 50 codes and is
unusable - and inconsiderate to a shared server - at 20,000. So the shape here
is fixed by the requirement, and asserted by call count in the tests, not left
to judgement:

1. **Bulk status resolution.** One ``ValueSet/$expand`` per chunk of
   ``chunk_size`` codes, over the ECL enumerating exactly that chunk
   (``snomed.ecl_set_of``), with ``activeOnly=true``. A code in the result
   exists in the edition *and* is active; that settles the overwhelming
   majority of the catalogue in ``ceil(N / chunk_size)`` requests.
2. **A targeted second pass for the delta only.** Every code the expansion did
   not return gets one ``CodeSystem/$lookup``, which is what distinguishes
   "inactive" (FR-46's inactivation reason and historical association come
   back with it) from "not in this edition at all". The delta is a small
   fraction of the catalogue; that is the entire reason this pass is
   affordable.
3. **Bounded concurrency on that second pass**, ``max_concurrency`` at a time.
4. **One** ``expand`` for the whole FR-84 hierarchy check:
   ``(codes) MINUS <<71388002``. Anything the server returns is a code that
   exists and is not a procedure.

Retry, ``Retry-After`` and exponential backoff live one layer down, in
``OntoserverClient`` - not repeated here, so a sweep against the stub and a
sweep against a real server differ in no respect this module can see.

Failure is always an exception (``errors.py``): a sweep that cannot reach the
server raises rather than returning a ``SweepResult`` full of absences, which
would read as a catalogue of errors instead of an outage (FR-54).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from nptc_shared.terminology.client import TerminologyClient
from nptc_shared.terminology.config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_CONCURRENCY,
    TerminologyConfig,
)
from nptc_shared.terminology.errors import TerminologyError, TerminologyStatusError
from nptc_shared.terminology.models import (
    PROCEDURE_ROOT_CODE,
    Edition,
    ExpandedConcept,
    LookupResult,
)
from nptc_shared.terminology.snomed import ecl_set_of, semantic_tag

__all__ = [
    "PROCEDURE_SEMANTIC_TAG",
    "ConceptTag",
    "SweepResult",
    "TerminologySweep",
]

#: The semantic tag FR-99 expects on a concept under ``<<71388002``. Any other
#: tag is a warning, never an error - subsumption does not imply the tag (PRD
#: Appendix A.10: ``71388002`` \|Procedure\| subsumes ``243120004``
#: \|Regime/therapy (regime/therapy)\|).
PROCEDURE_SEMANTIC_TAG = "procedure"

#: ``OperationOutcome`` issue codes that mean "this code is not in this
#: edition" rather than "this request failed". A 4xx carrying one of these is
#: an *answer* to the second pass's question, so it is recorded as an absence
#: instead of being raised - every other failure still propagates.
_NOT_FOUND_ISSUE_CODES = frozenset({"not-found", "code-invalid", "invalid-code"})


@dataclass(frozen=True, slots=True)
class ConceptTag:
    """A concept's served FSN and the semantic tag read off it (FR-99)."""

    code: str
    fully_specified_name: str
    tag: str | None


@dataclass(frozen=True, slots=True)
class SweepResult:
    """Everything one edition's sweep resolved.

    Every collection is sorted, so two runs over the same catalogue against
    the same server release produce identical output (FR-73). ``active``,
    ``inactive`` and ``absent`` partition the codes handed in: a code is in
    exactly one of them.
    """

    edition_label: str
    active: tuple[str, ...] = ()
    inactive: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    hierarchy_violations: tuple[str, ...] = ()
    unexpected_semantic_tags: tuple[ConceptTag, ...] = ()
    #: Every ``$lookup`` the second pass resolved, by code - the raw material
    #: for FR-46's inactivation-reason/historical-association pairing, kept
    #: rather than discarded so the caller that needs it (issue #28, the
    #: backend's findings) does not have to look the same codes up again.
    lookups: tuple[LookupResult, ...] = ()
    #: Every fully qualified version URI the server reported it resolved
    #: against, from any request in the sweep (FR-48). Normally one.
    resolved_versions: tuple[str, ...] = field(default_factory=tuple)


def _chunks(codes: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(codes), size):
        yield codes[start : start + size]


def _is_absence(exc: TerminologyError) -> bool:
    """True if ``exc`` means "no such code here", not "the request failed".

    A ``$lookup`` for a code that is not in the edition is a 404 from a
    conformant FHIR server, which is an answer to the second pass's question.
    Deliberately narrow: only a 4xx (never a 5xx, never a transport failure,
    never a protocol error) and only a 404 or an ``OperationOutcome`` that
    says not-found in as many words. Widening this to "any 4xx" would turn a
    malformed request - one the server rejected outright - into 20,000
    plausible-looking "code not found" findings.
    """
    if not isinstance(exc, TerminologyStatusError):
        return False
    if not 400 <= exc.status_code < 500:
        return False
    return exc.status_code == 404 or any(
        issue.code in _NOT_FOUND_ISSUE_CODES for issue in exc.issues
    )


class TerminologySweep:
    """Resolves a whole catalogue's codes against one edition, in batches.

    Holds a ``TerminologyClient`` and the two tuning knobs FR-52 asks to be
    configurable. Stateless between ``run`` calls: the same sweep object is
    reused across editions (FR-47, FR-74) and the result carries the edition
    it belongs to.
    """

    def __init__(
        self,
        client: TerminologyClient,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        procedure_root: str = PROCEDURE_ROOT_CODE,
    ) -> None:
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be at least 1, got {chunk_size}")
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be at least 1, got {max_concurrency}")
        self._client = client
        self._chunk_size = chunk_size
        self._max_concurrency = max_concurrency
        self._procedure_root = procedure_root

    @classmethod
    def from_config(cls, client: TerminologyClient, config: TerminologyConfig) -> TerminologySweep:
        """A sweep configured from ``NPTC_TX_CHUNK_SIZE``/``NPTC_TX_MAX_CONCURRENCY``."""
        return cls(client, chunk_size=config.chunk_size, max_concurrency=config.max_concurrency)

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def run(self, codes: Iterable[str], *, edition: Edition) -> SweepResult:
        """Sweeps ``codes`` against ``edition``: status, hierarchy, semantic tags.

        ``codes`` is de-duplicated and sorted first - a code bound by twenty
        catalogue entries costs one slot in one chunk, not twenty, and the
        request sequence depends on the *set* of codes rather than on the row
        order they arrived in (FR-73).

        Every code must already be a well-formed SCTID: ``ecl_set_of`` raises
        ``ValueError`` otherwise rather than concatenating an arbitrary string
        into an ECL query. Screening malformed codes out - and reporting them
        as the defect they are (FR-06) - belongs to the caller, which has the
        row and cell reference to report them against; this module never sees
        one.
        """
        unique = tuple(sorted(set(codes)))
        if not unique:
            return SweepResult(edition_label=edition.label)

        versions: set[str] = set()
        concepts = self._resolve_status(unique, edition=edition, versions=versions)
        resolved = {concept.code for concept in concepts}

        lookups = self._resolve_delta(
            tuple(code for code in unique if code not in resolved), edition=edition
        )
        inactive: list[str] = []
        absent: list[str] = []
        active = set(resolved)
        for code, lookup in lookups:
            if lookup is None:
                absent.append(code)
                continue
            if lookup.resolved_version is not None:
                versions.add(lookup.resolved_version)
            # The expansion already answered "not active" for this code; the
            # lookup only overturns that if it says so explicitly. A server
            # that reports no `inactive` property at all leaves the
            # expansion's verdict standing rather than silently promoting the
            # code back to active.
            if lookup.inactive is False:
                active.add(code)
            else:
                inactive.append(code)

        violations = self._check_hierarchy(unique, edition=edition, versions=versions)
        violating = set(violations)

        return SweepResult(
            edition_label=edition.label,
            active=tuple(sorted(active)),
            inactive=tuple(sorted(inactive)),
            absent=tuple(sorted(absent)),
            hierarchy_violations=violations,
            unexpected_semantic_tags=_unexpected_tags(concepts, exclude=violating),
            lookups=tuple(
                sorted(
                    (lookup for _code, lookup in lookups if lookup is not None),
                    key=lambda result: result.code,
                )
            ),
            resolved_versions=tuple(sorted(versions)),
        )

    # -- pass 1: bulk status -------------------------------------------------

    def _resolve_status(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[ExpandedConcept, ...]:
        """One ``$expand`` per chunk - ``ceil(len(codes) / chunk_size)`` requests.

        Sequential on purpose. FR-52 puts bounded concurrency on the *second*
        pass, and this one is both the smaller number of requests (67 at the
        20,000-code ceiling with the default chunk size) and the heavier
        per-request cost for the server. See ADR-0005.

        ``includeDesignations`` is on so the FSN comes back with the
        expansion: FR-99's semantic-tag check then costs no additional
        requests at all, which is the only reason it can be afforded per-code.
        """
        found: list[ExpandedConcept] = []
        for chunk in _chunks(codes, self._chunk_size):
            found.extend(self._expand_chunk(chunk, edition=edition, versions=versions))
        return tuple(found)

    def _expand_chunk(
        self, chunk: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[ExpandedConcept, ...]:
        ecl = ecl_set_of(chunk)
        concepts: list[ExpandedConcept] = []
        offset = 0
        while True:
            expansion = self._client.expand(
                ecl,
                edition=edition,
                count=len(chunk),
                offset=offset,
                include_designations=True,
                active_only=True,
            )
            versions.update(expansion.resolved_versions)
            concepts.extend(expansion.concepts)
            # `Expansion.is_complete` compares one *page* against `total`;
            # paging has to compare the accumulated count instead, or a
            # second page that is itself shorter than `total` looks like more
            # work forever.
            if expansion.total is None or len(concepts) >= expansion.total:
                break
            if not expansion.concepts:
                # A server that promises more but returns an empty page would
                # otherwise loop here. Stopping is safe rather than lossy:
                # every code still missing falls through to the second pass,
                # which resolves it one request at a time.
                break
            offset += len(expansion.concepts)
        return tuple(concepts)

    # -- pass 2: the delta ---------------------------------------------------

    def _resolve_delta(
        self, codes: Sequence[str], *, edition: Edition
    ) -> tuple[tuple[str, LookupResult | None], ...]:
        """One ``$lookup`` per unresolved code, ``max_concurrency`` at a time.

        ``None`` for a code the server says it does not have (see
        ``_is_absence``); any other failure propagates, aborting the sweep -
        an unreachable server must never be recorded as a catalogue of absent
        codes (FR-54).
        """
        if not codes:
            return ()
        if self._max_concurrency == 1 or len(codes) == 1:
            # No pool at all for the serial case: a ThreadPoolExecutor here
            # would move every exception's traceback into a worker thread for
            # no concurrency in return.
            return tuple((code, self._lookup(code, edition=edition)) for code in codes)
        with ThreadPoolExecutor(
            max_workers=self._max_concurrency, thread_name_prefix="nptc-tx-sweep"
        ) as pool:
            results = pool.map(lambda code: self._lookup(code, edition=edition), codes)
            # `map` yields in input order and re-raises the first failure here,
            # inside the `with`, so the pool is still shut down cleanly.
            return tuple(zip(codes, results, strict=True))

    def _lookup(self, code: str, *, edition: Edition) -> LookupResult | None:
        try:
            return self._client.lookup(code, edition=edition)
        except TerminologyError as exc:
            if _is_absence(exc):
                return None
            raise

    # -- FR-84 ---------------------------------------------------------------

    def _check_hierarchy(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[str, ...]:
        """One request: expand ``(codes) MINUS <<71388002`` (FR-84).

        Everything the server returns is a code that resolves in this edition
        and is not subsumed by the procedure root. A code that is absent from
        the edition cannot appear - an ECL enumerating codes only ever returns
        concepts that exist - so an AU-only code checked against the
        International edition is reported by the status pass as absent, not
        here as a false violation.

        The parenthesised code list is a disjunction of *literal* codes, never
        ``<code``: ``<`` is ECL's descendant-of operator, and asking for a leaf
        procedure's descendants returns nothing, which would make this check
        pass every code forever (see ``snomed.ecl_set_of``).

        Paging exists but should never engage: ``count`` asks for room for
        every code handed in, and the expected result is empty. It engages
        only if a server caps the page below the number of violations, in
        which case the alternative is under-reporting them.
        """
        ecl = f"({ecl_set_of(codes)}) MINUS <<{self._procedure_root}"
        violations: list[str] = []
        offset = 0
        while True:
            expansion = self._client.expand(ecl, edition=edition, count=len(codes), offset=offset)
            versions.update(expansion.resolved_versions)
            violations.extend(expansion.codes)
            if expansion.total is None or len(violations) >= expansion.total:
                break
            if not expansion.concepts:
                break
            offset += len(expansion.concepts)
        return tuple(sorted(violations))


def _unexpected_tags(
    concepts: Iterable[ExpandedConcept], *, exclude: set[str]
) -> tuple[ConceptTag, ...]:
    """FR-99: a concept under ``<<71388002`` whose tag is not ``(procedure)``.

    Read off the FSN designation the bulk expansion already returned - no
    extra request per code, which is what makes a per-concept check
    affordable at catalogue scale (FR-52).

    Two exclusions, both deliberate. A concept already reported as an FR-84
    violation is skipped: it is out of the procedure hierarchy altogether, so
    its tag is a symptom of an error already raised and repeating it as a
    warning is noise. A concept whose FSN the server did not return is also
    skipped - "no tag observed" is not evidence of a wrong tag, and inventing
    a warning from missing data is worse than staying silent (FR-54).
    """
    tagged: list[ConceptTag] = []
    for concept in concepts:
        if concept.code in exclude:
            continue
        fsn = next(
            (
                designation.value
                for designation in concept.designations
                if designation.is_fully_specified_name
            ),
            None,
        )
        if fsn is None:
            continue
        tag = semantic_tag(fsn)
        if tag is not None and tag != PROCEDURE_SEMANTIC_TAG:
            tagged.append(ConceptTag(code=concept.code, fully_specified_name=fsn, tag=tag))
    return tuple(sorted(tagged, key=lambda entry: entry.code))
