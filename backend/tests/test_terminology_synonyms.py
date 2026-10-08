"""Unit tests for `nptc.terminology.synonyms`: the filter, the failure path and the cache.

No database, no HTTP. `_ANAEMIA` copies the designations a live AU-edition `$lookup` served for
271737000 on 2026-10-09, so the filter is checked against the server's real shape as well as
the stub's.
"""

from __future__ import annotations

import pytest

from nptc.terminology.synonyms import (
    FAILURE_TTL_SECONDS,
    INTERACTIVE_TIMEOUT_SECONDS,
    SYNONYM_TTL_SECONDS,
    SnomedSynonymResult,
    SnomedSynonymSource,
    SynonymStatus,
    interactive_config,
    select_synonyms,
)
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    SNOMED_CT_AU,
    SNOMED_SYSTEM,
    Designation,
    Edition,
    LookupResult,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyConfig,
    TerminologyConfigError,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)
from nptc_shared.terminology.stub import StubNotSeededError, StubRequest

_HL7 = "http://terminology.hl7.org/CodeSystem/hl7TermMaintInfra"
_SYNONYM_USE = "900000000000013009"
_FSN_USE = "900000000000003001"


def _synonym(value: str) -> Designation:
    return Designation(value=value, language="en", use_system=SNOMED_SYSTEM, use_code=_SYNONYM_USE)


def _preferred(value: str, language: str) -> Designation:
    return Designation(
        value=value, language=language, use_system=_HL7, use_code="preferredForLanguage"
    )


_ANAEMIA = LookupResult(
    code="271737000",
    system=SNOMED_SYSTEM,
    display="Anaemia",
    designations=(
        _synonym("Absolute anaemia"),
        _synonym("Absolute anemia"),
        _preferred("Anaemia", "en"),
        _preferred("Anaemia", AU_LANGUAGE_TAG),
        _preferred("Anaemia", "en-x-sctlang-90000000-00005080-04"),
        _synonym("Anemia"),
        _preferred("Anemia", "en-x-sctlang-90000000-00005090-07"),
        Designation(
            value="Anemia (disorder)", language="en", use_system=SNOMED_SYSTEM, use_code=_FSN_USE
        ),
    ),
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _SwitchableClient(StubTerminologyClient):
    """The seeded stub, failing every `lookup` while `down` is set."""

    def __init__(self, *, down: bool = False) -> None:
        super().__init__(concepts=_SEEDED)
        self.down = down

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> LookupResult:
        if self.down:
            self._requests.append(StubRequest(Operation.LOOKUP, code))
            raise TerminologyTransportError("connection refused")
        return super().lookup(
            code, edition=edition, properties=properties, display_language=display_language
        )

    @property
    def lookups(self) -> int:
        return sum(request.operation is Operation.LOOKUP for request in self.requests)


_SEEDED = (
    StubConcept(
        code="26604007",
        fsn="Complete blood count (procedure)",
        preferred_terms={
            AU_LANGUAGE_TAG: "Full blood count",
            "en-x-sctlang-90000000-00005090-07": "Complete blood count",
        },
        synonyms=("FBC - Full blood count", "Full blood count", "FBE"),
    ),
    StubConcept(
        code="167217005",
        fsn="Urine examination (procedure)",
        preferred_terms={AU_LANGUAGE_TAG: "Urine examination"},
    ),
)


def _seeded_stub() -> StubTerminologyClient:
    return StubTerminologyClient(concepts=_SEEDED)


@pytest.mark.req("FR-53")
def test_select_synonyms_on_the_live_server_shape_keeps_only_plain_synonyms() -> None:
    assert select_synonyms(_ANAEMIA) == ("Absolute anaemia", "Absolute anemia", "Anemia")


@pytest.mark.req("FR-53")
def test_select_synonyms_on_the_stub_shape_drops_refset_preferred_terms() -> None:
    stub = _seeded_stub()
    result = stub.lookup("26604007", edition=SNOMED_CT_AU, display_language=AU_LANGUAGE_TAG)

    assert select_synonyms(result) == ("FBC - Full blood count", "FBE")


@pytest.mark.req("FR-53")
def test_select_synonyms_collapses_a_repeated_value_in_server_order() -> None:
    result = LookupResult(
        code="1",
        system=SNOMED_SYSTEM,
        display="P",
        designations=(_synonym("B"), _synonym("A"), _synonym("B"), _synonym("P")),
    )

    assert select_synonyms(result) == ("B", "A")


@pytest.mark.req("FR-53")
def test_a_concept_with_only_a_preferred_term_is_available_with_no_terms() -> None:
    source = SnomedSynonymSource(_seeded_stub())

    assert source.synonyms_for("167217005", preferred_term="Urine examination") == (
        SnomedSynonymResult(SynonymStatus.AVAILABLE, ())
    )


@pytest.mark.req("FR-53")
def test_the_stored_preferred_term_is_dropped_even_when_the_server_serves_another() -> None:
    stub = StubTerminologyClient()
    stub.seed_lookup(
        "271737000",
        LookupResult(
            code="271737000",
            system=SNOMED_SYSTEM,
            display="Anaemia",
            designations=(_synonym("Absolute anaemia"), _synonym("Anaemia disorder")),
        ),
    )
    source = SnomedSynonymSource(stub)

    result = source.synonyms_for("271737000", preferred_term="Anaemia disorder")

    assert result.terms == ("Absolute anaemia",)


@pytest.mark.req("FR-54")
@pytest.mark.parametrize(
    "error",
    [
        TerminologyTimeoutError("timed out"),
        TerminologyStatusError("unavailable", status_code=503),
        TerminologyTransportError("connection refused"),
        TerminologyStatusError("not found", status_code=404),
        StubNotSeededError("nothing seeded"),
    ],
    ids=["timeout", "503", "transport", "absent", "unseeded"],
)
def test_any_terminology_error_is_unavailable_not_an_exception(error: Exception) -> None:
    stub = StubTerminologyClient()
    stub.seed_error(Operation.LOOKUP, error, key="26604007")  # type: ignore[arg-type]
    source = SnomedSynonymSource(stub)

    result = source.synonyms_for("26604007", preferred_term="Full blood count")

    assert result == SnomedSynonymResult(SynonymStatus.UNAVAILABLE, ())


@pytest.mark.req("FR-54")
def test_a_config_error_is_raised_not_reported_as_unavailable() -> None:
    stub = StubTerminologyClient()
    stub.seed_error(Operation.LOOKUP, TerminologyConfigError("bad config"))
    source = SnomedSynonymSource(stub)

    with pytest.raises(TerminologyConfigError):
        source.synonyms_for("26604007", preferred_term=None)


@pytest.mark.req("FR-54")
def test_a_result_is_cached_until_its_lifetime_ends() -> None:
    client = _SwitchableClient()
    clock = _Clock()
    source = SnomedSynonymSource(client, clock=clock)

    first = source.synonyms_for("26604007", preferred_term="Full blood count")
    clock.now += SYNONYM_TTL_SECONDS - 1
    second = source.synonyms_for("26604007", preferred_term="Full blood count")
    assert client.lookups == 1
    assert first == second

    clock.now += 1
    source.synonyms_for("26604007", preferred_term="Full blood count")
    assert client.lookups == 2


@pytest.mark.req("FR-54")
def test_a_failure_is_cached_briefly_then_retried() -> None:
    client = _SwitchableClient(down=True)
    clock = _Clock()
    source = SnomedSynonymSource(client, clock=clock)

    assert source.synonyms_for("26604007", preferred_term=None).status is SynonymStatus.UNAVAILABLE
    clock.now += FAILURE_TTL_SECONDS - 1
    assert source.synonyms_for("26604007", preferred_term=None).status is SynonymStatus.UNAVAILABLE
    assert client.lookups == 1

    client.down = False
    clock.now += 1
    recovered = source.synonyms_for("26604007", preferred_term="Full blood count")
    assert recovered == SnomedSynonymResult(
        SynonymStatus.AVAILABLE, ("FBC - Full blood count", "FBE")
    )
    assert client.lookups == 2


@pytest.mark.req("FR-54")
def test_the_cache_evicts_the_oldest_code_past_its_bound() -> None:
    client = _SwitchableClient()
    source = SnomedSynonymSource(client, max_entries=1)

    source.synonyms_for("26604007", preferred_term=None)
    source.synonyms_for("167217005", preferred_term=None)
    source.synonyms_for("167217005", preferred_term=None)
    assert client.lookups == 2

    source.synonyms_for("26604007", preferred_term=None)
    assert client.lookups == 3


@pytest.mark.req("FR-53")
def test_the_lookup_asks_the_au_edition_for_the_au_preferred_term() -> None:
    seen: list[tuple[Edition, str | None]] = []

    class _Recording(StubTerminologyClient):
        def lookup(
            self,
            code: str,
            *,
            edition: Edition,
            properties: tuple[str, ...] = (),
            display_language: str | None = None,
        ) -> LookupResult:
            seen.append((edition, display_language))
            return super().lookup(code, edition=edition, display_language=display_language)

    SnomedSynonymSource(_Recording(concepts=_SEEDED)).synonyms_for("26604007", preferred_term=None)

    assert seen == [(SNOMED_CT_AU, AU_LANGUAGE_TAG)]


@pytest.mark.req("FR-54")
def test_interactive_config_makes_one_short_attempt() -> None:
    config = interactive_config(TerminologyConfig(base_url="https://tx.example/fhir"))

    assert config.max_retries == 0
    assert config.timeout_seconds == INTERACTIVE_TIMEOUT_SECONDS
    assert config.base_url == "https://tx.example/fhir/"


@pytest.mark.req("FR-54")
def test_interactive_config_keeps_a_shorter_configured_timeout() -> None:
    config = interactive_config(TerminologyConfig(timeout_seconds=1.0))

    assert config.timeout_seconds == 1.0
