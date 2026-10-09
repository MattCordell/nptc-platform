import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The designation edit screen (issue #149; FR-04, FR-05, FR-24, FR-36, FR-38).
 *
 * Every test drives the real router at the real URL, so what is under test is
 * the shipped route as well as the page.
 */

const BUSINESS_KEY = "NPTC-000247";
const EDIT_URL = `/admin/catalogue/${BUSINESS_KEY}/edit`;

const ENTRY = {
  business_key: BUSINESS_KEY,
  preferred_term: "Ferritin",
  // Deliberately not `"Ferritin".length`: FR-85's figure is computed by the
  // server from the *cleaned* term, and a test that derived it here would
  // stop being able to tell a rendered server value from a recomputed one.
  length: 8,
  status: "draft",
  updated_at: "2026-09-01T04:30:00Z",
  row_version: 3,
  designations: [
    {
      term: "Serum ferritin",
      status: "active",
      length: 14,
    },
    // A retired synonym The admin read route serves retired
    // designations alongside active ones, unlike the public route, so the
    // form has to list one with its Reinstate action.
    {
      term: "Obsolete ferritin note",
      status: "retired",
      length: 22,
    },
  ],
  bindings: [],
  properties: [],
};

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const READ_OK: Route = {
  method: "GET",
  path: `/catalogue/admin/entries/${BUSINESS_KEY}`,
  status: 200,
  body: ENTRY,
};

const ADD_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations`;
const AMEND_PATH = `${ADD_PATH}/amendment`;

async function renderLoaded() {
  const rendered = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByRole("heading", { name: "Ferritin", level: 1 });
  return rendered;
}

function callsTo(
  calls: { method: string; path: string; body: unknown; text: string }[],
  path: string,
) {
  return calls.filter((call) => call.method === "POST" && call.path.endsWith(path));
}

/** Reads of the entry, for asserting that something refetched it. */
function readsOf(calls: { method: string; path: string }[]) {
  return calls.filter(
    (call) => call.method === "GET" && call.path.endsWith(READ_OK.path),
  );
}

/**
 * Everything the page's live regions currently say. There are two - the
 * page owns one for a failed refresh, and the edit form owns one for the
 * outcome of its own writes - and both are mounted from the first render,
 * which is the point of `LiveRegion`. A test cares
 * what was announced, not which region carried it.
 */
function announced(): string {
  return screen
    .getAllByRole("status")
    .map((region) => region.textContent ?? "")
    .join(" ");
}

/**
 * `getByText`/`findByText`'s `ignore` option for excluding the page's own
 * live region(s) - #288 announces the same wording it renders for a hard
 * load failure, so an unscoped query can match both the rendered element and
 * the announcement. Extends Testing Library's own default (`script, style`)
 * rather than replacing it, and is coupled to `LiveRegion` putting its
 * message text directly on the role-bearing element (review nit): it would
 * stop excluding the announcement if `LiveRegion` ever wrapped its message in
 * an inner element instead.
 */
const NOT_LIVE_REGION = { ignore: 'script, style, [role="status"], [role="alert"]' };

/** Queries scoped to the edit form, for the same reason as `inTermsPanel()`. */
function inForm() {
  return within(screen.getByRole("region", { name: "Edit entry" }));
}

/** Changes the preferred term in the form and saves it, to drive a write through the page. */
async function renameViaForm(user: ReturnType<typeof userEvent.setup>, note: string) {
  await user.type(inForm().getByLabelText("RCPA Preferred"), " renamed");
  await user.type(inForm().getByLabelText("Changelog note"), note);
  await user.click(inForm().getByRole("button", { name: "Save" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the entry it loads", () => {
  it("reads through the admin route, so a draft entry can be edited at all", async () => {
    // The public detail route 404s a draft entry identically to a key that was
    // never minted (#142), so loading through it would make this screen
    // unusable for exactly the entries most likely to need editing.
    const calls = stubApi([READ_OK]);

    await renderLoaded();

    // Not `calls[0]`: `AdminLayout`'s `StepUpBanner` (issue #184) fires its
    // own `GET /auth/me` on every admin route, racing this read, so the
    // entry read is no longer guaranteed to be the first call recorded.
    expect(
      calls.some(
        (call) =>
          call.method === "GET" &&
          call.path === `/api/v1/catalogue/admin/entries/${BUSINESS_KEY}`,
      ),
    ).toBe(true);
    expect(screen.getByText("Entry status").nextElementSibling).toHaveTextContent(
      "Draft",
    );
  });

  it("states the preferred-term length as text, with no control to edit it (FR-24, FR-85)", async () => {
    // The figure beside the term is text, and nothing on the screen is a
    // control for it on any code path for any role.
    stubApi([READ_OK]);

    const { container } = await renderLoaded();

    expect(inForm().getByText(/Length: 8 characters/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/length/i)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("8")).not.toBeInTheDocument();
    expect(container.querySelector("input[name*='length' i]")).toBeNull();
  });

  it("triggers the step-up dialog when the API refuses the load for want of MFA", async () => {
    // `catalogue.edit_published` is MFA-gated. Before issue #184 this landed
    // on a hand-written "sign out and sign in again" paragraph; now the
    // step-up controller (mounted app-wide in `RootLayout`) reacts to the
    // RFC 9470 challenge itself, and the default test `stepUp` stub resolves
    // `"interaction-required"` (`render-route.tsx`), so this exercises the
    // interactive fallback.
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    expect(
      await screen.findByRole("dialog", { name: "Sign in again to continue" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Sign out and sign in again/)).not.toBeInTheDocument();
  });

  it("carries the challenge's acr_values and the current path into the interactive fallback", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn().mockResolvedValue(undefined);
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, signIn } });
    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(signIn).toHaveBeenCalledWith({ acrValues: "2", redirect: EDIT_URL });
  });

  it("abandoning the interactive fallback leaves a usable screen and never signs out", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn().mockResolvedValue(undefined);
    const signOut = vi.fn().mockResolvedValue(undefined);
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, signIn, signOut } });
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
    expect(signOut).not.toHaveBeenCalled();
    // Still on the same screen, with the refusal's own text visible - not a
    // blank page and not bounced anywhere. `NOT_LIVE_REGION` excludes the
    // announcement (#288 now announces this same hard failure too).
    expect(
      screen.getByText(
        "This action requires multi-factor authentication.",
        NOT_LIVE_REGION,
      ),
    ).toBeInTheDocument();
  });

  it("retries the read in place after a silent step-up succeeds, with no dialog", async () => {
    const stepUp = vi.fn().mockResolvedValue("done");
    const calls = stubApi([READ_OK], {
      vary: (call, priorSameCalls) => {
        if (call.method !== "GET" || !call.path.endsWith(READ_OK.path)) {
          return null;
        }
        // StrictMode double-mounts the load itself, so the first two reads
        // are the ones that hit the MFA-gated route unsatisfied; every read
        // from the third onward is the retried one, after step-up.
        return priorSameCalls < 2
          ? {
              ...READ_OK,
              status: 403,
              body: { detail: "This action requires multi-factor authentication." },
              headers: {
                "WWW-Authenticate":
                  'Bearer error="insufficient_user_authentication", acr_values="2"',
              },
            }
          : null;
      },
    });

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });

    expect(
      await screen.findByRole("heading", { name: "Ferritin", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(stepUp).toHaveBeenCalledWith("2");
    expect(readsOf(calls).length).toBeGreaterThanOrEqual(3);
  });

  it("does not repeat the step-up attempt once one has run for this read", async () => {
    // Every response 403s with the same challenge - without the
    // retry-once guard, a silent step-up that "succeeds" but still does
    // not satisfy the server would retry forever.
    const stepUp = vi.fn().mockResolvedValue("done");
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });

    expect(
      await screen.findByText("This action requires multi-factor authentication."),
    ).toBeInTheDocument();
    expect(stepUp).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not permanently block a later, genuine step-up challenge for the same read", async () => {
    // PR #284 review: the retry-once guard must protect only the immediate
    // retry cycle, not this query's entire lifetime - a session that steps
    // up once and later needs to again (a second, unrelated MFA
    // requirement; the realm's loa-max-age elapsing) must still be offered
    // step-up, not silently swallowed by a guard entry nothing ever cleared.
    const user = userEvent.setup();
    const stepUp = vi.fn().mockResolvedValue("done");
    const CHALLENGE = {
      status: 403,
      body: { detail: "This action requires multi-factor authentication." },
      headers: {
        "WWW-Authenticate":
          'Bearer error="insufficient_user_authentication", acr_values="2"',
      },
    };
    const CONFLICT = {
      method: "POST",
      path: AMEND_PATH,
      status: 409,
      body: {
        detail: "This entry was changed by someone else since you loaded it.",
        business_key: BUSINESS_KEY,
        expected_row_version: 3,
        current_row_version: 4,
        conflicts: [],
        changed_by: "A Curator",
        changed_at: "2026-09-02T01:00:00Z",
      },
    };
    // First cycle: the load itself 403s (twice, under StrictMode) and the
    // step-up's own retry succeeds. Second cycle: the amend's version
    // conflict forces a refetch of the same read, which 403s again - a
    // fresh challenge for the same `queryHash`, well after the first
    // cycle's guard entry should have been cleared.
    let amended = false;
    stubApi([READ_OK, CONFLICT], {
      vary: (call, priorSameCalls) => {
        if (call.method === "POST") {
          amended = true;
          return null;
        }
        if (!call.path.endsWith(READ_OK.path)) {
          return null;
        }
        if (amended) {
          return { ...READ_OK, ...CHALLENGE };
        }
        return priorSameCalls < 2 ? { ...READ_OK, ...CHALLENGE } : null;
      },
    });

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });
    await screen.findByRole("heading", { name: "Ferritin", level: 1 });
    expect(stepUp).toHaveBeenCalledTimes(1);

    await renameViaForm(user, "Rename the entry");

    await waitFor(() => expect(stepUp).toHaveBeenCalledTimes(2));
  });

  it("does not trigger step-up for a plain 403 with no challenge header", async () => {
    // FR-44's own negative-case rule, applied to step-up: an ordinary
    // missing-permission refusal must not be sent through the step-up loop
    // just because it happens to be a 403.
    const stepUp = vi.fn().mockResolvedValue("done");
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "You do not have permission to do this." },
      },
    ]);

    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });

    // `NOT_LIVE_REGION`: #288 also announces this hard failure, so an
    // unscoped query can match both once the announcement's own
    // setTimeout(0) has fired.
    expect(
      await screen.findByText("You do not have permission to do this.", NOT_LIVE_REGION),
    ).toBeInTheDocument();
    expect(stepUp).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names the identifier when there is no such entry", async () => {
    stubApi([
      { ...READ_OK, status: 404, body: { detail: "No catalogue entry was found." } },
    ]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    expect(
      await screen.findByText(
        new RegExp(`No catalogue entry was found for ${BUSINESS_KEY}`),
        NOT_LIVE_REGION,
      ),
    ).toBeInTheDocument();
    // #288: the initial-load failure was rendered but never announced -
    // silence for a screen-reader user whose first load 404s.
    await waitFor(() =>
      expect(announced()).toMatch(
        new RegExp(`No catalogue entry was found for ${BUSINESS_KEY}`),
      ),
    );
  });

  it("shows and announces a refusal message when the initial load fails for any other reason", async () => {
    // #288, mirroring PR #285 review finding 3 on the list screen: a
    // non-404 initial-load failure (no prior data to fall back on) must be
    // both rendered and announced.
    stubApi([{ ...READ_OK, status: 500, body: { detail: "boom" } }]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    expect(await screen.findByText("boom", NOT_LIVE_REGION)).toBeInTheDocument();
    await waitFor(() => expect(announced()).toContain("boom"));
  });

  it("falls back to a generic message when the initial-load refusal carries no detail sentence", async () => {
    // Review finding 2: `loadFailureMessage`'s `??` fallback arm was not
    // exercised by any test - the 404 case takes the first branch, and the
    // 500 case above supplies a string `detail`, so `refusalDetail` wins
    // there too. `refusalDetail` refuses a non-string `detail`, which is
    // exactly what FastAPI's own validation error sends (`HTTPValidationError`
    // - an array of issues, not a sentence) - the principal failure mode of
    // this branch, per CLAUDE.md's testing conventions.
    stubApi([
      {
        ...READ_OK,
        status: 422,
        body: { detail: [{ msg: "bad request", loc: ["query"], type: "value_error" }] },
      },
    ]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    const message = `${BUSINESS_KEY} could not be loaded. Try again, or contact an administrator if the problem persists.`;
    expect(await screen.findByText(message, NOT_LIVE_REGION)).toBeInTheDocument();
    await waitFor(() => expect(announced()).toContain(message));
  });

  it("keeps the editor on screen when a refresh fails, and says so", async () => {
    // `isError` and `data` are not exclusive states. Before this, an entry
    // that loaded and then failed a refetch rendered "You cannot edit this
    // entry with your current sign-in" directly above a working form -
    // and the amend mutation's conflict refetch makes that a designed-in path
    // (PR #238 review).
    const user = userEvent.setup();
    const CONFLICT = {
      method: "POST",
      path: AMEND_PATH,
      status: 409,
      body: {
        detail: "This entry was changed by someone else since you loaded it.",
        business_key: BUSINESS_KEY,
        expected_row_version: 3,
        current_row_version: 4,
        conflicts: [],
        changed_by: "A Curator",
        changed_at: "2026-09-02T01:00:00Z",
      },
    };
    // The read succeeds once and is refused after that - a session expiring
    // while the screen sits open, which is the same session length that makes
    // a version conflict likely in the first place. Re-stubbing `fetch`
    // mid-test would not do it: the API client holds the reference it was
    // created with.
    // Reads are refused from the amendment onwards, not from the second read
    // onwards: StrictMode double-mounts, so the load itself is two reads.
    let expired = false;
    const calls = stubApi([READ_OK, CONFLICT], {
      vary: (call) => {
        if (call.method === "POST") {
          expired = true;
          return null;
        }
        return expired
          ? { ...READ_OK, status: 403, body: { detail: "Step-up required." } }
          : null;
      },
    });
    await renderLoaded();
    // Captured after the load, which is two reads under StrictMode, so the
    // assertion below is about the refetch and not about the load (PR #238
    // review).
    const readsBefore = readsOf(calls).length;

    await renameViaForm(user, "Rename the entry");

    expect(
      await screen.findByText(/could not be refreshed just now/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Sign out and sign in again/)).not.toBeInTheDocument();
    // Announced, not just shown: the reader who cannot see the banner appear
    // is the one it exists for. `LiveRegion` is the always-mounted region, so
    // the sentence is a text change inside it rather than a new element.
    await waitFor(() => expect(announced()).toMatch(/could not be refreshed just now/));
    expect(
      screen.getByRole("button", { name: "Remove Serum ferritin" }),
    ).toBeInTheDocument();
    // The refetch the conflict asked for actually went out.
    expect(readsOf(calls).length).toBeGreaterThan(readsBefore);
  });
});

describe("a write refused for want of MFA", () => {
  it("reaches step-up when the form's write is refused for want of MFA, and does not replay it", async () => {
    // The form's save run catches every failure to build its summary, so it must
    // still rethrow the one that stopped it: the mutation cache is what
    // recognises the challenge (ADR-0036).
    const user = userEvent.setup();
    const stepUp = vi.fn().mockResolvedValue("done");
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);
    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });
    await screen.findByRole("heading", { name: "Ferritin", level: 1 });

    await renameViaForm(user, "Rename the entry");

    await waitFor(() => expect(stepUp).toHaveBeenCalledWith("2"));
    // The editor's input is still there, the summary says what was not saved,
    // and nothing was sent a second time.
    expect(inForm().getByLabelText("RCPA Preferred")).toHaveValue("Ferritin renamed");
    expect(
      await screen.findByRole("heading", { name: "Some changes were not saved" }),
    ).toBeInTheDocument();
    expect(callsTo(calls, AMEND_PATH)).toHaveLength(1);
  });

  it("reaches step-up when a synonym write is refused, and does not replay it", async () => {
    // Out of scope by design (ADR-0036): a refused mutation is never replayed
    // automatically, silent step-up or not. The editor keeps their typed input.
    const user = userEvent.setup();
    const stepUp = vi.fn().mockResolvedValue("done");
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: ADD_PATH,
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);
    await renderRoute(EDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });
    await screen.findByRole("heading", { name: "Ferritin", level: 1 });

    await user.type(inForm().getByLabelText("Add synonyms"), "Zovirax");
    await user.type(inForm().getByLabelText("Changelog note"), "Add the brand name");
    await user.click(inForm().getByRole("button", { name: "Save" }));

    await waitFor(() => expect(stepUp).toHaveBeenCalledWith("2"));
    expect(inForm().getByLabelText("Add synonyms")).toHaveValue("Zovirax");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(callsTo(calls, ADD_PATH)).toHaveLength(1);
  });
});

describe("accessibility", () => {
  it("has no automated accessibility violations on the loaded screen", async () => {
    stubApi([READ_OK]);

    const { container } = await renderLoaded();

    await expectNoA11yViolations(container);
  });
});
