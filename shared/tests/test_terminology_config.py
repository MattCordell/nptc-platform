"""Tests for TerminologyConfig (FR-53 configuration; NFR-26/NFR-35 secret hygiene)."""

from __future__ import annotations

import pytest

from nptc_shared.terminology.config import DEFAULT_BASE_URL, TerminologyConfig
from nptc_shared.terminology.errors import TerminologyConfigError


def test_defaults_are_anonymous_against_the_reference_ontoserver() -> None:
    config = TerminologyConfig()
    assert config.base_url == DEFAULT_BASE_URL
    assert config.bearer_token is None
    assert config.timeout_seconds == 30.0
    assert config.max_retries == 3


def test_base_url_without_a_trailing_slash_is_normalised() -> None:
    config = TerminologyConfig(base_url="https://tx.example.test/fhir")
    assert config.base_url == "https://tx.example.test/fhir/"


def test_base_url_with_a_trailing_slash_is_left_alone() -> None:
    config = TerminologyConfig(base_url="https://tx.example.test/fhir/")
    assert config.base_url == "https://tx.example.test/fhir/"


def test_from_env_reads_every_variable_from_a_supplied_mapping() -> None:
    config = TerminologyConfig.from_env(
        {
            "NPTC_TX_BASE_URL": "https://local.test/fhir",
            "NPTC_TX_TOKEN": "s3cr3t",
            "NPTC_TX_TIMEOUT_SECONDS": "12.5",
            "NPTC_TX_MAX_RETRIES": "5",
        }
    )
    assert config.base_url == "https://local.test/fhir/"
    assert config.bearer_token == "s3cr3t"
    assert config.timeout_seconds == 12.5
    assert config.max_retries == 5


def test_from_env_with_an_empty_mapping_is_anonymous_and_uses_defaults() -> None:
    config = TerminologyConfig.from_env({})
    assert config.base_url == DEFAULT_BASE_URL
    assert config.bearer_token is None


def test_from_env_treats_an_empty_token_as_unset() -> None:
    """An empty NPTC_TX_TOKEN must mean anonymous, not an empty-string
    bearer token - a .env file can carry the key with no value without
    accidentally sending `Authorization: Bearer `."""
    config = TerminologyConfig.from_env({"NPTC_TX_TOKEN": ""})
    assert config.bearer_token is None


def test_from_env_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_TX_BASE_URL", "https://from-environ.test/fhir")
    monkeypatch.delenv("NPTC_TX_TOKEN", raising=False)
    config = TerminologyConfig.from_env()
    assert config.base_url == "https://from-environ.test/fhir/"


def test_from_env_rejects_a_malformed_timeout_rather_than_falling_back() -> None:
    with pytest.raises(TerminologyConfigError, match="NPTC_TX_TIMEOUT_SECONDS"):
        TerminologyConfig.from_env({"NPTC_TX_TIMEOUT_SECONDS": "not-a-number"})


def test_from_env_rejects_a_malformed_max_retries_rather_than_falling_back() -> None:
    with pytest.raises(TerminologyConfigError, match="NPTC_TX_MAX_RETRIES"):
        TerminologyConfig.from_env({"NPTC_TX_MAX_RETRIES": "three"})


@pytest.mark.req("FR-52")
def test_from_env_reads_the_sweep_tuning_variables() -> None:
    config = TerminologyConfig.from_env(
        {"NPTC_TX_CHUNK_SIZE": "500", "NPTC_TX_MAX_CONCURRENCY": "8"}
    )
    assert config.chunk_size == 500
    assert config.max_concurrency == 8


@pytest.mark.req("FR-52")
def test_sweep_tuning_defaults_sit_inside_the_range_fr52_specifies() -> None:
    config = TerminologyConfig()
    assert 200 <= config.chunk_size <= 500
    assert config.max_concurrency >= 1


@pytest.mark.req("FR-52")
def test_from_env_rejects_a_malformed_chunk_size_rather_than_falling_back() -> None:
    with pytest.raises(TerminologyConfigError, match="NPTC_TX_CHUNK_SIZE"):
        TerminologyConfig.from_env({"NPTC_TX_CHUNK_SIZE": "lots"})


@pytest.mark.req("FR-52")
def test_a_chunk_size_of_zero_is_refused_not_silently_treated_as_the_default() -> None:
    """A zero chunk makes no progress and a negative one slices to nothing -
    either would let a sweep report a catalogue it never checked as clean."""
    with pytest.raises(TerminologyConfigError, match="chunk_size"):
        TerminologyConfig.from_env({"NPTC_TX_CHUNK_SIZE": "0"})
    with pytest.raises(TerminologyConfigError, match="chunk_size"):
        TerminologyConfig(chunk_size=-1)


@pytest.mark.req("FR-52")
def test_a_max_concurrency_below_one_is_refused() -> None:
    with pytest.raises(TerminologyConfigError, match="max_concurrency"):
        TerminologyConfig.from_env({"NPTC_TX_MAX_CONCURRENCY": "0"})


def test_bearer_token_never_appears_in_repr() -> None:
    config = TerminologyConfig(bearer_token="s3cr3t")
    assert "s3cr3t" not in repr(config)
