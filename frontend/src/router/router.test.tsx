import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("not-found route", () => {
  it("renders the not-found page for an unknown URL, not a blank screen", async () => {
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/no-such-page");

    expect(
      await screen.findByRole("heading", { level: 1, name: /couldn't find that page/i }),
    ).toBeInTheDocument();
    // Fuzzy matching keeps the shell, so the user has somewhere to go.
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", {
        name: /search the catalogue/i,
      }),
    ).toBeInTheDocument();
  });

  it("renders the not-found page for an unrecognised segment under a real route", async () => {
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/catalogue/NPTC-000247/not-a-tab");

    expect(
      await screen.findByRole("heading", { level: 1, name: /couldn't find that page/i }),
    ).toBeInTheDocument();
  });
});

describe("route error boundary", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // The "not-found route" tests above already imported the production
    // module graph (route-tree.ts imports pages/home.tsx unconditionally,
    // whatever route is under test), so it is cached by the time these
    // tests run. Reset it before `vi.doMock` so the next dynamic import
    // re-evaluates that graph against the mock, instead of returning the
    // already-cached, unmocked modules.
    vi.resetModules();
    // React and the router both log the caught error, which is correct
    // behaviour, but it shouldn't spam test output.
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    vi.doUnmock("../pages/home.tsx");
    vi.resetModules();
  });

  it("catches a thrown render error and says what to do next (PRD SS17.2 item 5)", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("ORA-00600: internal error at rowid 0x8f3a");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    const { container } = await renderRoute("/");

    expect(
      await screen.findByRole("heading", { level: 1, name: /something went wrong/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", {
        name: /search the catalogue/i,
      }),
    ).toBeInTheDocument();

    // The friendly heading passing is not enough on its own - it would still
    // pass with a stack trace printed underneath. Assert the exception text,
    // a source frame, and a raw status code are all absent from the DOM.
    expect(screen.queryByText(/ORA-00600/)).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/ORA-00600/);
    expect(container.textContent).not.toMatch(/\.tsx:\d+/);
    expect(container.textContent).not.toMatch(/\b[45]\d\d\b/);

    // The detail still reaches a developer.
    expect(consoleError).toHaveBeenCalled();
  });

  it("keeps the shell so the user can navigate away from the error", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("boom");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/");

    expect(
      await screen.findByRole("navigation", { name: /primary/i }),
    ).toBeInTheDocument();
  });
});
