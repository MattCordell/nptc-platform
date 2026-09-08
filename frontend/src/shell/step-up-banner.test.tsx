import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The pre-emptive step-up offer (issue #184, NFR-06) - shown on `/admin/*`
 * (via `AdminLayout`) so an administrator can complete the realm's second
 * factor before walking into a 403, not only after one.
 */

const SESSION_PATH = "/auth/me";

function sessionRoute(overrides: Record<string, unknown> = {}): Route {
  return {
    method: "GET",
    path: SESSION_PATH,
    status: 200,
    body: {
      authenticated: true,
      user: {
        username: "a.curator",
        display_name: "A Curator",
        organisation: null,
        status: "active",
      },
      roles: ["Administrator"],
      permissions: [],
      mfa_satisfied: false,
      ...overrides,
    },
  };
}

const SIGNED_IN = { auth: { status: "signed-in" as const } };

describe("StepUpBanner", () => {
  it("offers to verify when signed in without MFA", async () => {
    stubApi([sessionRoute()]);

    await renderRoute("/admin", SIGNED_IN);

    expect(await screen.findByText(/have not completed yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify now" })).toBeInTheDocument();
  });

  it("renders nothing once MFA is satisfied", async () => {
    stubApi([sessionRoute({ mfa_satisfied: true })]);

    await renderRoute("/admin", SIGNED_IN);

    // Waits for the query to settle before asserting an absence - otherwise
    // this would trivially pass while the request is still in flight.
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("renders nothing for an unauthenticated session", async () => {
    stubApi([sessionRoute({ authenticated: false, user: null, mfa_satisfied: false })]);

    await renderRoute("/admin", SIGNED_IN);

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("does not appear outside /admin", async () => {
    stubApi([sessionRoute()]);

    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("heading", { name: /^submissions$/i });
    expect(screen.queryByRole("button", { name: "Verify now" })).not.toBeInTheDocument();
  });

  it("verifying carries the current path and the realm's LoA into the redirect", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn().mockResolvedValue(undefined);
    stubApi([sessionRoute()]);

    await renderRoute("/admin", { auth: { ...SIGNED_IN.auth, signIn } });
    await user.click(await screen.findByRole("button", { name: "Verify now" }));

    expect(signIn).toHaveBeenCalledWith({ acrValues: "2", redirect: "/admin" });
  });
});
