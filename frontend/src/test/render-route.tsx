import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { act, render } from "@testing-library/react";
import { StrictMode } from "react";

import { createQueryClient } from "../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { createAppRouter } from "../router/router.tsx";

/**
 * The auth context every route renders under, unless a test says otherwise.
 *
 * `"unavailable"` is the default deliberately. It is the one status with no
 * side effects: `"signed-out"` makes `/sign-in` and `RequireAuth` start a
 * redirect, which is exactly what their own tests should opt into
 * explicitly rather than something every unrelated route test triggers by
 * accident.
 */
const DEFAULT_AUTH: AuthContextValue = {
  status: "unavailable",
  getAccessToken: () => Promise.resolve(null),
  signIn: () => Promise.resolve(),
  signOut: () => Promise.resolve(),
  register: () => Promise.resolve(),
  restore: () => Promise.resolve(),
  completeCallback: () => Promise.resolve(null),
};

export interface RenderRouteOptions {
  /** Overrides merged onto `DEFAULT_AUTH` for this render. */
  auth?: Partial<AuthContextValue>;
}

/**
 * Mounts the real application router at `initialEntry` over a fresh
 * `createMemoryHistory` - a cold browser session, exactly like deep-linking
 * into a brand-new tab: no prior navigation, nothing cached from an earlier
 * route.
 *
 * Deliberately uses the production `createAppRouter` (including its custom
 * search serialisation) under `<StrictMode>`, matching `main.tsx` exactly -
 * not a throwaway test route tree or a laxer render, either of which would
 * prove nothing about the shipped app. `StrictMode`'s double-invoked mount
 * effects are exactly what caught `root-layout.tsx`'s focus bug: a helper
 * that skipped it would have let that regress silently again.
 *
 * The auth context is supplied directly rather than by mounting the real
 * `AuthProvider`: the provider's job is to talk to Keycloak, and a route
 * test should not need a network stub to render a page. `AuthProvider`
 * itself is tested in `src/auth/auth-context.test.tsx`.
 */
export async function renderRoute(
  initialEntry: string,
  options: RenderRouteOptions = {},
) {
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  const auth: AuthContextValue = { ...DEFAULT_AUTH, ...options.auth };
  // A fresh QueryClient per render, matching main.tsx's provider tree but
  // never shared across renders: a shared client would leak cached query
  // state between tests, and its `retry: false` default keeps a route that
  // hits a stubbed-failure query from retrying under StrictMode's
  // double-mount.
  const queryClient = createQueryClient();
  const result = render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={auth}>
          <RouterProvider router={router} />
        </AuthContext.Provider>
      </QueryClientProvider>
    </StrictMode>,
  );
  // `router.load()` resolves the initial match asynchronously, then pushes
  // it into React's store. In the act environment React defers that update
  // to the act queue instead of its normal scheduler, so without this
  // wrapper `renderRoute` can return before the not-found/error/real page
  // has actually committed - a race a caller's `findByRole` sometimes wins
  // (fast machine, empty queue) and sometimes loses (loaded machine,
  // `pnpm test:coverage` competing with 27 other files), timing out on a
  // component that was never actually mismatched, just not yet on screen
  // (#215). Wrapping in `act` flushes that deferred update before this
  // helper resolves, so callers always assert against a committed DOM.
  await act(async () => {
    await router.load();
  });
  return { ...result, router, auth };
}
