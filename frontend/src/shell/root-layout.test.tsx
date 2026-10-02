import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
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

describe("site header and footer (NFR-20, NFR-31)", () => {
  it("has no axe violations", async () => {
    const { container } = await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });
    await expectNoA11yViolations(container);
  });

  it("adds no h1 of its own", async () => {
    await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });
    expect(within(screen.getByRole("banner")).queryByRole("heading")).toBeNull();
    expect(within(screen.getByRole("contentinfo")).queryByRole("heading")).toBeNull();
  });

  it("marks only the current page's nav link with aria-current", async () => {
    await renderRoute("/releases");
    const nav = screen.getByRole("navigation", { name: /primary/i });

    expect(within(nav).getByRole("link", { name: "Releases" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Admin" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("keeps every nav link visible to a signed-out visitor, admin included", async () => {
    await renderRoute("/about", { auth: { status: "signed-out" } });
    const nav = screen.getByRole("navigation", { name: /primary/i });

    for (const name of [
      "Search the catalogue",
      "Releases",
      "Submissions",
      "My interest",
      "Admin",
      "Account",
    ]) {
      expect(within(nav).getByRole("link", { name })).toBeVisible();
    }
  });

  it("keeps the footer links and publisher line", async () => {
    await renderRoute("/about");
    const footer = screen.getByRole("contentinfo");

    for (const name of ["About the catalogue", "Exports", "Terms of use"]) {
      expect(within(footer).getByRole("link", { name })).toBeVisible();
    }
    expect(
      within(footer).getByText(/RCPA-QAP National Pathology Test Catalogue/),
    ).toBeVisible();
  });
});
