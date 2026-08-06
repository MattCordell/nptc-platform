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


@dataclass(frozen=True)
class Finding:
    """A single defect finding.

    ``code``, ``location`` (a cell reference or similar) and ``message``,
    plus ``band`` (FR-71), derived from ``code`` alone via ``band_for`` - a
    property, not a field, so every ``Finding`` that exists is classified by
    construction and no caller can create one that isn't. Grouped rendering
    by band (P0-8) is owned elsewhere.
    """

    code: str
    location: str
    message: str

    @property
    def band(self) -> Band:
        return band_for(self.code)

    def sort_key(self) -> tuple[str, str, str]:
        return (self.location, self.code, self.message)
