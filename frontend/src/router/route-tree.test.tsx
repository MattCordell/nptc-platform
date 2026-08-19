import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderRoute } from "../test/render-route.tsx";
import { createAppRouter } from "./router.tsx";

/**
 * One entry per public URL shape declared in `route-tree.ts`. `to`/`params`/
 * `search` are the typed route-table inputs `<Link>`/`navigate` would use -
 * never a hand-built path string - so this fixture cannot drift from the
 * route table without failing to compile (acceptance criterion 4).
 *
 * Authenticated and admin routes are exercised separately in
 * `require-auth.test.tsx`, since `RequireAuth` intercepts them before their
 * own component renders.
 */
const ROUTES = [
  { to: "/", heading: /NPTC Catalogue Maintenance Platform/i },
  { to: "/catalogue", heading: /Search the catalogue/i },
  {
    to: "/catalogue/lookup",
    search: { system: "http://snomed.info/sct", code: "000123" },
    heading: /Code lookup/i,
  },
  {
    to: "/catalogue/code/$systemToken/$code",
    params: { systemToken: "sct", code: "000123" },
    heading: /Code lookup/i,
  },
  {
    to: "/catalogue/$businessKey",
    params: { businessKey: "NPTC-000247" },
    heading: /Catalogue entry/i,
  },
  {
    to: "/catalogue/$businessKey/history",
    params: { businessKey: "NPTC-000247" },
    heading: /Entry change history/i,
  },
  { to: "/releases", heading: /^Releases$/i },
  {
    to: "/releases/compare",
    search: { from: "R1", to: "R2" },
    heading: /Compare releases/i,
  },
  { to: "/releases/$releaseId", params: { releaseId: "R1" }, heading: /^Release$/i },
  { to: "/exports", heading: /^Exports$/i },
  { to: "/about", heading: /About the catalogue/i },
  { to: "/terms", heading: /Terms of use/i },
  { to: "/sign-in", heading: /Sign in/i },
  { to: "/sign-out", heading: /Sign out/i },
  { to: "/register", heading: /^Register$/i },
] as const;

describe("route table", () => {
  it.each(ROUTES)("deep-links straight into $to", async (spec) => {
    // Built from the route table itself - if a component needs an href, this
    // is what it must do too (criterion 4).
    const href = createAppRouter().buildLocation(spec).href;

    // A cold memory history is a fresh browser session: no prior navigation,
    // nothing in the router's cache, no landing page in between.
    const { router } = await renderRoute(href);

    expect(
      await screen.findByRole("heading", { level: 1, name: spec.heading }),
    ).toBeInTheDocument();
    // Criterion 1: rendered where we asked, not after a redirect.
    expect(router.state.location.pathname).toBe(
      new URL(href, "http://example.test").pathname,
    );
    expect(router.history.length).toBe(1);
  });

  it("keeps the app shell around every route", async () => {
    await renderRoute("/catalogue");
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  // FR-06 / #140: leading zeros, and an 18-digit SCTID beyond
  // Number.MAX_SAFE_INTEGER, must survive the URL unchanged - never coerced
  // to a number. This is the guard on `parseSearch`/`stringifySearch` in
  // router.tsx: reverting that override would silently break this.
  it.each(["000123", "900000000000003001"])(
    "round-trips the code %s through the path unchanged",
    async (code) => {
      const { router } = await renderRoute(`/catalogue/code/sct/${code}`);
      const params = router.state.matches.at(-1)?.params as { code: string };
      expect(params.code).toBe(code);
      expect(typeof params.code).toBe("string");
    },
  );

  it("round-trips the code through the ?code= query form unchanged", async () => {
    const { router } = await renderRoute(
      "/catalogue/lookup?system=http%3A%2F%2Fsnomed.info%2Fsct&code=900000000000003001",
    );
    const search = router.state.matches.at(-1)?.search as {
      code: string;
      system: string;
    };
    expect(search.code).toBe("900000000000003001");
    expect(typeof search.code).toBe("string");
    expect(search.system).toBe("http://snomed.info/sct");

    // And back out again, in FR-17's unquoted ?code=... contract shape.
    const href = router.buildLocation({ to: "/catalogue/lookup", search }).href;
    expect(href).toContain("code=900000000000003001");
    expect(href).not.toContain("code=%22900000000000003001%22");
  });

  it("matches the static /catalogue/lookup route ahead of /catalogue/$businessKey", async () => {
    await renderRoute("/catalogue/lookup?system=x&code=1");
    expect(
      screen.getByRole("heading", { level: 1, name: /Code lookup/i }),
    ).toBeInTheDocument();
  });

  it("restores search state from a pasted URL (#140)", async () => {
    const { router } = await renderRoute("/catalogue?q=glucose&page=3&sort=code");
    expect(router.state.matches.at(-1)?.search).toEqual({
      q: "glucose",
      page: 3,
      sort: "code",
    });
  });

  it("falls back to safe defaults for a malformed search param rather than erroring", async () => {
    const { router } = await renderRoute("/catalogue?page=not-a-number&sort=nonsense");
    expect(router.state.matches.at(-1)?.search).toEqual({
      q: "",
      page: 1,
      sort: "relevance",
    });
    expect(
      screen.getByRole("heading", { level: 1, name: /Search the catalogue/i }),
    ).toBeInTheDocument();
  });
});
