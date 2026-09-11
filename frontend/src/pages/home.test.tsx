import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";

// Scoped to the `main` landmark throughout: the site header and footer
// (rendered by RootLayout around every route) already carry their own
// "Search the catalogue" and "About the catalogue" links, so an unscoped
// getByRole query would match more than one element.
function main() {
  return within(screen.getByRole("main"));
}

describe("/ (homepage)", () => {
  it("shows register and sign-in actions for a signed-out visitor", async () => {
    const { container } = await renderRoute("/", { auth: { status: "signed-out" } });

    expect(main().getByRole("link", { name: "Register" })).toHaveAttribute(
      "href",
      "/register",
    );
    expect(main().getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
    expect(main().queryByRole("link", { name: "Sign out" })).not.toBeInTheDocument();
    // The first screen with a `Link` styled to look like a `Button` - worth
    // an axe pass in its own right, not just the pattern's origin in
    // `button.test.tsx`.
    await expectNoA11yViolations(container);
  });

  it("shows only a sign-out action for a signed-in visitor", async () => {
    const { container } = await renderRoute("/", { auth: { status: "signed-in" } });

    expect(main().getByRole("link", { name: "Sign out" })).toHaveAttribute(
      "href",
      "/sign-out",
    );
    expect(main().queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    await expectNoA11yViolations(container);
  });

  it("shows no auth actions while the session is still restoring", async () => {
    await renderRoute("/", { auth: { status: "restoring" } });

    expect(main().queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("shows no auth actions when auth is unavailable", async () => {
    await renderRoute("/", { auth: { status: "unavailable" } });

    expect(main().queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it.each(["restoring", "signed-in", "signed-out", "unavailable"] as const)(
    "always shows the catalogue and about links regardless of auth status (%s)",
    async (status) => {
      await renderRoute("/", { auth: { status } });

      expect(main().getByRole("link", { name: "Search the catalogue" })).toHaveAttribute(
        "href",
        "/catalogue",
      );
      expect(main().getByRole("link", { name: "About the catalogue" })).toHaveAttribute(
        "href",
        "/about",
      );
    },
  );
});
