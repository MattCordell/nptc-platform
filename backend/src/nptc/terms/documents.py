"""Loading the per-version terms files (NFR-47, ADR-0043).

One Markdown file per version under `versions/`, named `<version>.md`. A published file is
never edited: a new version is a new file, so the exact text a user accepted can be served
from the deployed service long after it stopped being current. The version is a zero-padded
date, which sorts and matches the file's effective date, but the platform only ever compares
versions for equality.

Each file opens with a front-matter block of `key: value` lines between two `---` fences,
carrying `version` (which must equal the file name) and `effective` (an ISO date). The rest
is the Markdown text, served verbatim.
"""

from __future__ import annotations

import re
from datetime import date
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final

from pydantic import BaseModel, ConfigDict

from nptc.terms.errors import TermsFileMalformedError, TermsVersionNotFoundError

#: The default `NPTC_TERMS_CURRENT_VERSION`, the version of the first (temporary) terms file.
DEFAULT_TERMS_VERSION: Final = "2026-10-06"

_VERSION_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_FENCE: Final = "---"


class TermsDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    effective_date: date
    text: str


def _parse(version: str, raw: str) -> TermsDocument:
    lines = raw.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != _FENCE:
        raise TermsFileMalformedError(f"terms file {version}.md has no front matter")
    try:
        closing = lines.index(_FENCE, 1)
    except ValueError:
        raise TermsFileMalformedError(
            f"terms file {version}.md has an unterminated front matter block"
        ) from None

    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()

    if fields.get("version") != version:
        raise TermsFileMalformedError(
            f"terms file {version}.md declares version {fields.get('version')!r}"
        )
    try:
        effective = date.fromisoformat(fields.get("effective", ""))
    except ValueError:
        raise TermsFileMalformedError(
            f"terms file {version}.md has no valid ISO `effective` date"
        ) from None

    return TermsDocument(
        version=version,
        effective_date=effective,
        text="\n".join(lines[closing + 1 :]).strip() + "\n",
    )


def _versions_dir() -> Traversable:
    return resources.files("nptc.terms").joinpath("versions")


@cache
def load_terms_document(version: str) -> TermsDocument:
    """The terms document for `version`.

    Cached because a published file never changes. Raises `TermsVersionNotFoundError` for a
    malformed version or one with no file, and `TermsFileMalformedError` for a bad file.
    """
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise TermsVersionNotFoundError(f"{version!r} is not a terms version")
    resource = _versions_dir().joinpath(f"{version}.md")
    if not resource.is_file():
        raise TermsVersionNotFoundError(f"no terms file for version {version!r}")
    return _parse(version, resource.read_text(encoding="utf-8"))
