"""The FR-52 batch validation sweep, the FR-84 hierarchy check and FR-97's
designation reconciliation probe.

The P0 seeding transform (``nptc_transform.terminology_check``,
``nptc_transform.designation_check``) and the backend's scheduled sweep both
drive this module, so the migration path has one validation implementation
(FR-74), over the ``TerminologyClient`` contract (FR-53, ADR-0003).

The subject is request count. One ``$validate-code`` per code per edition is
40,000 requests at the 20,000-entry planning ceiling, so FR-52 fixes the shape
below and the tests assert it by call count:

1. **Bulk status.** One ``ValueSet/$expand`` per chunk of ``chunk_size``
   codes, over the ECL enumerating that chunk, with ``activeOnly=true``. A
   returned code exists in the edition and is active.
2. **Delta.** One ``CodeSystem/$lookup`` per code the expansion did not
   return. It separates "inactive" (FR-46's reason and historical association
   come back with it) from "not in this edition".
3. **Bounded concurrency on the delta**, ``max_concurrency`` at a time,
   submitted in batches so a failure stops the next batch from being queued.
4. **The FR-84 hierarchy check**, ``(chunk) MINUS <<71388002`` over the same
   chunks. One disjunction for the whole catalogue is too large to send
   (about 340KB of percent-encoded ECL at the ceiling; ADR-0005).
5. **``confirm_labels`` (FR-97)** probes only the labels ``designation_check.py``
   could not settle against ``SweepResult.designations``, reusing item 3's
   batching.

Retry and backoff live in ``OntoserverClient``. Failure is always an
exception (``errors.py``): a sweep that cannot reach the server must not
return a ``SweepResult`` of absences, which would read as a catalogue of errors
instead of an outage (FR-54).
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
from nptc_shared.terminology.errors import (
    TerminologyConfigError,
    TerminologyError,
    is_concept_absence,
)
from nptc_shared.terminology.models import (
    PROCEDURE_ROOT_CODE,
    Edition,
    ExpandedConcept,
    LookupResult,
)
from nptc_shared.terminology.snomed import ecl_set_of, semantic_tag

__all__ = [
    "PROCEDURE_SEMANTIC_TAG",
    "ConceptDesignations",
    "ConceptTag",
    "LabelConfirmation",
    "SweepResult",
    "TerminologySweep",
]

#: The tag FR-99 expects under ``<<71388002``. Any other tag is a warning, not
#: an error: subsumption does not imply the tag (PRD Appendix A.10: ``71388002``
#: \|Procedure\| subsumes ``243120004`` \|Regime/therapy (regime/therapy)\|).
PROCEDURE_SEMANTIC_TAG = "procedure"

#: ``inactive`` is requested explicitly because FHIR R4 does not oblige a server
#: to return unrequested properties, and a ``None`` ``LookupResult.inactive``
#: would put an active code in ``run()``'s ``inactive`` bucket. The rest are
#: FR-46's inactivation reason and historical associations, free on a call
#: already being made.
_LOOKUP_PROPERTIES: tuple[str, ...] = (
    "inactive",
    "inactivationReason",
    "SAME_AS",
    "MOVED_TO",
    "POSSIBLY_EQUIVALENT_TO",
    "WAS_A",
    "REPLACED_BY",
)


@dataclass(frozen=True, slots=True)
class ConceptTag:
    """A concept's served FSN and the semantic tag read off it (FR-99).

    ``tag`` is never ``None``: an FSN with no tag at all is the un-taggable case
    counted in ``unresolved_fsn_count``.
    """

    code: str
    fully_specified_name: str
    tag: str


@dataclass(frozen=True, slots=True)
class ConceptDesignations:
    """One active concept's designation set, as resolved by the status pass (FR-97).

    A de-duplicated, sorted projection, not the raw ``ExpandedConcept`` objects:
    ``_expand_chunk`` tolerates overlapping pages, so the raw list can hold a
    concept twice and two readers could disagree about which copy won.
    """

    code: str
    #: The served FSN with its semantic tag (FR-82). ``None`` when the expansion
    #: returned no FSN, the case ``SweepResult.unresolved_fsn_count`` counts.
    fully_specified_name: str | None
    #: ``display`` under the edition's ``display_language`` (FR-82): the AU
    #: preferred term for the AU edition. ``None`` if the server reported none.
    display: str | None
    #: Every designation value returned, de-duplicated and sorted. Verbatim
    #: (FR-82): callers apply their own normalisation
    #: (``nptc_shared.text.normalise_for_comparison``).
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LabelConfirmation:
    """One ``CodeSystem/$validate-code`` probe's answer (FR-97).

    Reserved for a published label that matches nothing in the designations a
    bulk ``$expand`` returned, never one call per catalogue row (FR-52).

    ``matched`` is the server's ``result`` boolean as FHIR R4 defines it, never
    qualified by ``message``. Trusting ``result`` alone, and its cost, are
    recorded in ADR-0006's 2026-09-30 amendment.
    """

    code: str
    display: str
    matched: bool
    message: str | None = None


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
    #: Concepts the expansion returned with no FSN. FR-99 cannot run over them,
    #: because "no tag observed" is not evidence of a wrong tag. Zero on a
    #: server that honours ``includeDesignations``; persistently nonzero means
    #: FR-99 is silently not running and the server's designation shape needs
    #: checking (ADR-0005).
    unresolved_fsn_count: int = 0
    #: Every delta ``$lookup`` result, kept for FR-46's inactivation-reason and
    #: association pairing so callers need not look the codes up again.
    lookups: tuple[LookupResult, ...] = ()
    #: Every active concept's designation set, sorted by code (see
    #: ``ConceptDesignations``), from both passes: a code the delta ``$lookup``
    #: confirmed active gets an entry projected from that response. A caller
    #: never has to tell "no designations" from "absent or inactive" for a code
    #: listed active.
    designations: tuple[ConceptDesignations, ...] = ()
    #: Every fully qualified version URI the server reported it resolved
    #: against, from any request in the sweep (FR-48). Normally one.
    resolved_versions: tuple[str, ...] = field(default_factory=tuple)


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class TerminologySweep:
    """Resolves a whole catalogue's codes against one edition, in batches.

    Holds a ``TerminologyClient`` and the two tuning knobs FR-52 asks to be
    configurable. Stateless between ``run`` calls, so one sweep serves every
    edition (FR-47, FR-74).
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
            raise TerminologyConfigError(f"chunk_size must be at least 1, got {chunk_size}")
        if max_concurrency < 1:
            raise TerminologyConfigError(
                f"max_concurrency must be at least 1, got {max_concurrency}"
            )
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

        ``codes`` is de-duplicated and sorted first, so the request sequence
        depends on the set of codes, not the row order (FR-73).

        Every code must be a well-formed SCTID: ``ecl_set_of`` raises
        ``ValueError`` rather than concatenating an arbitrary string into ECL.
        The caller screens malformed codes (FR-06), because it has the row and
        cell reference to report them against.
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
        delta_active_designations: list[ConceptDesignations] = []
        for code, lookup in lookups:
            if lookup is None:
                absent.append(code)
                continue
            if lookup.resolved_version is not None:
                versions.add(lookup.resolved_version)
            # The expansion already said "not active"; the lookup overturns that
            # only if it explicitly reports `inactive` false. A server that
            # reports no `inactive` leaves the expansion's verdict standing.
            if lookup.inactive is False:
                active.add(code)
                # This code never reached `concepts` (that is why it is in the
                # delta), so `_project_designations` cannot cover it. Without
                # this entry it would be active with no designations,
                # indistinguishable from absent or inactive.
                delta_active_designations.append(_designations_from_lookup(lookup))
            else:
                inactive.append(code)

        # Absent codes stay out of the hierarchy check: that shrinks the ECL and
        # avoids relying on servers tolerating an unknown concept in a disjunction.
        resolved_codes = tuple(sorted(active | set(inactive)))
        violations = self._check_hierarchy(resolved_codes, edition=edition, versions=versions)
        violating = set(violations)
        # Disjoint by construction: `delta_active_designations` only covers
        # codes `concepts` lacks, so no code appears twice before the sort.
        designations = tuple(
            sorted(
                (*_project_designations(concepts), *delta_active_designations),
                key=lambda entry: entry.code,
            )
        )
        unexpected_tags, unresolved_fsn_count = _unexpected_tags(designations, exclude=violating)

        return SweepResult(
            edition_label=edition.label,
            active=tuple(sorted(active)),
            inactive=tuple(sorted(inactive)),
            absent=tuple(sorted(absent)),
            hierarchy_violations=violations,
            unexpected_semantic_tags=unexpected_tags,
            unresolved_fsn_count=unresolved_fsn_count,
            lookups=tuple(
                sorted(
                    (lookup for _code, lookup in lookups if lookup is not None),
                    key=lambda result: result.code,
                )
            ),
            designations=designations,
            resolved_versions=tuple(sorted(versions)),
        )

    # -- FR-97 -----------------------------------------------------------

    def confirm_labels(
        self, probes: Sequence[tuple[str, str]], *, edition: Edition
    ) -> tuple[LabelConfirmation, ...]:
        """One ``CodeSystem/$validate-code`` per unique ``(code, display)`` pair (FR-97).

        Only for rows a local check against ``SweepResult.designations`` could
        not settle (see ``client.py``'s ``validate_code``). ``probes`` is
        de-duplicated and sorted as in ``run()`` (FR-73), and batched like
        ``_resolve_delta``.

        Unlike ``_lookup``, every ``TerminologyError`` propagates, including
        not-found. The code was just resolved active in ``edition``, so a 404
        contradicts the status pass rather than answering a question, and an
        unreachable server must never be recorded as designation defects (FR-54).
        """
        unique = tuple(sorted(set(probes)))
        if not unique:
            return ()
        if self._max_concurrency == 1 or len(unique) == 1:
            results = [self._confirm(code, display, edition=edition) for code, display in unique]
        else:
            results = []
            with ThreadPoolExecutor(
                max_workers=self._max_concurrency, thread_name_prefix="nptc-tx-sweep"
            ) as pool:
                for batch in _chunks(unique, self._max_concurrency):
                    futures = [
                        pool.submit(self._confirm, code, display, edition=edition)
                        for code, display in batch
                    ]
                    # `.result()` re-raises before the next batch is submitted
                    # (see `_resolve_delta`).
                    for future in futures:
                        results.append(future.result())
        return tuple(
            sorted(results, key=lambda confirmation: (confirmation.code, confirmation.display))
        )

    def _confirm(self, code: str, display: str, *, edition: Edition) -> LabelConfirmation:
        result = self._client.validate_code(code, edition=edition, display=display)
        return LabelConfirmation(
            code=code, display=display, matched=result.result, message=result.message
        )

    # -- pass 1: bulk status -------------------------------------------------

    def _resolve_status(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[ExpandedConcept, ...]:
        """One ``$expand`` per chunk - ``ceil(len(codes) / chunk_size)`` requests.

        Sequential on purpose: FR-52 bounds concurrency only on the second pass,
        and this pass is the smaller request count (67 at the ceiling with the
        default chunk size) but the heavier per-request cost. See ADR-0005.

        ``includeDesignations`` brings the FSN back with the expansion, so FR-99's
        semantic-tag check costs no further requests.
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
                display_language=edition.display_language,
                active_only=True,
            )
            versions.update(expansion.resolved_versions)
            concepts.extend(expansion.concepts)
            # `Expansion.is_complete` compares one page against `total`; paging
            # must compare the accumulated count, or a short second page looks
            # like more work forever.
            if expansion.total is None or len(concepts) >= expansion.total:
                break
            if not expansion.concepts:
                # A server that promises more but returns an empty page would
                # loop. Stopping is safe: codes still missing fall through to
                # the second pass.
                break
            offset += len(expansion.concepts)
        return tuple(concepts)

    # -- pass 2: the delta ---------------------------------------------------

    def _resolve_delta(
        self, codes: Sequence[str], *, edition: Edition
    ) -> tuple[tuple[str, LookupResult | None], ...]:
        """One ``$lookup`` per unresolved code, ``max_concurrency`` at a time.

        ``None`` means the server says it does not have the code
        (``errors.is_concept_absence``). Any other failure aborts the sweep,
        because an unreachable server must never be recorded as a catalogue of
        absent codes (FR-54).

        Submitted in batches, not via ``Executor.map``, which submits every
        future eagerly: a failing lookup would not stop thousands of queued
        requests. Batching bounds the extra requests to one batch past the failure.
        """
        if not codes:
            return ()
        if self._max_concurrency == 1 or len(codes) == 1:
            # No pool for the serial case: it would only move tracebacks into a
            # worker thread.
            return tuple((code, self._lookup(code, edition=edition)) for code in codes)
        results: list[tuple[str, LookupResult | None]] = []
        with ThreadPoolExecutor(
            max_workers=self._max_concurrency, thread_name_prefix="nptc-tx-sweep"
        ) as pool:
            for batch in _chunks(codes, self._max_concurrency):
                futures = {pool.submit(self._lookup, code, edition=edition): code for code in batch}
                for future in futures:
                    # `.result()` re-raises inside the `with` before the next
                    # batch is submitted; the pool shuts down cleanly either way.
                    results.append((futures[future], future.result()))
        return tuple(results)

    def _lookup(self, code: str, *, edition: Edition) -> LookupResult | None:
        try:
            return self._client.lookup(code, edition=edition, properties=_LOOKUP_PROPERTIES)
        except TerminologyError as exc:
            if is_concept_absence(exc):
                return None
            raise

    # -- FR-84 ---------------------------------------------------------------

    def _check_hierarchy(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[str, ...]:
        """Expands ``(chunk) MINUS <<71388002`` per chunk (FR-84).

        A named wrapper over ``_expand_combined`` (ADR-0005).
        """
        return self._expand_combined(
            codes,
            operator="MINUS",
            rhs=f"<<{self._procedure_root}",
            edition=edition,
            versions=versions,
        )

    def _expand_combined(
        self, codes: Sequence[str], *, operator: str, rhs: str, edition: Edition, versions: set[str]
    ) -> tuple[str, ...]:
        """Chunked ``(chunk) <operator> <rhs>``, one request per chunk of ``codes``.

        One implementation for FR-84's hierarchy check and FR-75's
        specimen-attribute checks, which need the same chunking (ADR-0005).
        ``operator`` is ``MINUS`` or ``AND``.

        The caller passes only codes it knows resolve, because an ECL that
        enumerates absent codes cannot return them (see the hierarchy comment
        in ``run()``).

        Paging should rarely engage: ``count`` leaves room for every code in the
        chunk. It matters only if a server caps the page below the match count,
        where ignoring it would under-report.
        """
        matches: list[str] = []
        for chunk in _chunks(codes, self._chunk_size):
            matches.extend(
                self._expand_combined_chunk(
                    chunk, operator=operator, rhs=rhs, edition=edition, versions=versions
                )
            )
        return tuple(sorted(matches))

    def _expand_combined_chunk(
        self,
        chunk: Sequence[str],
        *,
        operator: str,
        rhs: str,
        edition: Edition,
        versions: set[str],
    ) -> tuple[str, ...]:
        ecl = f"({ecl_set_of(chunk)}) {operator} {rhs}"
        matches: list[str] = []
        offset = 0
        while True:
            expansion = self._client.expand(ecl, edition=edition, count=len(chunk), offset=offset)
            versions.update(expansion.resolved_versions)
            matches.extend(expansion.codes)
            if expansion.total is None or len(matches) >= expansion.total:
                break
            if not expansion.concepts:
                break
            offset += len(expansion.concepts)
        return tuple(matches)

    # -- FR-75 -----------------------------------------------------------

    def codes_without_attribute(
        self,
        codes: Sequence[str],
        *,
        attribute: str,
        edition: Edition,
        versions: set[str] | None = None,
    ) -> tuple[str, ...]:
        """Which of ``codes`` constrains no value at all for ``attribute`` (FR-75).

        Chunked ``(chunk) MINUS (* : <attribute> = *)``: every returned code
        resolves in ``edition`` and has no relationship for ``attribute``, the
        basis for ``TERM_SPECIMEN_NOT_MODELLED``. Pass only the codes the caller
        needs an answer for, not the whole catalogue (see ``semantic_drift.py``).

        ``versions``, if given, receives every resolved version URI the requests
        reported (FR-48), as ``run()`` does.
        """
        local_versions: set[str] = set()
        result = self._expand_combined(
            codes,
            operator="MINUS",
            rhs=f"(* : {attribute} = *)",
            edition=edition,
            versions=local_versions,
        )
        if versions is not None:
            versions.update(local_versions)
        return result

    def codes_with_attribute_value(
        self,
        codes: Sequence[str],
        *,
        attribute: str,
        root: str,
        edition: Edition,
        versions: set[str] | None = None,
    ) -> tuple[str, ...]:
        """Which of ``codes`` constrains ``attribute`` to a value subsumed by ``root``.

        Chunked ``(chunk) AND (* : <attribute> = <<root>)``. The ``<<`` on the
        value side is deliberate: it counts a descendant (for example "Urine
        specimen from catheter" under "Urine specimen") as agreeing, where an
        exact match would report a false ``TERM_SPECIMEN_DIFFERS``.

        ``versions`` behaves as in ``codes_without_attribute``.
        """
        local_versions: set[str] = set()
        result = self._expand_combined(
            codes,
            operator="AND",
            rhs=f"(* : {attribute} = <<{root})",
            edition=edition,
            versions=local_versions,
        )
        if versions is not None:
            versions.update(local_versions)
        return result

    def describe(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str] | None = None
    ) -> tuple[ConceptDesignations, ...]:
        """The designation sets of ``codes``, resolved directly (FR-75).

        Chunked ``$expand`` with ``includeDesignations=true`` through
        ``_expand_chunk`` and ``_project_designations``, the path FR-97 uses, so
        the two callers cannot disagree about duplicated pages.

        Does not go through ``run()``: these are specimen concepts, not
        procedures, so its FR-84 and FR-99 checks would misfire on each (never
        under ``<<71388002``, never tagged ``(procedure)``).

        ``versions`` behaves as in ``codes_without_attribute``.
        """
        unique = tuple(sorted(set(codes)))
        if not unique:
            return ()
        local_versions: set[str] = set()
        concepts: list[ExpandedConcept] = []
        for chunk in _chunks(unique, self._chunk_size):
            concepts.extend(self._expand_chunk(chunk, edition=edition, versions=local_versions))
        if versions is not None:
            versions.update(local_versions)
        return _project_designations(concepts)


def _project_designations(concepts: Iterable[ExpandedConcept]) -> tuple[ConceptDesignations, ...]:
    """Reduces the expansion's possibly-duplicated concepts to one
    ``ConceptDesignations`` per code, sorted.

    ``_expand_chunk`` tolerates a server that ignores ``offset`` or overlaps
    pages. Without this, a duplicate page would double a concept's values.
    First occurrence wins for every field.
    """
    projected: dict[str, ConceptDesignations] = {}
    for concept in concepts:
        if concept.code in projected:
            continue
        fsn = next(
            (
                designation.value
                for designation in concept.designations
                if designation.is_fully_specified_name
            ),
            None,
        )
        values = tuple(sorted({designation.value for designation in concept.designations}))
        projected[concept.code] = ConceptDesignations(
            code=concept.code,
            fully_specified_name=fsn,
            display=concept.display,
            values=values,
        )
    return tuple(sorted(projected.values(), key=lambda entry: entry.code))


def _designations_from_lookup(lookup: LookupResult) -> ConceptDesignations:
    """The designation set of a delta code confirmed active, from the
    ``$lookup`` ``run()`` already made for it.

    The bulk expansion never returned this code, so ``_project_designations`` has
    nothing to project. This keeps ``SweepResult.designations`` covering every
    active concept at no extra request.
    """
    values = tuple(sorted({designation.value for designation in lookup.designations}))
    return ConceptDesignations(
        code=lookup.code,
        fully_specified_name=lookup.fully_specified_name,
        display=lookup.display,
        values=values,
    )


def _unexpected_tags(
    designations: Iterable[ConceptDesignations], *, exclude: set[str]
) -> tuple[tuple[ConceptTag, ...], int]:
    """FR-99: concepts under ``<<71388002`` whose tag is not ``(procedure)``.

    Read off the FSN the status pass already resolved, so no request per code
    (FR-52).

    Two deliberate exclusions. A concept already reported as an FR-84 violation
    is skipped: its tag is a symptom of an error already raised. A concept with
    no FSN is skipped because "no tag observed" is not evidence of a wrong tag
    (FR-54), but it is counted in ``unresolved_fsn_count``. Otherwise a server
    that ignores ``includeDesignations`` would make the whole check pass silently.
    """
    tagged: dict[str, ConceptTag] = {}
    unresolved = 0
    for entry in designations:
        if entry.code in exclude:
            continue
        if entry.fully_specified_name is None:
            unresolved += 1
            continue
        tag = semantic_tag(entry.fully_specified_name)
        if tag is not None and tag != PROCEDURE_SEMANTIC_TAG:
            tagged[entry.code] = ConceptTag(
                code=entry.code, fully_specified_name=entry.fully_specified_name, tag=tag
            )
    return tuple(sorted(tagged.values(), key=lambda entry: entry.code)), unresolved
