import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderRoute } from "../test/render-route.tsx";

/**
 * `RequireAuth` gates every authenticated and admin route on the real OIDC
 * session (issue #41). This file replaced the placeholder-era contract it
 * asserted before #41 landed: those tests asserted "renders a not-yet-
 * available notice at the requested URL, with no redirect", and said in as
 * many words that exactly these assertions would change when real sign-in
 * arrived.
 *
 * What has *not* changed, and is re-asserted below, is the route table:
 * every path still resolves to the same route it always did. Only what
 * `RequireAuth` renders for a given session status is different.
 *
 * None of this is access control (NFR-20). The API is the boundary; these
 * tests are about what the shell shows.
 */

const GATED_PATHS = ["/submissions", "/admin/users", "/interest", "/admin"];

describe("RequireAuth - signed out", () => {
  it.each(GATED_PATHS)("sends a signed-out visitor from %s to sign-in", async (path) => {
    const { router } = await renderRoute(path, { auth: { status: "signed-out" } });

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/sign-in");
    });
    // The whole point of the redirect: the user comes back to where they
    // were aiming, not to the home page.
    expect(router.state.location.search).toEqual({ redirect: path });
  });

  it("replaces rather than pushes, so 'back' does not bounce the user", async () => {
    const { router } = await renderRoute("/submissions", {
      auth: { status: "signed-out" },
    });

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/sign-in");
    });
    // A push would leave /submissions in history, and going back would
    // land on it and immediately redirect here again - a trap.
    expect(router.history.length).toBe(1);
  });
});

describe("RequireAuth - signed in", () => {
  it.each(GATED_PATHS)("renders %s itself, with no redirect", async (path) => {
    const { router } = await renderRoute(path, { auth: { status: "signed-in" } });

    expect(router.state.location.pathname).toBe(path);
    expect(router.history.length).toBe(1);
    expect(
      screen.queryByRole("heading", { name: /taking you to sign in/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the authenticated route's own screen", async () => {
    await renderRoute("/submissions", { auth: { status: "signed-in" } });

    expect(
      await screen.findByRole("heading", { name: /^submissions$/i }),
    ).toBeInTheDocument();
  });
});

describe("RequireAuth - sign-in unavailable", () => {
  it("says so at the requested URL instead of redirecting into a loop", async () => {
    // The failure mode this guards against: with the identity provider
    // unreachable, "signed out" and "redirect to sign-in" would bounce the
    // user between two pages that can never make progress.
    const { router } = await renderRoute("/submissions", {
      auth: { status: "unavailable" },
    });

    expect(
      await screen.findByRole("heading", { name: /sign-in is unavailable/i }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/submissions");
    expect(router.history.length).toBe(1);
    // Still a screen inside the shell, not a dead end.
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", {
        name: /search the catalogue/i,
      }),
    ).toBeInTheDocument();
  });
});

describe("useAuthStatus seam", () => {
  it("is what RequireAuth reads, so the route table needs no change", async () => {
    // Asserts the seam itself still exists and is honoured, which is what
    // let #41 land without touching `route-tree.ts`'s authenticated subtree.
    vi.resetModules();
    vi.doMock("../auth/auth-status.ts", () => ({ useAuthStatus: () => "signed-in" }));
    const { renderRoute: fresh } = await import("../test/render-route.tsx");

    await fresh("/submissions", { auth: { status: "signed-out" } });

    expect(
      await screen.findByRole("heading", { name: /^submissions$/i }),
    ).toBeInTheDocument();

    vi.doUnmock("../auth/auth-status.ts");
    vi.resetModules();
  });
});
