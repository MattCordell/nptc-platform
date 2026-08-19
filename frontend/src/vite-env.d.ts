/// <reference types="vite/client" />

/**
 * The build-time configuration the sign-in flow needs (issue #41).
 *
 * Both are safe to ship in a bundle: ADR-0021's `nptc-frontend` is a public
 * client, so an issuer URL and a client id are public facts, not secrets.
 * No `VITE_*` variable may ever carry a credential - `pnpm build` is
 * followed by `scripts/assert-no-secret-in-bundle.mjs`, which checks that
 * against the built assets rather than trusting review (NFR-01).
 */
interface ImportMetaEnv {
  readonly VITE_OIDC_ISSUER?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
