import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { StrictMode } from "react";

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
  const result = render(
    <StrictMode>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} />
      </AuthContext.Provider>
    </StrictMode>,
  );
  await router.load(); // settle the initial match before assertions run
  return { ...result, router, auth };
}
