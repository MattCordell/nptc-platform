import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `RequireAuth` gates every authenticated and admin route behind
 * `useAuthStatus`. Today that always returns "unavailable" (issue #41 lands
 * the real OIDC session), so these routes render a "sign-in is not yet
 * available" notice *at the URL that was requested* - not a redirect. That
 * is the contract that lets #41 swap in real sign-in with no route-table
 * change: only `require-auth.tsx`'s body changes, and exactly these
 * assertions would need to change with it.
 */
describe("RequireAuth - before sign-in exists (#41 not yet landed)", () => {
  it.each(["/submissions", "/admin/users", "/interest", "/admin"])(
    "renders the sign-in-unavailable notice at %s, inside the shell, with no redirect",
    async (path) => {
      const { renderRoute } = await import("../test/render-route.tsx");
      const { router } = await renderRoute(path);

      expect(
        await screen.findByRole("heading", { name: /sign-in is not yet available/i }),
      ).toBeInTheDocument();
      expect(router.state.location.pathname).toBe(path);
      expect(router.history.length).toBe(1);
      // The shell is still present - this is a screen, not a dead end.
      expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
      expect(
        within(screen.getByRole("main")).getByRole("link", {
          name: /search the catalogue/i,
        }),
      ).toBeInTheDocument();
    },
  );
});

describe("RequireAuth - once signed in", () => {
  beforeEach(() => {
    // The previous describe block already imported the production module
    // graph, so it is cached by now - reset before `vi.doMock` so the next
    // dynamic import re-evaluates it against the mock.
    vi.resetModules();
  });

  afterEach(() => {
    vi.doUnmock("../auth/auth-status.ts");
    vi.resetModules();
  });

  it("renders the authenticated route's own screen instead of the notice", async () => {
    vi.doMock("../auth/auth-status.ts", () => ({
      useAuthStatus: () => "signed-in",
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/submissions");

    expect(
      await screen.findByRole("heading", { name: /^submissions$/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /sign-in is not yet available/i }),
    ).not.toBeInTheDocument();
  });
});
