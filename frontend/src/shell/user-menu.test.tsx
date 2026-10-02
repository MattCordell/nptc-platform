import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The header's account control across the four auth states (NFR-31), and the
 * rule that it only ever offers links - it never decides access (NFR-20).
 */

function sessionRoute(user: Record<string, unknown> | null): Route {
  return {
    method: "GET",
    path: "/auth/me",
    status: 200,
    body: {
      authenticated: user !== null,
      user,
      roles: ["administrator"],
      permissions: ["catalogue.edit"],
      mfa_satisfied: true,
    },
  };
}

const CURATOR = {
  username: "a.curator",
  display_name: "A Curator",
  organisation: null,
  status: "active",
};

const SIGNED_IN = { auth: { status: "signed-in" as const } };

function header() {
  return screen.getByRole("banner");
}

describe("UserMenu in each auth state (NFR-31)", () => {
  it("shows an inert placeholder while the session is restoring", async () => {
    const calls = stubApi([]);
    await renderRoute("/about", { auth: { status: "restoring" } });

    expect(within(header()).queryByRole("link", { name: /sign in/i })).toBeNull();
    expect(within(header()).queryByRole("link", { name: /register/i })).toBeNull();
    expect(within(header()).queryByRole("button")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("offers Register and Sign in when signed out, without calling the API", async () => {
    const calls = stubApi([]);
    await renderRoute("/about", { auth: { status: "signed-out" } });

    expect(within(header()).getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
    expect(within(header()).getByRole("link", { name: "Register" })).toHaveAttribute(
      "href",
      "/register",
    );
    expect(calls).toHaveLength(0);
  });

  it("shows muted text, not a status region, when sign-in is unavailable", async () => {
    const calls = stubApi([]);
    await renderRoute("/about", { auth: { status: "unavailable" } });

    expect(within(header()).getByText("Sign-in unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    expect(within(header()).queryByRole("link", { name: /sign in/i })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("labels the signed-in button with the display name", async () => {
    stubApi([sessionRoute(CURATOR)]);
    await renderRoute("/about", SIGNED_IN);

    expect(
      await within(header()).findByRole("button", { name: /A Curator/ }),
    ).toBeInTheDocument();
  });

  it("falls back to the username, then to a generic label", async () => {
    stubApi([sessionRoute({ ...CURATOR, display_name: null })]);
    const { unmount } = await renderRoute("/about", SIGNED_IN);
    expect(
      await within(header()).findByRole("button", { name: /a\.curator/ }),
    ).toBeInTheDocument();
    unmount();

    stubApi([sessionRoute({ ...CURATOR, display_name: null, username: null })]);
    await renderRoute("/about", SIGNED_IN);
    expect(
      await within(header()).findByRole("button", { name: /Your account/ }),
    ).toBeInTheDocument();
  });

  it("still offers Account and Sign out when /auth/me fails", async () => {
    stubApi([{ method: "GET", path: "/auth/me", status: 500, body: { detail: "boom" } }]);
    const user = userEvent.setup();
    await renderRoute("/about", SIGNED_IN);

    const button = await within(header()).findByRole("button", { name: /Your account/ });
    await user.click(button);

    const panel = within(document.getElementById(button.getAttribute("aria-controls")!)!);
    expect(panel.getByRole("link", { name: "Account" })).toBeVisible();
    expect(panel.getByRole("link", { name: "Sign out" })).toBeVisible();
  });

  it("never shows roles or permissions in the menu", async () => {
    stubApi([sessionRoute(CURATOR)]);
    const user = userEvent.setup();
    await renderRoute("/about", SIGNED_IN);

    await user.click(await within(header()).findByRole("button", { name: /A Curator/ }));

    expect(within(header()).queryByText(/administrator|catalogue\.edit/)).toBeNull();
  });
});

describe("UserMenu disclosure (NFR-31)", () => {
  // The primary nav also has an "Account" link, so the menu's own is found
  // through the panel the button controls.
  function panelOf(button: HTMLElement) {
    return within(document.getElementById(button.getAttribute("aria-controls")!)!);
  }

  async function openMenu() {
    stubApi([sessionRoute(CURATOR)]);
    const user = userEvent.setup();
    const rendered = await renderRoute("/about", SIGNED_IN);
    const button = await within(header()).findByRole("button", { name: /A Curator/ });
    return { user, button, ...rendered };
  }

  it("starts closed and toggles aria-expanded on click", async () => {
    const { user, button } = await openMenu();

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: "Sign out" })).toBeNull();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panelOf(button).getByRole("link", { name: "Account" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Sign out" })).toHaveAttribute(
      "href",
      "/sign-out",
    );

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("opens from the keyboard", async () => {
    const { user, button } = await openMenu();

    button.focus();
    await user.keyboard("{Enter}");

    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("closes on Escape and returns focus to the button", async () => {
    const { user, button } = await openMenu();
    await user.click(button);
    screen.getByRole("link", { name: "Sign out" }).focus();

    await user.keyboard("{Escape}");

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(button);
  });

  it("closes on a click outside the menu", async () => {
    const { user, button } = await openMenu();
    await user.click(button);

    await user.click(screen.getByRole("main"));

    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("closes when Tab moves focus out of the menu", async () => {
    const { user, button } = await openMenu();
    await user.click(button);
    screen.getByRole("link", { name: "Sign out" }).focus();

    await user.tab();

    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("closes and navigates when Account is chosen", async () => {
    const { user, button, router } = await openMenu();
    await user.click(button);

    await user.click(panelOf(button).getByRole("link", { name: "Account" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("has no axe violations while open", async () => {
    const { user, button, container } = await openMenu();
    await user.click(button);

    await expectNoA11yViolations(container);
  });
});
