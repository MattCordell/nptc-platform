import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { StrictMode } from "react";

import { createAppRouter } from "../router/router.tsx";

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
 */
export async function renderRoute(initialEntry: string) {
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  const result = render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
  await router.load(); // settle the initial match before assertions run
  return { ...result, router };
}
