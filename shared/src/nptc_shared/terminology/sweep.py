"""The FR-52 batch validation sweep, the FR-84 hierarchy check, and FR-97's
designation reconciliation probe.

Written once here, not in the transform, because FR-74 forbids a second
validation implementation for the migration path: the P0 seeding transform
(``nptc_transform.terminology_check``, ``nptc_transform.designation_check``)
and the backend's scheduled validation sweep both drive this module, over the
same ``TerminologyClient`` contract (FR-53, ADR-0003).

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
3. **Bounded concurrency on that second pass**, ``max_concurrency`` at a
   time, submitted in batches rather than all at once - a failure in one
   batch stops the next batch from ever being queued.
4. **Chunked ``expand`` for the FR-84 hierarchy check too**:
   ``(chunk) MINUS <<71388002`` over the same ``chunk_size`` chunks as the
   status pass, not one request for the whole catalogue - at the PRD's
   20,000-code planning ceiling a single disjunction is itself too large to
   send (measured: ~340KB of percent-encoded ECL). Anything a chunk's
   expansion returns is a code that exists and is not a procedure.
5. **``confirm_labels`` (FR-97) never issues one ``$validate-code`` per row.**
   Its caller (``designation_check.py``) classifies published labels against
   ``SweepResult.designations`` - itself already fetched for free by pass 1 -
   and calls this only for the labels that check could not settle locally.
   Bounded concurrency and batched submission, reusing pass 2's discipline.

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
from nptc_shared.terminology.errors import (
    TerminologyConfigError,
    TerminologyError,
    TerminologyStatusError,
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

#: ``$lookup`` properties requested on every delta call. ``inactive`` is
#: requested explicitly rather than relying on a server volunteering it
#: unprompted: FHIR R4 does not require a server to return any property that
#: wasn't asked for, and ``LookupResult.inactive`` coming back ``None`` (not
#: reported, distinct from "reported false") would otherwise send an active
#: code into the ``inactive`` bucket in ``run()`` below - a false blocking
#: defect. The rest are FR-46's own inactivation-reason/historical-association
#: table; requesting them costs nothing extra on a call already being made,
#: and is what lets ``SweepResult.lookups`` make good on its own docstring's
#: promise to issue #28.
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

    ``tag`` is never ``None`` here: ``_unexpected_tags`` only constructs one
    once it has already confirmed ``semantic_tag(fsn) is not None`` - an FSN
    with no identifiable tag at all is a different, un-taggable case (see
    ``unresolved_fsn_count``), not a ``ConceptTag`` with a missing ``tag``.
    """

    code: str
    fully_specified_name: str
    tag: str


@dataclass(frozen=True, slots=True)
class ConceptDesignations:
    """One active concept's designation set, as resolved by the status pass.

    The raw material for FR-97's seeding-time designation reconciliation
    (issue #28): rather than hand the caller the ``ExpandedConcept`` objects
    the bulk ``$expand`` returned, this is a de-duplicated, sorted projection
    of them - the expansion's own paging loop (``_expand_chunk``) tolerates a
    server that returns overlapping pages, so the raw concepts are neither
    de-duplicated nor sorted, and handing them out as-is would give a caller
    a second, independent opportunity to disagree with ``_unexpected_tags``
    about which of two duplicated pages won.
    """

    code: str
    #: The served FSN, semantic tag intact (FR-82). ``None`` when the
    #: expansion returned no identifiable FSN designation - the same case
    #: ``SweepResult.unresolved_fsn_count`` counts.
    fully_specified_name: str | None
    #: The concept's ``display`` under this edition's ``display_language``
    #: (FR-82) - the AU preferred term for the AU edition. ``None`` if the
    #: server reported none.
    display: str | None
    #: Every designation value the expansion returned for this concept,
    #: de-duplicated and sorted. Verbatim (FR-82): never stripped, never
    #: normalised - a caller comparing against these applies its own
    #: normalisation (see ``nptc_shared.text.normalise_for_comparison``).
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LabelConfirmation:
    """One ``CodeSystem/$validate-code`` probe's answer (FR-97).

    Reserved for the one case FR-97's designation reconciliation cannot
    settle locally: a published label that matches nothing in the
    designations a bulk ``$expand`` returned. ``client.py``'s own
    ``validate_code`` docstring calls this "the delta ... where the delta is
    the workload" - the same discipline this module has enforced since
    FR-52's batch sweep: never one such call per row of a large catalogue,
    only for the rows a cheaper pass could not already resolve.

    ``matched`` is exactly the server's own ``result`` boolean, trusted as
    the FHIR R4 ``$validate-code`` contract defines it: "whether the code
    (system/code/display) is valid" - not re-derived from, or qualified by,
    ``message``. This is a stated precondition, not an oversight: the spec
    documents ``message`` as "error details, if result = false", not as a
    caveat a caller should apply to a ``true`` result, and there is no
    schema for what a free-text message would mean if it disagreed with
    ``result`` - inferring one would be guessing at an undefined contract,
    not implementing the defined one. The consequence is real and worth
    naming rather than hiding: a non-conformant server that returns
    ``result=true`` for a display it does not genuinely recognise (a
    lenient acceptance-with-a-caveat, say) downgrades what should have been
    a blocking FR-97 outcome to informational drift, because the design in
    ADR-0006 makes the probe strictly monotone - trusting ``result`` this
    way is *why* that monotonicity holds, not a gap in it. Guarding against
    a non-conformant server is a decision to make deliberately (see
    ``client.py``'s own trust boundary for the terminology server), not one
    to smuggle in as free-text pattern-matching here.
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
    #: Concepts the bulk expansion returned with no identifiable FSN
    #: designation - the FR-99 check could not run over them at all, because
    #: "no tag observed" is not evidence of a wrong tag and cannot be turned
    #: into a warning. Zero on a conformant server that honours
    #: ``includeDesignations``; a persistently nonzero count means the FR-99
    #: check is silently not running for those concepts and the server's
    #: designation shape should be checked (see ADR-0005).
    unresolved_fsn_count: int = 0
    #: Every ``$lookup`` the second pass resolved, by code - the raw material
    #: for FR-46's inactivation-reason/historical-association pairing, kept
    #: rather than discarded so the caller that needs it (issue #28, the
    #: backend's findings) does not have to look the same codes up again.
    lookups: tuple[LookupResult, ...] = ()
    #: Every active concept's designation set, sorted by code - see
    #: ``ConceptDesignations``. Covers both passes: the bulk pass's own
    #: concepts cost nothing further (``_resolve_status`` already fetches
    #: this with ``includeDesignations`` for FR-99's own semantic-tag check),
    #: and a code the bulk pass missed but the delta ``$lookup`` confirmed
    #: active still gets an entry, projected from that same lookup response -
    #: "every active concept" here is a real guarantee, not just the common
    #: case, so a caller (FR-97's reconciliation, FR-99's tag check) never
    #: has to distinguish "no designations projected" from "concept
    #: absent/inactive" for a code this field reports active.
    designations: tuple[ConceptDesignations, ...] = ()
    #: Every fully qualified version URI the server reported it resolved
    #: against, from any request in the sweep (FR-48). Normally one.
    resolved_versions: tuple[str, ...] = field(default_factory=tuple)


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


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
        delta_active_designations: list[ConceptDesignations] = []
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
                # This code never reached `concepts` at all (that is why it
                # was in the delta), so `_project_designations` below has
                # nothing to project it from - without this, it would be
                # reported active with no designation entry, and a caller
                # could not tell that apart from "absent/inactive" (see
                # `SweepResult.designations`'s own docstring).
                delta_active_designations.append(_designations_from_lookup(lookup))
            else:
                inactive.append(code)

        # Only codes known to exist go into the hierarchy check - an absent
        # code (already reported as such) never appears in a disjunction
        # sent to the server, which both shrinks the ECL and avoids relying
        # on every server tolerating an unknown-concept reference inside one.
        resolved_codes = tuple(sorted(active | set(inactive)))
        violations = self._check_hierarchy(resolved_codes, edition=edition, versions=versions)
        violating = set(violations)
        # Disjoint by construction - `delta_active_designations` only ever
        # covers codes `concepts` does not - so concatenating before the sort
        # can never collide two entries for the same code.
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
        """One ``CodeSystem/$validate-code`` per unique ``(code, display)`` pair.

        Reserved for FR-97's designation reconciliation, and only for the
        rows a local check against ``SweepResult.designations`` could not
        already settle - see ``client.py``'s own ``validate_code`` docstring.
        ``probes`` is de-duplicated and sorted first, the same discipline as
        ``run()`` and for the same reason: two rows citing the same code and
        label cost one call, not two, and the request sequence depends on the
        *set* of probes rather than row order (FR-73). Batched at
        ``max_concurrency`` using the same submit-a-batch-then-wait discipline
        as ``_resolve_delta``, reused rather than re-implemented, so a failure
        in one batch stops the next from ever being queued.

        Unlike ``_lookup``, this does **not** treat a "not found" answer as
        data: every ``TerminologyError`` propagates, aborting the sweep. A
        probe is only ever issued for a code this same sweep just resolved as
        active in ``edition``, so a 404 here is a contradiction with the
        status pass, not an answer to a question this call asked - and an
        unreachable server must never be recorded as a catalogue of
        designation defects (FR-54).
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
                    # `.result()` re-raises here, inside the `with` and before
                    # the next batch is ever submitted - see `_resolve_delta`.
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
                display_language=edition.display_language,
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

        Submitted in batches of ``max_concurrency``, not all at once:
        ``Executor.map`` submits every future the moment it is called
        (``[self.submit(...) for ...]``, eagerly, before any result is
        consumed), so a failing lookup would not stop the remaining codes -
        potentially thousands of them - from being queued and executed before
        the exception ever surfaces to the caller. Batching means a failure
        in one batch means the next batch is never submitted at all - at most
        one batch's worth of extra requests beyond the failure, not the whole
        remaining catalogue.
        """
        if not codes:
            return ()
        if self._max_concurrency == 1 or len(codes) == 1:
            # No pool at all for the serial case: a ThreadPoolExecutor here
            # would move every exception's traceback into a worker thread for
            # no concurrency in return.
            return tuple((code, self._lookup(code, edition=edition)) for code in codes)
        results: list[tuple[str, LookupResult | None]] = []
        with ThreadPoolExecutor(
            max_workers=self._max_concurrency, thread_name_prefix="nptc-tx-sweep"
        ) as pool:
            for batch in _chunks(codes, self._max_concurrency):
                futures = {pool.submit(self._lookup, code, edition=edition): code for code in batch}
                for future in futures:
                    # `.result()` re-raises here, inside the `with` and before
                    # the next batch is ever submitted - the pool is still
                    # shut down cleanly either way.
                    results.append((futures[future], future.result()))
        return tuple(results)

    def _lookup(self, code: str, *, edition: Edition) -> LookupResult | None:
        try:
            return self._client.lookup(code, edition=edition, properties=_LOOKUP_PROPERTIES)
        except TerminologyError as exc:
            if _is_absence(exc):
                return None
            raise

    # -- FR-84 ---------------------------------------------------------------

    def _check_hierarchy(
        self, codes: Sequence[str], *, edition: Edition, versions: set[str]
    ) -> tuple[str, ...]:
        """Expands ``(chunk) MINUS <<71388002`` per chunk (FR-84).

        A thin, byte-identical-ECL wrapper over ``_expand_combined`` - see
        that method for the chunking rationale (ADR-0005). Kept as its own
        named method rather than inlined at the one call site in ``run()``,
        so a reader looking for "where does FR-84's check live" finds a
        method named for it.
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

        Generalises what was originally ``_check_hierarchy``'s own MINUS-only
        chunking loop to any right-hand ECL expression and either top-level
        operator (``MINUS``/``AND``) - FR-75's specimen-attribute checks
        (``codes_without_attribute``, ``codes_with_attribute_value``) need the
        identical chunking discipline ADR-0005 measured for FR-84's hierarchy
        check (~340KB of percent-encoded ECL for an unchunked disjunction at
        the PRD's 20,000-code planning ceiling), so this is one implementation
        parameterised rather than a second copy of the same loop. Produces
        byte-identical ECL to the pre-existing FR-84 check for that call
        shape - only the plumbing moved, not the behaviour.

        A code absent from the edition cannot appear in any result here - an
        ECL enumerating codes only ever returns concepts that exist - so the
        caller is responsible for passing only codes it already knows resolve
        (see ``run()``'s own comment on this for the hierarchy check).

        Paging exists but should rarely engage: ``count`` asks for room for
        every code in the chunk. It engages only if a server caps the page
        below the number of matches, in which case the alternative is
        under-reporting them.
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
        self, codes: Sequence[str], *, attribute: str, edition: Edition
    ) -> tuple[str, ...]:
        """Which of ``codes`` constrains no value at all for ``attribute``.

        Chunked ``(chunk) MINUS (* : <attribute> = *)`` (issue #29's
        semantic-drift check, FR-75): everything the server returns from a
        chunk is a code that resolves in ``edition`` and carries no
        relationship for ``attribute`` whatsoever - the raw material for
        ``TERM_SPECIMEN_NOT_MODELLED``. ``codes`` should be restricted to the
        set of codes a caller actually needs an answer for (never the whole
        catalogue) - see ``semantic_drift.py``'s own request-count discipline.
        """
        versions: set[str] = set()
        return self._expand_combined(
            codes,
            operator="MINUS",
            rhs=f"(* : {attribute} = *)",
            edition=edition,
            versions=versions,
        )

    def codes_with_attribute_value(
        self, codes: Sequence[str], *, attribute: str, root: str, edition: Edition
    ) -> tuple[str, ...]:
        """Which of ``codes`` constrains ``attribute`` to a value subsumed by ``root``.

        Chunked ``(chunk) AND (* : <attribute> = <<root>)``. The ``<<`` on the
        *value* side - not just wrapping the whole refinement - is deliberate:
        it is what catches a descendant specimen value (e.g. "Urine specimen
        from catheter" under "Urine specimen") as agreeing with ``root``,
        rather than only an exact match. Dropping it would silently miss every
        such descendant and report a false ``TERM_SPECIMEN_DIFFERS``.
        """
        versions: set[str] = set()
        return self._expand_combined(
            codes,
            operator="AND",
            rhs=f"(* : {attribute} = <<{root})",
            edition=edition,
            versions=versions,
        )

    def describe(
        self, codes: Sequence[str], *, edition: Edition
    ) -> tuple[ConceptDesignations, ...]:
        """Every one of ``codes``'s designation sets, resolved directly - not
        through a hierarchy expression.

        Chunked ``$expand`` with ``includeDesignations=true`` over exactly
        ``codes`` (reusing ``_expand_chunk``, the same paging/dedup-tolerant
        primitive ``_resolve_status`` uses) and projected through the same
        ``_project_designations`` FR-97 already relies on, so the two callers
        can never disagree about which of two duplicated pages won.

        Deliberately does **not** go through ``run()``: these are specimen
        concepts (issue #29, FR-75), not procedures, and ``run()``'s FR-84
        hierarchy check and FR-99 semantic-tag check would both misfire on
        every one of them - a specimen concept is never subsumed by
        ``<<71388002`` and never tagged ``(procedure)``.
        """
        unique = tuple(sorted(set(codes)))
        if not unique:
            return ()
        versions: set[str] = set()
        concepts: list[ExpandedConcept] = []
        for chunk in _chunks(unique, self._chunk_size):
            concepts.extend(self._expand_chunk(chunk, edition=edition, versions=versions))
        return _project_designations(concepts)


def _project_designations(concepts: Iterable[ExpandedConcept]) -> tuple[ConceptDesignations, ...]:
    """Reduces the bulk expansion's raw, possibly-duplicated concepts to one
    de-duplicated, sorted ``ConceptDesignations`` per code.

    ``_expand_chunk``'s paging loop deliberately tolerates a server that
    ignores ``offset`` or overlaps pages (see its own docstring); without
    this, a duplicated page would double a concept's designation values, and
    two independent readers of the raw list (FR-97's reconciliation and
    FR-99's tag check) could disagree about which of two duplicate pages won.
    First occurrence wins, consistently for every field.
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
    """One delta-confirmed-active code's designation set, from the same
    ``$lookup`` response ``run()`` already made for it.

    The bulk expansion never returned this code at all - that is why it was
    in the delta - so ``_project_designations`` has nothing to build an entry
    from. Projecting one here from the lookup's own designations is what
    makes ``SweepResult.designations``'s "every active concept" promise true
    rather than "every active concept the bulk pass happened to return",
    with no extra request: the lookup already happened, for FR-46's own
    inactivation-reason/historical-association pairing.
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
    """FR-99: a concept under ``<<71388002`` whose tag is not ``(procedure)``.

    Read off the FSN the status pass already resolved - no extra request per
    code, which is what makes a per-concept check affordable at catalogue
    scale (FR-52).

    Two exclusions, both deliberate. A concept already reported as an FR-84
    violation is skipped: it is out of the procedure hierarchy altogether, so
    its tag is a symptom of an error already raised and repeating it as a
    warning is noise. A concept with no identifiable FSN is also skipped -
    "no tag observed" is not evidence of a wrong tag, and inventing a warning
    from missing data is worse than staying silent (FR-54) - but counted in
    the returned ``unresolved_fsn_count``, since *every* concept hitting this
    case (a server that doesn't honour ``includeDesignations``, or tags
    ``use`` non-standardly) would otherwise make the whole check pass
    silently and permanently, with nothing to show it never ran.
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
