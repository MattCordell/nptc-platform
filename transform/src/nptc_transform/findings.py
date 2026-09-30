"""The ``Finding`` type, split out from ``pipeline.py``.

Both ``cell_defects.py`` (which produces findings) and ``pipeline.py`` (which
collects and reports them) need this type. A module of its own means neither
imports the other to get it, so the reader and the pipeline never become
circularly dependent. It imports ``bands``, not the reverse, so that direction
stays acyclic too.
"""

from __future__ import annotations

from dataclasses import dataclass

from nptc_transform.bands import Band, band_for
from nptc_transform.cellref import CellRef


@dataclass(frozen=True)
class Finding:
    """A single defect finding.

    ``code``, ``location`` (a structured ``CellRef``) and ``message``, plus
    ``band`` (FR-71), derived from ``code`` alone via ``band_for``. ``band`` is a
    property, not a field, so every ``Finding`` is classified by construction.
    ``report_writer.py`` owns grouped rendering by band and defect class (FR-72).

    A ``Finding`` built with a plain ``str`` for ``location`` fails at
    ``RunResult.__post_init__`` (``Finding.sort_key`` calling ``str.sort_key()``)
    with an ``AttributeError``, not at construction. There is no runtime
    ``isinstance`` guard on purpose: mypy covers every in-repo production call
    site, and this repo's style is "true by construction", not re-checking what
    the type system guarantees.
    """

    code: str
    location: CellRef
    message: str

    @property
    def band(self) -> Band:
        return band_for(self.code)

    def sort_key(self) -> tuple[tuple[str, int, str, int], str, str]:
        return (self.location.sort_key(), self.code, self.message)
