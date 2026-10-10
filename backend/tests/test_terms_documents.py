"""The per-version terms files and the setting that names the current one (NFR-47, ADR-0043).

No database: the loader reads packaged files, and start-up validation builds the app without
serving a request.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from importlib import resources
from pathlib import Path

import pytest

from nptc.api.app import create_app
from nptc.terms import documents
from nptc.terms.documents import DEFAULT_TERMS_VERSION, TermsDocument, load_terms_document
from nptc.terms.errors import TermsFileMalformedError, TermsVersionNotFoundError

_spec = importlib.util.spec_from_file_location(
    "hermetic_settings_support", Path(__file__).parent / "hermetic_settings_support.py"
)
assert _spec is not None and _spec.loader is not None
_hermetic = importlib.util.module_from_spec(_spec)
sys.modules["hermetic_settings_support"] = _hermetic
_spec.loader.exec_module(_hermetic)
hermetic_api_settings = _hermetic.hermetic_api_settings


@pytest.mark.req("NFR-47")
def test_the_default_version_has_a_terms_file_marked_temporary() -> None:
    document = load_terms_document(DEFAULT_TERMS_VERSION)

    assert document.version == DEFAULT_TERMS_VERSION
    assert document.effective_date == date.fromisoformat(DEFAULT_TERMS_VERSION)
    assert "Temporary text" in document.text


@pytest.mark.req("NFR-46")
def test_the_default_version_grants_the_contribution_licence_and_the_first_one_does_not() -> None:
    licence = ("perpetual", "irrevocable", "worldwide", "royalty-free", "non-exclusive")
    text = load_terms_document(DEFAULT_TERMS_VERSION).text

    assert all(term in text for term in licence)
    assert "keep ownership" in text
    assert "licence" not in load_terms_document("2026-10-06").text


@pytest.mark.req("NFR-47")
def test_the_text_is_served_without_the_front_matter() -> None:
    document = load_terms_document(DEFAULT_TERMS_VERSION)

    assert not document.text.startswith("---")
    assert "effective:" not in document.text


@pytest.mark.req("NFR-47")
def test_every_packaged_file_loads_and_is_named_for_its_own_version() -> None:
    files = [
        entry
        for entry in resources.files("nptc.terms").joinpath("versions").iterdir()
        if entry.name.endswith(".md")
    ]

    assert files
    for entry in files:
        version = entry.name.removesuffix(".md")
        assert load_terms_document(version).version == version


@pytest.mark.req("NFR-47")
@pytest.mark.parametrize(
    "version",
    ["2099-01-01", "", "latest", "2026-10-6", "../versions/2026-10-06", "2026-10-06.md", "x" * 500],
)
def test_an_unknown_or_malformed_version_is_not_found(version: str) -> None:
    with pytest.raises(TermsVersionNotFoundError):
        load_terms_document(version)


def _parse(version: str, raw: str) -> TermsDocument:
    return documents._parse(version, raw)


@pytest.mark.parametrize(
    "raw",
    [
        "no front matter\n",
        "---\nversion: 2026-10-06\neffective: 2026-10-06\nnever closed\n",
        "---\nversion: 2026-11-01\neffective: 2026-10-06\n---\ntext\n",
        "---\nversion: 2026-10-06\n---\ntext\n",
        "---\nversion: 2026-10-06\neffective: not-a-date\n---\ntext\n",
    ],
    ids=["no-fence", "unterminated", "version-mismatch", "no-effective", "bad-effective"],
)
def test_a_malformed_file_is_refused(raw: str) -> None:
    with pytest.raises(TermsFileMalformedError):
        _parse("2026-10-06", raw)


@pytest.mark.parametrize("fence", ["---", "--- ", "---\t", " ---"])
def test_either_fence_may_carry_stray_whitespace(fence: str) -> None:
    raw = f"{fence}\nversion: 2026-10-06\neffective: 2026-10-06\n{fence}\n\nBody\n"

    assert _parse("2026-10-06", raw).text == "Body\n"


def test_windows_line_endings_parse_the_same_as_unix() -> None:
    raw = "---\nversion: 2026-10-06\neffective: 2026-10-06\n---\n\n# Title\n\nBody\n"

    assert _parse("2026-10-06", raw.replace("\n", "\r\n")) == _parse("2026-10-06", raw)


@pytest.mark.req("NFR-47")
def test_start_up_refuses_a_current_version_with_no_file() -> None:
    settings = hermetic_api_settings(terms_current_version="2099-01-01")

    with pytest.raises(TermsVersionNotFoundError):
        create_app(settings=settings)


@pytest.mark.req("NFR-47")
def test_start_up_succeeds_with_the_default_version() -> None:
    create_app(settings=hermetic_api_settings())
