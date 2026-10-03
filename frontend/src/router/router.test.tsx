import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";

describe("not-found route", () => {
  it("renders the not-found page for an unknown URL, not a blank screen", async () => {
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
    // The not-found/error surfaces sit outside the route table's per-route
    // `head` mechanism (they replace whatever route was requested, rather
    // than being one), so they set the title themselves - otherwise a cold
    // deep link to an unknown URL would leave the title blank.
    await waitFor(() => expect(document.title).toMatch(/page not found/i));
  });

  it("moves focus to the heading on a cold load, with no other focus move", async () => {
    await renderRoute("/no-such-page");

    const heading = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("keeps focus on the heading after a client-side navigation to it", async () => {
    // The root layout moves focus to <main> after every navigation; the
    // heading's own focus move must survive that.
    const { router } = await renderRoute("/");

    // `history.push`, not `router.navigate`: the typed `to` rejects a path
    // that matches no route, which is the point of this test.
    await act(async () => {
      router.history.push("/no-such-page");
      await router.load();
    });

    const heading = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(document.activeElement).not.toBe(screen.getByRole("main"));
  });

  it("links back to the landing page", async () => {
    const user = userEvent.setup();
    const { router } = await renderRoute("/no-such-page");

    await user.click(
      await within(await screen.findByRole("main")).findByRole("link", {
        name: "Back to the landing page",
      }),
    );

    expect(router.state.location.pathname).toBe("/");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = await renderRoute("/no-such-page");

    await screen.findByRole("heading", { level: 1 });
    await expectNoA11yViolations(container);
  });

  it("renders the not-found page for an unrecognised segment under a real route", async () => {
    await renderRoute("/catalogue/NPTC-000247/not-a-tab");

    expect(
      await screen.findByRole("heading", { level: 1, name: /couldn't find that page/i }),
    ).toBeInTheDocument();
  });
});

describe("route error boundary", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // The static `renderRoute` import at the top of this file already
    // pulled in the production module graph (route-tree.ts imports
    // pages/home.tsx unconditionally, whatever route is under test), so it
    // is cached by the time these tests run. Reset it before `vi.doMock` so
    // the next dynamic import re-evaluates that graph against the mock,
    // instead of returning the already-cached, unmocked modules.
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
    // pass with a stack trace printed underneath. Assert the exception text
    // and a source frame are both absent from the DOM. (No generic 3-digit
    // "status code" pattern is asserted here: nothing in this component ever
    // renders one - there is no fetch/response layer yet - and a blanket
    // `\d{3}` check would fail on any legitimate three-digit number a later
    // page might show, e.g. a count or a year, for reasons unrelated to this
    // test's purpose.)
    expect(screen.queryByText(/ORA-00600/)).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/ORA-00600/);
    expect(container.textContent).not.toMatch(/\.tsx:\d+/);

    // The detail still reaches a developer.
    expect(consoleError).toHaveBeenCalled();
    await waitFor(() => expect(document.title).toMatch(/something went wrong/i));
  });

  it("moves focus to the heading and keeps it there", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("boom");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/");

    const heading = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("keeps focus on the heading after a client-side navigation to the error", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("boom");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    const { router } = await renderRoute("/about");

    await act(async () => {
      router.history.push("/");
      await router.load();
    });

    const heading = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(document.activeElement).not.toBe(screen.getByRole("main"));
  });

  it("links back to the landing page", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("boom");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/");

    expect(
      await within(await screen.findByRole("main")).findByRole("link", {
        name: "Back to the landing page",
      }),
    ).toHaveAttribute("href", "/");
  });

  it("shows the screen again when 'Try again' is chosen and the fault has cleared", async () => {
    const user = userEvent.setup();
    let failing = true;
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        if (failing) {
          throw new Error("boom");
        }
        return <h1>Recovered</h1>;
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    await renderRoute("/");

    const tryAgain = await screen.findByRole("button", { name: /try again/i });
    failing = false;
    await user.click(tryAgain);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Recovered" }),
    ).toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    vi.doMock("../pages/home.tsx", () => ({
      HomePage: () => {
        throw new Error("boom");
      },
    }));
    const { renderRoute } = await import("../test/render-route.tsx");
    const { container } = await renderRoute("/");

    await screen.findByRole("heading", { level: 1 });
    await expectNoA11yViolations(container);
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
