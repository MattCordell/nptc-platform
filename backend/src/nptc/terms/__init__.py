"""Versioned terms of use and the record of who accepted which version (NFR-45, NFR-47,
ADR-0043).

`nptc.terms.documents` loads the immutable per-version Markdown files, `nptc.terms.acceptance`
appends and reads the acceptance rows, and `nptc.terms.gate` is the request-time check that
refuses a contribution from a user who has not accepted the current version. The files sit
inside this package, not under `docs/`, because the runtime image installs the package without
a repository checkout.
"""
