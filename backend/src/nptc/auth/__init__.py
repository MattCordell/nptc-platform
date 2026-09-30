"""OIDC token verification and the permission framework (NFR-07, FR-44, NFR-20,
FR-01, FR-80, FR-81, NFR-06). `nptc.auth.permissions` holds the PRD Section 4.7
matrix as code, `nptc.auth.principal` the resolved-actor type,
`nptc.auth.authorisation` the check API and `nptc.auth.grants` role granting and
revoking. `nptc.auth.identity` maps a verified identity to an `app_user`."""
