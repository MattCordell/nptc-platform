"""The ``Finding`` type, split out from ``pipeline.py``.

Both ``cell_defects.py`` (which produces findings) and ``pipeline.py`` (which
collects and reports them) need this type. Keeping it in its own module - not
in whichever of the two seemed more natural - means neither has to import the
other just to get it, so the reader (P0-2) and the pipeline never become
circularly dependent. It imports ``bands`` (not the reverse), so that
dependency direction stays acyclic too.
"""

from __future__ import annotations

from dataclasses import dataclass

from nptc_transform.bands import Band, band_for
from nptc_transform.cellref import CellRef


@dataclass(frozen=True)
class Finding:
    """A single defect finding.

    ``code``, ``location`` (a structured ``CellRef``) and ``message``, plus
    ``band`` (FR-71), derived from ``code`` alone via ``band_for`` - a
    property, not a field, so every ``Finding`` that exists is classified by
    construction and no caller can create one that isn't. Grouped rendering
    by band and defect class (FR-72) is owned by ``report_writer.py``.

    A ``Finding`` built with a plain ``str`` for ``location`` fails at
    ``RunResult.__post_init__`` (``Finding.sort_key`` calling
    ``str.sort_key()``) with an ``AttributeError``, not at construction. No
    runtime ``isinstance`` guard is added here on purpose: mypy already
    covers every in-repo production call site, and this repo's style is
    "true by construction", not defensive re-checking of what the type
    system already guarantees.
    """

    code: str
    location: CellRef
    message: str

    @property
    def band(self) -> Band:
        return band_for(self.code)

    def sort_key(self) -> tuple[tuple[str, int, str, int], str, str]:
        return (self.location.sort_key(), self.code, self.message)
