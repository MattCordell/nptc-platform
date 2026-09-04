#!/usr/bin/env python3
"""Registry-timeout retry guard for `pnpm audit` (issue #255, PR #258 review).

`pnpm audit`'s POST to registry.npmjs.org's advisories/bulk endpoint has
intermittently timed out, failing `security.yml`'s `frontend-audit` job -
and with it the `Required (Security)` aggregator - on PRs that touch no
dependency manifest at all. Retrying blindly on any nonzero exit would also
delay a real high/critical advisory behind a multi-minute retry chain before
it ever surfaces, so this only retries a recognised network/timeout
signature (see NETWORK_SIGNATURE) and fails immediately, on the first
attempt, for anything else - a real advisory, or a deterministic HTTP-status
failure like pnpm's own ERR_PNPM_FETCH_401/403/404 (bad auth, not found -
correctly excluded even though it shares the ERR_PNPM_FETCH prefix with
nothing here, since it's a real response, not a dead connection, and won't
be fixed by retrying).

Streams the wrapped command's combined stdout/stderr live, line by line, so
a run sitting in retry backoff still shows progress in the CI log rather
than going silent until the process exits.

Started life as an inline `security.yml` bash loop; extracted here (as
codeql_gate.py and doc_impact_gate.py were before it, for the same reason)
so its classification logic - the part with a real failure mode, and the
part that has already been wrong once in this PR's own history - has
committed regression coverage under scripts/tests/.

Usage:
  python3 scripts/audit_retry_guard.py -- pnpm audit --prod --audit-level=high
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TextIO


class _Process(Protocol):
    """The slice of `subprocess.Popen` this module actually uses - narrow
    enough that a test's stand-in doesn't need to be a real `Popen`. `stdout`
    is typed loosely (just "iterable of lines") rather than as `IO[str]`,
    since `Popen`'s own stub types it as `IO[Any] | None` depending on how
    it's constructed, and a test double has no reason to be a real file
    object to satisfy this module's actual use of it."""

    stdout: Any
    returncode: int | None

    def wait(self) -> int: ...


NETWORK_SIGNATURE = re.compile(
    r"TimeoutError|operation was aborted|ETIMEDOUT|ECONNRESET|ECONNREFUSED|ENOTFOUND|EAI_AGAIN"
)

DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_SLEEP_SECONDS = 30.0


def is_registry_timeout(output: str) -> bool:
    return NETWORK_SIGNATURE.search(output) is not None


def _default_popen(command: Sequence[str]) -> _Process:
    return subprocess.Popen(
        list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def run_with_retries(
    command: Sequence[str],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    popen: Callable[..., _Process] = _default_popen,
    sleep: Callable[[float], None] = time.sleep,
    out: TextIO = sys.stdout,
) -> int:
    """Run `command`, retrying only a registry-timeout failure.

    Output is streamed to `out` as it's produced and also accumulated, since
    classification needs the full text but the CI log shouldn't go dark for
    the several minutes a stalled request can take to give up.
    """
    attempt = 1
    while True:
        process = popen(command)
        lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            out.write(line)
            lines.append(line)
        returncode = process.wait()
        output = "".join(lines)

        if returncode == 0:
            return 0
        if not is_registry_timeout(output):
            return returncode
        if attempt >= max_attempts:
            print(
                f"::error::pnpm audit failed after {attempt} attempts with a "
                "registry-timeout signature - registry.npmjs.org appears "
                "unreachable (see issue #255)",
                file=out,
            )
            return returncode
        print(
            f"pnpm audit hit a registry timeout (attempt {attempt}/{max_attempts}) "
            f"- retrying in {sleep_seconds}s",
            file=out,
        )
        attempt += 1
        sleep(sleep_seconds)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("usage: audit_retry_guard.py -- <command...>", file=sys.stderr)
        return 2
    return run_with_retries(argv)


if __name__ == "__main__":
    sys.exit(main())
