import { QueryClient } from "@tanstack/react-query";

/**
 * The shared TanStack Query defaults (issue #147).
 *
 * A factory rather than a module-level singleton, so `main.tsx` and
 * `src/test/render-route.tsx` cannot drift apart, and so every test gets its
 * own cache - a shared client would leak state between tests, and StrictMode
 * double-mounts every route it renders.
 *
 * `retry: false` for the same reason: this app's principal failure mode is
 * "the caller isn't authorised" or "the resource doesn't exist" (NFR-08's
 * audited 4xx paths), not a flaky network - retrying those just delays the
 * error the UI needs to show, and makes tests slower and more prone to
 * timing flakiness under StrictMode.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}
