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

  it("tries silently first, closing the banner with no dialog on success", async () => {
    // PR #284 review: "Verify now" used to redirect unconditionally. It now
    // goes through the same controller as a reactive challenge, so a step-up
    // the SSO session can satisfy silently never shows the dialog at all -
    // the banner just disappears once the (now-satisfied) session refetches.
    const user = userEvent.setup();
    const stepUp = vi.fn().mockResolvedValue("done");
    stubApi([sessionRoute()]);

    await renderRoute("/admin", { auth: { ...SIGNED_IN.auth, stepUp } });
    await user.click(await screen.findByRole("button", { name: "Verify now" }));

    await waitFor(() => expect(stepUp).toHaveBeenCalledWith("2"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stops offering to verify once a silent step-up actually invalidates the session", async () => {
    // PR #284 review: nothing used to invalidate useSession's own query key
    // after a successful step-up, so the banner kept saying "you have not
    // completed yet" for the rest of the tab's life even once the session
    // genuinely had. `StepUpController` now invalidates it directly.
    const user = userEvent.setup();
    const stepUp = vi.fn().mockResolvedValue("done");
    stubApi([], {
      vary: (call, priorSameCalls) => {
        if (call.method !== "GET" || !call.path.endsWith(SESSION_PATH)) {
          return null;
        }
        // StrictMode double-mounts, so the load itself is two reads (same
        // precedent as `admin-catalogue-edit.test.tsx`) - only the refetch
        // `StepUpController`'s invalidation triggers is the satisfied one.
        return priorSameCalls < 2
          ? sessionRoute()
          : sessionRoute({ mfa_satisfied: true });
      },
    });

    await renderRoute("/admin", { auth: { ...SIGNED_IN.auth, stepUp } });
    await user.click(await screen.findByRole("button", { name: "Verify now" }));

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("falls back to the same interactive dialog, carrying the current path and the realm's LoA", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn().mockResolvedValue(undefined);
    stubApi([sessionRoute()]);

    // The default test `stepUp` resolves "interaction-required"
    // (`render-route.tsx`), so this exercises the fallback dialog rather
    // than the silent path above.
    await renderRoute("/admin", { auth: { ...SIGNED_IN.auth, signIn } });
    await user.click(await screen.findByRole("button", { name: "Verify now" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(signIn).toHaveBeenCalledWith({ acrValues: "2", redirect: "/admin" });
  });
});
