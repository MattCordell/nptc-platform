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
  /**
   * Base URL for the backend API (issue #147). Defaults to same origin
   * (`window.location.origin`), since Caddy fronts both frontend and
   * backend in every deployed environment (ADR-0001). Only needed to point
   * at a different origin (e.g. local dev without the Caddy proxy).
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
