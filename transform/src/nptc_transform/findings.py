"""The ``Finding`` type, split out from ``pipeline.py``.

Both ``cell_defects.py`` (which produces findings) and ``pipeline.py`` (which
collects and reports them) need this type. Keeping it in its own module - not
in whichever of the two seemed more natural - means neither has to import the
other just to get it, so the reader (P0-2) and the pipeline never become
circularly dependent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """A single defect finding.

    Minimal today: ``code``, ``location`` (a cell reference or similar) and
    ``message``. The defect band (P0-3) and grouped rendering (P0-8) are owned
    elsewhere; this type exists here only because determinism needs a defined
    ordering to be testable.
    """

    code: str
    location: str
    message: str

    def sort_key(self) -> tuple[str, str, str]:
        return (self.location, self.code, self.message)
