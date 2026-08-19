import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderRoute } from "../test/render-route.tsx";

describe("RootLayout", () => {
  it("renders exactly one of each landmark", async () => {
    await renderRoute("/catalogue");
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
  });

  it("puts the skip link first, targeting the main landmark", async () => {
    const user = userEvent.setup();
    await renderRoute("/catalogue");

    await user.tab();
    const skipLink = screen.getByRole("link", { name: /skip to main content/i });
    expect(document.activeElement).toBe(skipLink);
    expect(skipLink).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("moves focus to <main> after a navigation", async () => {
    const user = userEvent.setup();
    // Start somewhere other than the link's destination - clicking a link
    // to the route already on screen wouldn't change the pathname, so the
    // focus effect (keyed on pathname) wouldn't fire.
    await renderRoute("/");

    const nav = screen.getByRole("navigation", { name: /primary/i });
    await user.click(within(nav).getByRole("link", { name: /search the catalogue/i }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("main")));
  });

  it("does not move focus to <main> on the initial render", async () => {
    await renderRoute("/catalogue");
    expect(document.activeElement).not.toBe(screen.getByRole("main"));
  });

  it("sets the document title per route", async () => {
    await renderRoute("/catalogue");
    await waitFor(() => expect(document.title).toMatch(/Search/));
  });
});
