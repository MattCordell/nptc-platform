"""Runs the transform CLI against the stub terminology server, in a process of its own.

For a test that needs a fresh interpreter (``PYTHONHASHSEED``) and still must not open a socket
(NFR-37). ``STUBBED_CLI_PROCEDURES`` is a JSON list of ``[code, fsn]`` pairs the server serves as
procedures. Every code in the specimen map is served as an active specimen.
"""

from __future__ import annotations

import json
import os

from nptc_shared.terminology.models import (
    AU_LANGUAGE_TAG,
    PROCEDURE_ROOT_CODE,
    SPECIMEN_ROOT_CODE,
)
from nptc_shared.terminology.stub import StubConcept, StubTerminologyClient
from nptc_transform import cli
from nptc_transform.specimen_map import SPECIMEN_MAP

_AU_VERSION = "http://snomed.info/sct/32506021000036107/version/20260531"


class _Server(StubTerminologyClient):
    def __enter__(self) -> _Server:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


def _server() -> _Server:
    procedures = [
        StubConcept(code=code, fsn=fsn, parents=(PROCEDURE_ROOT_CODE,))
        for code, fsn in json.loads(os.environ.get("STUBBED_CLI_PROCEDURES", "[]"))
    ]
    specimens = [
        StubConcept(
            code=code,
            fsn=f"Fixture specimen {code} (specimen)",
            preferred_terms={AU_LANGUAGE_TAG: f"Fixture specimen {code}"},
            parents=() if code == SPECIMEN_ROOT_CODE else (SPECIMEN_ROOT_CODE,),
        )
        for code in SPECIMEN_MAP.codes
    ]
    return _Server(concepts=[*procedures, *specimens], resolved_version={"au": _AU_VERSION})


if __name__ == "__main__":
    cli.OntoserverClient = lambda _config: _server()  # type: ignore[assignment,misc]
    cli.app()
