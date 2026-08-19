import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";

import { createAppRouter } from "../router/router.tsx";

/**
 * Mounts the real application router at `initialEntry` over a fresh
 * `createMemoryHistory` - a cold browser session, exactly like deep-linking
 * into a brand-new tab: no prior navigation, nothing cached from an earlier
 * route.
 *
 * Deliberately uses the production `createAppRouter` (including its custom
 * search serialisation), not a throwaway test route tree - a helper that
 * built its own routes would prove nothing about the shipped app.
 */
export async function renderRoute(initialEntry: string) {
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  const result = render(<RouterProvider router={router} />);
  await router.load(); // settle the initial match before assertions run
  return { ...result, router };
}
