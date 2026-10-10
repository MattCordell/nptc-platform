import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/unwrap.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi, type Route } from "../test/stub-api.ts";

/**
 * The terms acceptance gate (NFR-45, NFR-31). It sits in front of the
 * signed-in routes, so each test opens `/submissions` as a signed-in user and
 * drives the stubbed `GET /auth/terms` and `POST /auth/terms/acceptance`. The
 * server is the authority; these tests are about what the SPA presents.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

function terms(overrides: Record<string, unknown> = {}) {
  return {
    version: "2026-10-06",
    effective_date: "2026-10-06",
    text: "# NPTC terms of use\n\n## 1. Using the platform\n\nYou may read the catalogue without an account.",
    accepted: false,
    ...overrides,
  };
}

const ACCEPTED_AT = "2026-10-07T01:00:00Z";

/**
 * A stub server whose acceptance state moves: a successful POST flips
 * `accepted`, so the refetch the app makes afterwards sees what a real server
 * would answer. `onPost` lets a test replace the POST answer.
 */
function stubServer(options: {
  accepted?: boolean;
  version?: string;
  onPost?: (state: { accepted: boolean; version: string }) => Route | null;
}) {
  const state = {
    accepted: options.accepted ?? false,
    version: options.version ?? "2026-10-06",
  };
  const calls = stubApi([], {
    vary: ({ method, path }) => {
      if (method === "GET" && path.endsWith("/auth/terms")) {
        return {
          method,
          path,
          status: 200,
          body: terms({ accepted: state.accepted, version: state.version }),
        };
      }
      if (method === "POST" && path.endsWith("/auth/terms/acceptance")) {
        const custom = options.onPost?.(state);
        if (custom) {
          return custom;
        }
        state.accepted = true;
        return {
          method,
          path,
          status: 200,
          body: { version: state.version, accepted_at: ACCEPTED_AT },
        };
      }
      return null;
    },
  });
  return { calls, state };
}

function posts(calls: ReturnType<typeof stubApi>) {
  return calls.filter((call) => call.method === "POST");
}

describe("the gate is shown or hidden by the accepted flag", () => {
  it("shows the gate, and not the page, to a signed-in user who has not accepted", async () => {
    stubServer({ accepted: false });
    const { container } = await renderRoute("/submissions", SIGNED_IN);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Accept the terms of use" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.queryByRole("heading", { name: /^submissions$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "NPTC terms of use" }),
    ).toBeVisible();
    expect(screen.getByText(/Version 2026-10-06, effective/)).toBeVisible();
    expect(screen.getByText("6 October 2026")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows the page, not the gate, to a user who has accepted", async () => {
    stubServer({ accepted: true });
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "Accept the terms of use" }),
      ).not.toBeInTheDocument();
    });
  });

  // Principal failure mode: the server is the enforcer, so a read that fails
  // must not lock a user out of the app.
  it("does not block the page when the terms cannot be read", async () => {
    stubApi([
      { method: "GET", path: "/auth/terms", status: 500, body: { detail: "boom" } },
    ]);
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Accept the terms of use" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the page mounted but hidden while the gate shows", async () => {
    stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);
    await screen.findByRole("heading", { name: "Accept the terms of use" });

    const hiddenPage = screen.getByRole("heading", {
      name: /^submissions$/i,
      hidden: true,
    });
    expect(hiddenPage).not.toBeVisible();
  });

  it("shows the gate when a write is refused mid-session for unaccepted terms", async () => {
    const { state } = stubServer({ accepted: true });
    const { queryClient } = await renderRoute("/submissions", SIGNED_IN);
    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();

    // The terms changed under a long session: the cached `accepted: true` is
    // now wrong, and the next write is refused.
    state.accepted = false;
    await queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationFn: () =>
          Promise.reject(
            new ApiError(403, {
              detail: "Accept the terms first.",
              code: "terms_acceptance_required",
            }),
          ),
      })
      .execute(undefined)
      .catch(() => undefined);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Accept the terms of use" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /^submissions$/i }),
    ).not.toBeInTheDocument();
  });
});

describe("accepting the terms", () => {
  it("puts focus on the heading when the gate appears", async () => {
    stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);

    const heading = await screen.findByRole("heading", {
      name: "Accept the terms of use",
    });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("refuses an unticked submit, says why, and links the message to the box", async () => {
    const user = userEvent.setup();
    const { calls } = stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("button", { name: "Accept and continue" }));

    expect(posts(calls)).toHaveLength(0);
    const link = await screen.findByRole("link", {
      name: "Tick the box to say you accept the terms of use.",
    });
    const summary = screen.getByText("There is a problem").closest("div");
    expect(document.activeElement).toBe(summary);
    await user.click(link);
    expect(document.activeElement).toBe(screen.getByRole("checkbox"));
  });

  it("records acceptance of the displayed version and returns the user to the page", async () => {
    const user = userEvent.setup();
    const { calls } = stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    expect(posts(calls)).toHaveLength(1);
    expect(posts(calls)[0]!.body).toEqual({ version: "2026-10-06" });
    expect(
      screen.queryByRole("heading", { name: "Accept the terms of use" }),
    ).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.activeElement).toBe(document.getElementById("main-content"));
    });
  });

  it("can be completed from the keyboard alone", async () => {
    const user = userEvent.setup();
    const { calls } = stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);
    await screen.findByRole("heading", { name: "Accept the terms of use" });

    const checkbox = screen.getByRole("checkbox");
    // The skip link, header and privacy link come first; tab until the box.
    for (let step = 0; step < 30 && document.activeElement !== checkbox; step += 1) {
      await user.tab();
    }
    expect(document.activeElement).toBe(checkbox);
    await user.keyboard(" ");
    expect(checkbox).toBeChecked();
    await user.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Accept and continue" }),
    );
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    expect(posts(calls)).toHaveLength(1);
  });

  it("offers a Sign out link beside Accept", async () => {
    stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);
    await screen.findByRole("heading", { name: "Accept the terms of use" });

    const signOut = screen
      .getAllByRole("link", { name: "Sign out" })
      .find((link) => link.closest("form") !== null);
    expect(signOut).toHaveAttribute("href", "/sign-out");
  });

  it("shows the collection notice and links the privacy policy", async () => {
    stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);

    expect(
      await screen.findByRole("heading", { level: 2, name: "How we use your details" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Read the privacy policy" })).toHaveAttribute(
      "href",
      "/privacy",
    );
  });

  it("says the organisation on a submission stays with it after the account closes", async () => {
    stubServer({ accepted: false });
    await renderRoute("/submissions", SIGNED_IN);

    expect(
      await screen.findByText(
        "The organisation recorded on a submission stays with it after your account closes.",
      ),
    ).toBeVisible();
  });
});

describe("a failed acceptance", () => {
  // The user has not lost their place: the form stays, the box stays ticked
  // and the failure is announced by moving focus to the summary.
  it("announces the failure, keeps the box ticked and keeps the form", async () => {
    const user = userEvent.setup();
    const { calls } = stubServer({
      accepted: false,
      onPost: () => ({
        method: "POST",
        path: "/auth/terms/acceptance",
        status: 500,
        body: { detail: "The service could not save your acceptance." },
      }),
    });
    const { container } = await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(
      await screen.findByText("The service could not save your acceptance."),
    ).toBeVisible();
    const summary = screen.getByText("There is a problem").closest("div");
    await waitFor(() => expect(document.activeElement).toBe(summary));
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByRole("button", { name: "Accept and continue" })).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 1, name: "Accept the terms of use" }),
    ).toBeVisible();
    expect(posts(calls)).toHaveLength(1);
    await expectNoA11yViolations(container);
  });

  it("uses its own sentence when the failure carries no detail", async () => {
    const user = userEvent.setup();
    stubServer({
      accepted: false,
      onPost: () => ({
        method: "POST",
        path: "/auth/terms/acceptance",
        status: 503,
        body: {},
      }),
    });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(
      await screen.findByText("Your acceptance could not be saved. Try again."),
    ).toBeVisible();
  });

  it("can be retried after a failure", async () => {
    const user = userEvent.setup();
    let fail = true;
    const { calls } = stubServer({
      accepted: false,
      onPost: (state) => {
        if (fail) {
          return {
            method: "POST",
            path: "/auth/terms/acceptance",
            status: 500,
            body: { detail: "Try later." },
          };
        }
        state.accepted = true;
        return null;
      },
    });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));
    await screen.findByText("Try later.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    expect(posts(calls)).toHaveLength(2);
  });
});

describe("a version that changes while the user reads (NFR-47)", () => {
  it("shows the gate again under the new version, unticked, with the change announced", async () => {
    const user = userEvent.setup();
    const { calls } = stubServer({
      accepted: false,
      onPost: (state) => {
        // The terms moved on after the page loaded.
        state.version = "2026-11-01";
        return {
          method: "POST",
          path: "/auth/terms/acceptance",
          status: 409,
          body: {
            detail: "The terms have changed.",
            code: "terms_version_stale",
            current_version: "2026-11-01",
          },
        };
      },
    });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByText(/Version 2026-11-01, effective/)).toBeVisible();
    const notice = await screen.findByText(
      /The terms of use changed while you were reading/,
    );
    await waitFor(() => expect(document.activeElement).toBe(notice));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /version 2026-11-01/ })).toBeVisible();
    expect(posts(calls)).toHaveLength(1);
    expect(posts(calls)[0]!.body).toEqual({ version: "2026-10-06" });
  });

  it("then accepts the new version", async () => {
    const user = userEvent.setup();
    let firstPost = true;
    const { calls } = stubServer({
      accepted: false,
      onPost: (state) => {
        if (firstPost) {
          firstPost = false;
          state.version = "2026-11-01";
          return {
            method: "POST",
            path: "/auth/terms/acceptance",
            status: 409,
            body: {
              detail: "The terms have changed.",
              code: "terms_version_stale",
              current_version: "2026-11-01",
            },
          };
        }
        return null;
      },
    });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));
    await screen.findByText(/Version 2026-11-01, effective/);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByRole("heading", { name: /^submissions$/i })).toBeVisible();
    expect(posts(calls)[1]!.body).toEqual({ version: "2026-11-01" });
  });

  // The other 409 on this route has no code, and must read as an ordinary
  // failure, not as "the terms changed".
  it("treats a 409 without the stale code as an ordinary failure", async () => {
    const user = userEvent.setup();
    stubServer({
      accepted: false,
      onPost: () => ({
        method: "POST",
        path: "/auth/terms/acceptance",
        status: 409,
        body: { detail: "More than one account matches your sign-in." },
      }),
    });
    await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    expect(
      await screen.findByText("More than one account matches your sign-in."),
    ).toBeVisible();
    expect(screen.queryByText(/changed while you were reading/)).not.toBeInTheDocument();
  });
});
