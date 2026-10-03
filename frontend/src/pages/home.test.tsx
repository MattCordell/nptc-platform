import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it.each(["restoring", "signed-in", "signed-out", "unavailable"] as const)(
    "has one h1 and three h2 headings in order (%s)",
    async (status) => {
      await renderRoute("/", { auth: { status } });

      expect(main().getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(
        main()
          .getAllByRole("heading", { level: 2 })
          .map((heading) => heading.textContent),
      ).toEqual(["What the catalogue is", "Who maintains it", "How to contribute"]);
    },
  );

  describe("search form", () => {
    it("is a labelled search landmark with a text box and a Search button", async () => {
      await renderRoute("/", { auth: { status: "signed-out" } });

      const search = within(main().getByRole("search", { name: "Catalogue search" }));
      expect(
        search.getByRole("searchbox", { name: "Search by test name or code" }),
      ).toBeInTheDocument();
      expect(search.getByRole("button", { name: "Search" })).toHaveAttribute(
        "type",
        "submit",
      );
    });

    it("hands the trimmed query to /catalogue", async () => {
      const user = userEvent.setup();
      const { router } = await renderRoute("/", { auth: { status: "signed-out" } });

      await user.type(
        main().getByRole("searchbox", { name: "Search by test name or code" }),
        "  glucose  ",
      );
      await user.click(main().getByRole("button", { name: "Search" }));

      expect(router.state.location.pathname).toBe("/catalogue");
      expect(router.state.location.search).toEqual({ q: "glucose" });
    });

    it("submits from the keyboard with Enter", async () => {
      const user = userEvent.setup();
      const { router } = await renderRoute("/", { auth: { status: "signed-out" } });

      await user.type(
        main().getByRole("searchbox", { name: "Search by test name or code" }),
        "HbA1c{Enter}",
      );

      expect(router.state.location.pathname).toBe("/catalogue");
      expect(router.state.location.search).toEqual({ q: "HbA1c" });
    });

    it.each(["", "   "])(
      "goes to plain /catalogue when the query is empty (%j)",
      async (typed) => {
        const user = userEvent.setup();
        const { router } = await renderRoute("/", { auth: { status: "signed-out" } });

        if (typed) {
          await user.type(
            main().getByRole("searchbox", { name: "Search by test name or code" }),
            typed,
          );
        }
        await user.click(main().getByRole("button", { name: "Search" }));

        expect(router.state.location.pathname).toBe("/catalogue");
        expect(router.state.location.search).toEqual({});
        expect(router.state.location.searchStr).toBe("");
      },
    );

    it.each(["restoring", "signed-in", "signed-out", "unavailable"] as const)(
      "is available regardless of auth status (%s)",
      async (status) => {
        await renderRoute("/", { auth: { status } });

        expect(main().getByRole("search", { name: "Catalogue search" })).toBeVisible();
      },
    );
  });

  describe("contribute card", () => {
    it("links a signed-out visitor to registration without a second plain Register link", async () => {
      await renderRoute("/", { auth: { status: "signed-out" } });

      expect(
        main().getByRole("link", { name: "Register to contribute" }),
      ).toHaveAttribute("href", "/register");
      expect(main().getAllByRole("link", { name: "Register" })).toHaveLength(1);
    });

    it("links a signed-in visitor to their submissions", async () => {
      await renderRoute("/", { auth: { status: "signed-in" } });

      expect(main().getByRole("link", { name: "My submissions" })).toHaveAttribute(
        "href",
        "/submissions",
      );
      expect(
        main().queryByRole("link", { name: "Register to contribute" }),
      ).not.toBeInTheDocument();
    });

    it.each(["restoring", "unavailable"] as const)(
      "shows text only, with no contribute link, while auth is %s",
      async (status) => {
        await renderRoute("/", { auth: { status } });

        const card = main().getByRole("heading", { name: "How to contribute" })
          .parentElement as HTMLElement;
        expect(within(card).queryByRole("link")).not.toBeInTheDocument();
        expect(card).toHaveTextContent("Registered members can propose new tests");
        expect(card).not.toHaveTextContent("Register to get started");
      },
    );
  });
});
