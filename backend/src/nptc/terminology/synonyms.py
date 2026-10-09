"""The SNOMED CT synonyms an entry's detail page shows for its active binding (FR-53, FR-54).

**Fetched live, cached in process, never stored.** One `$lookup` against the AU edition per
code, kept for `SYNONYM_TTL_SECONDS`.

**Every synonym the server serves, not only the en-AU ones.** No FHIR operation on Ontoserver
6.29 separates the synonyms acceptable in the AU language refset from the rest: `$lookup` serves
each as `en` with `use` Synonym. A synonym here is any designation that is not the FSN and not a
refset's preferred term, so US spellings such as "Anemia" can appear.

**A short failure budget of its own.** The page waits on this call, and the shared client's
defaults (30 s, three retries) could hold it for over a minute. `interactive_config` gives it one
attempt and at most `INTERACTIVE_TIMEOUT_SECONDS`.

**No `TerminologyError` reaches the page as an error response (FR-54).** Each kind is cached
for as long as waiting could change it:

- A retryable failure (timeout, transport, 5xx, 429) means the server is down. Every code is
  `unavailable`, with no request, for `FAILURE_TTL_SECONDS`, so an outage costs one timeout a
  minute rather than one per uncached code.
- A concept absence usually means the code is not in the current AU edition, so it is
  `available` with no terms. Reporting the code itself is the validation sweep's job (FR-45).
  It is kept only for `ABSENCE_TTL_SECONDS`, because Ontoserver gives the same 404 when the
  server lacks the AU edition entirely: a misconfigured server must not hide every synonym
  for a day after it is fixed.
- Any other failure is `unavailable` for that code for `FAILURE_TTL_SECONDS`.

A `TerminologyConfigError` is re-raised, because it is a deployment fault for `nptc.api.errors`
to report.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from nptc_shared.terminology import (
    SNOMED_CT_AU,
    TerminologyClient,
    TerminologyConfig,
    TerminologyConfigError,
    TerminologyError,
    is_concept_absence,
)
from nptc_shared.terminology.models import Designation, LookupResult

__all__ = [
    "ABSENCE_TTL_SECONDS",
    "FAILURE_TTL_SECONDS",
    "INTERACTIVE_TIMEOUT_SECONDS",
    "SYNONYM_TTL_SECONDS",
    "SnomedSynonymResult",
    "SnomedSynonymSource",
    "SynonymStatus",
    "interactive_config",
    "select_synonyms",
]

#: AU releases are monthly, so a day-old synonym list is current for practical purposes.
SYNONYM_TTL_SECONDS: Final = 24 * 60 * 60.0
FAILURE_TTL_SECONDS: Final = 60.0
ABSENCE_TTL_SECONDS: Final = 60 * 60.0
#: A live `$lookup` took 0.3 to 0.9 s on 2026-10-09.
INTERACTIVE_TIMEOUT_SECONDS: Final = 3.0
#: The catalogue holds about 2,000 active bindings, with a ceiling near 5,000.
MAX_CACHED_CODES: Final = 10_000

#: How a FHIR server marks a designation as a language refset's preferred term.
_PREFERRED_FOR_LANGUAGE: Final = (
    "http://terminology.hl7.org/CodeSystem/hl7TermMaintInfra",
    "preferredForLanguage",
)
#: A designation tagged with a SNOMED CT language refset is a preferred term in that refset.
_REFSET_LANGUAGE_MARKER: Final = "-x-sctlang-"

_logger = logging.getLogger(__name__)


class SynonymStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SnomedSynonymResult:
    """`terms` is empty whenever `status` is `UNAVAILABLE`."""

    status: SynonymStatus
    terms: tuple[str, ...] = ()


_UNAVAILABLE: Final = SnomedSynonymResult(SynonymStatus.UNAVAILABLE)
_NONE: Final = SnomedSynonymResult(SynonymStatus.AVAILABLE)


def interactive_config(config: TerminologyConfig) -> TerminologyConfig:
    """`config` with one attempt and a timeout no longer than `INTERACTIVE_TIMEOUT_SECONDS`."""
    return replace(
        config,
        timeout_seconds=min(config.timeout_seconds, INTERACTIVE_TIMEOUT_SECONDS),
        max_retries=0,
    )


def select_synonyms(result: LookupResult) -> tuple[str, ...]:
    """The synonyms in `result`, in server order, without the FSN, any refset's preferred
    term or the served preferred term, each value once."""
    terms = (
        designation.value
        for designation in result.designations
        if _is_synonym(designation) and designation.value != result.display
    )
    return tuple(dict.fromkeys(terms))


def _is_synonym(designation: Designation) -> bool:
    if designation.is_fully_specified_name:
        return False
    if (designation.use_system, designation.use_code) == _PREFERRED_FOR_LANGUAGE:
        return False
    return _REFSET_LANGUAGE_MARKER not in (designation.language or "")


class SnomedSynonymSource:
    """A process-wide TTL cache over one `$lookup` per SNOMED CT code.

    Thread-safe, because the sync detail routes run on a worker thread pool. The lock is not
    held during the fetch, so two first views of one code can both ask the server; the
    alternative would queue every detail page behind one slow request.
    """

    def __init__(
        self,
        client: TerminologyClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = MAX_CACHED_CODES,
    ) -> None:
        self._client = client
        self._clock = clock
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, SnomedSynonymResult]] = OrderedDict()
        self._outage_until = float("-inf")
        self._lock = threading.Lock()

    def synonyms_for(self, code: str, *, preferred_term: str | None) -> SnomedSynonymResult:
        """`code`'s synonyms, without `preferred_term` - the stored AU preferred term, which
        the page already shows and which can differ from the one served today."""
        result = self._cached(code)
        if result is None:
            result = self._fetch(code)
        if preferred_term is None or result.status is SynonymStatus.UNAVAILABLE:
            return result
        return replace(result, terms=tuple(t for t in result.terms if t != preferred_term))

    def _cached(self, code: str) -> SnomedSynonymResult | None:
        with self._lock:
            now = self._clock()
            entry = self._entries.get(code)
            if entry is not None and now < entry[0]:
                return entry[1]
            if entry is not None:
                del self._entries[code]
            return _UNAVAILABLE if now < self._outage_until else None

    def _fetch(self, code: str) -> SnomedSynonymResult:
        try:
            lookup = self._client.lookup(
                code, edition=SNOMED_CT_AU, display_language=SNOMED_CT_AU.display_language
            )
        except TerminologyConfigError:
            raise
        except TerminologyError as exc:
            if is_concept_absence(exc):
                _logger.info("SNOMED CT code %s is not in the AU edition", code)
                return self._store(code, _NONE, ABSENCE_TTL_SECONDS)
            _logger.warning(
                "SNOMED CT synonyms unavailable for code %s: %s", code, type(exc).__name__
            )
            if exc.retryable:
                with self._lock:
                    self._outage_until = self._clock() + FAILURE_TTL_SECONDS
                return _UNAVAILABLE
            return self._store(code, _UNAVAILABLE, FAILURE_TTL_SECONDS)
        result = SnomedSynonymResult(SynonymStatus.AVAILABLE, select_synonyms(lookup))
        return self._store(code, result, SYNONYM_TTL_SECONDS)

    def _store(self, code: str, result: SnomedSynonymResult, ttl: float) -> SnomedSynonymResult:
        with self._lock:
            self._entries[code] = (self._clock() + ttl, result)
            self._entries.move_to_end(code)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return result
