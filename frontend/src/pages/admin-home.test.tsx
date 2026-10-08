import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { SESSION_QUERY_KEY } from "../api/queries.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";
import type { Route } from "../test/stub-api.ts";

/**
 * The admin home (NFR-20, NFR-31). What the shell shows, never access control:
 * the server refuses what a caller may not do, so no card is ever hidden.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

function sessionRoute(overrides: Record<string, unknown> = {}): Route {
  return {
    method: "GET",
    path: "/auth/me",
    status: 200,
    body: {
      authenticated: true,
      user: {
        username: "a.curator",
        display_name: "A Curator",
        organisation: null,
        status: "active",
      },
      roles: ["administrator"],
      permissions: [],
      mfa_satisfied: true,
      ...overrides,
    },
  };
}

function main() {
  return within(screen.getByRole("main"));
}

const BUILT = [
  { name: "Catalogue administration", href: "/admin/catalogue" },
  { name: "Property registry", href: "/admin/properties" },
  { name: "Audit log", href: "/admin/audit" },
];

const PLANNED = [
  { name: "User administration", href: "/admin/users" },
  { name: "Validation findings", href: "/admin/validation" },
  { name: "Release administration", href: "/admin/releases" },
  { name: "Export configuration", href: "/admin/exports/config" },
];

const NOTICE = "Your account does not have the administrator role.";
const SESSION_NOTICE = "This session does not include the administrator role.";

/**
 * Waits until the session read has an answer, so an assertion that the notice
 * is absent runs after the page could have shown it. Without this wait such an
 * assertion passes while the request is still in flight.
 */
async function sessionSettled(queryClient: QueryClient, status: "success" | "error") {
  await waitFor(() =>
    expect(queryClient.getQueryState(SESSION_QUERY_KEY)?.status).toBe(status),
  );
}

describe("admin home", () => {
  it("links to every built admin screen", async () => {
    stubApi([sessionRoute()]);
    await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    for (const { name, href } of BUILT) {
      expect(main().getByRole("link", { name })).toHaveAttribute("href", href);
    }
  });

  it("links to every planned admin screen and labels each one Planned in text", async () => {
    stubApi([sessionRoute()]);
    await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    for (const { name, href } of PLANNED) {
      const link = main().getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      const card = link.closest("li");
      expect(card).not.toBeNull();
      expect(within(card!).getByText("Planned")).toBeVisible();
    }
    // Built screens carry no Planned label.
    for (const { name } of BUILT) {
      const card = main().getByRole("link", { name }).closest("li");
      expect(within(card!).queryByText("Planned")).not.toBeInTheDocument();
    }
  });

  it("reaches a built screen from its card", async () => {
    const user = userEvent.setup();
    stubApi([sessionRoute()]);
    const { router } = await renderRoute("/admin", SIGNED_IN);

    await user.click(await main().findByRole("link", { name: "Property registry" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/properties"));
  });

  it("shows no notice to an administrator", async () => {
    stubApi([sessionRoute()]);
    const { queryClient } = await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    await sessionSettled(queryClient, "success");
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByText(SESSION_NOTICE)).not.toBeInTheDocument();
  });

  it("shows no notice to an administrator who has not finished the second sign-in step", async () => {
    stubApi([sessionRoute({ mfa_satisfied: false })]);
    const { queryClient } = await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    await sessionSettled(queryClient, "success");
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByText(SESSION_NOTICE)).not.toBeInTheDocument();
  });

  it("tells a signed-in user without the role, and still shows every card", async () => {
    stubApi([sessionRoute({ roles: ["member"] })]);
    await renderRoute("/admin", SIGNED_IN);

    expect(await screen.findByText(NOTICE)).toBeVisible();
    expect(screen.getByText(/Ask an administrator to grant you the role/)).toBeVisible();
    for (const { name } of [...BUILT, ...PLANNED]) {
      expect(main().getByRole("link", { name })).toBeVisible();
    }
  });

  it("points a user who has not finished the second sign-in step to the banner", async () => {
    stubApi([sessionRoute({ roles: ["member"], mfa_satisfied: false })]);
    await renderRoute("/admin", SIGNED_IN);

    expect(await screen.findByText(SESSION_NOTICE)).toBeVisible();
    expect(screen.getByText(/complete the extra sign-in step at the top/)).toBeVisible();
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByText(/grant you the role/)).not.toBeInTheDocument();
  });

  it("shows no notice while the session read is pending", async () => {
    stubApi([{ ...sessionRoute(), neverSettles: true }]);
    await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(main().getByRole("link", { name: "Audit log" })).toBeVisible();
  });

  it("shows no notice, and every card, when the session read fails", async () => {
    stubApi([{ method: "GET", path: "/auth/me", status: 500, body: { detail: "boom" } }]);
    const { queryClient } = await renderRoute("/admin", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Administration" });
    await sessionSettled(queryClient, "error");
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByText(SESSION_NOTICE)).not.toBeInTheDocument();
    for (const { name } of [...BUILT, ...PLANNED]) {
      expect(main().getByRole("link", { name })).toBeVisible();
    }
  });

  it("has one h1, one card heading per screen, and no axe violations", async () => {
    stubApi([sessionRoute({ roles: ["member"] })]);
    const { container } = await renderRoute("/admin", SIGNED_IN);

    await screen.findByText(NOTICE);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(main().getAllByRole("heading", { level: 2 })).toHaveLength(
      BUILT.length + PLANNED.length,
    );
    expect(
      screen.getByRole("navigation", { name: "Administration screens" }),
    ).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("reaches every card from the keyboard, in reading order", async () => {
    const user = userEvent.setup();
    stubApi([sessionRoute()]);
    await renderRoute("/admin", SIGNED_IN);
    await screen.findByRole("heading", { level: 1, name: "Administration" });

    const names = [...BUILT, ...PLANNED].map((screenLink) => screenLink.name);
    main().getByRole("link", { name: names[0] }).focus();
    for (const name of names.slice(1)) {
      await user.tab();
      expect(document.activeElement).toBe(main().getByRole("link", { name }));
    }
  });
});
