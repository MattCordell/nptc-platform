import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";

/**
 * What `/sign-in`, `/auth/callback`, `/sign-out` and `/register` do for each
 * session status (issue #41).
 *
 * These render the production route table, so they also prove the four
 * routes #146 reserved as placeholders now resolve to real screens at the
 * same paths.
 */

const LANDING_LINK = { name: "Back to the landing page" };

describe("/sign-in", () => {
  it("shows the redirect screen with a way home, and no axe violations", async () => {
    const { container } = await renderRoute("/sign-in", {
      auth: { status: "signed-out" },
    });

    expect(
      await screen.findByRole("heading", { level: 1, name: "Taking you to sign in" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toHaveAttribute("href", "/");
    await expectNoA11yViolations(container);
  });

  it("starts the redirect to Keycloak for a signed-out visitor", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-in", { auth: { status: "signed-out", signIn } });

    await waitFor(() => {
      expect(signIn).toHaveBeenCalledTimes(1);
    });
  });

  it("passes the redirect target through from the search params", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-in?redirect=%2Fsubmissions", {
      auth: { status: "signed-out", signIn },
    });

    await waitFor(() => {
      expect(signIn).toHaveBeenCalledWith({ redirect: "/submissions" });
    });
  });

  it("drops an off-site redirect rather than following it", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    // `search-params.ts` rejects anything that is not a single-slash
    // internal path; this asserts the sign-in page honours that rather than
    // reaching for the raw query string. An open redirect here would let a
    // crafted sign-in link bounce a freshly authenticated user off-site.
    await renderRoute("/sign-in?redirect=https%3A%2F%2Fevil.test%2Fsteal", {
      auth: { status: "signed-out", signIn },
    });

    await waitFor(() => {
      expect(signIn).toHaveBeenCalledWith({ redirect: undefined });
    });
  });

  it("waits instead of redirecting while the session is still restoring", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-in", { auth: { status: "restoring", signIn } });

    expect(
      await screen.findByRole("heading", { name: /checking your session/i }),
    ).toBeInTheDocument();
    // Starting an interactive redirect here would discard a session that is
    // about to restore.
    expect(signIn).not.toHaveBeenCalled();
  });

  it("does not redirect a visitor who is already signed in", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-in", { auth: { status: "signed-in", signIn } });

    expect(
      await screen.findByRole("heading", { name: /already signed in/i }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("says so, without looping, when the provider is unreachable", async () => {
    const signIn = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-in", { auth: { status: "unavailable", signIn } });

    expect(
      await screen.findByRole("heading", { name: /sign-in is unavailable/i }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });
});

describe("/auth/callback", () => {
  it("completes the exchange and continues to the stored destination", async () => {
    const completeCallback = vi.fn(() => Promise.resolve("/catalogue"));

    // A public destination: the harness's session status is fixed, so a
    // gated one would be bounced onward by `RequireAuth` and this would be
    // asserting that redirect instead of the callback's own navigation.
    const { router } = await renderRoute("/auth/callback?code=abc&state=xyz", {
      auth: { status: "signed-out", completeCallback },
    });

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/catalogue");
    });
    // The spent code must not stay in history as a back-button target.
    expect(router.history.length).toBe(1);
  });

  it("runs the exchange exactly once under StrictMode", async () => {
    // The exchange is single-use by design; a second invocation from
    // StrictMode's double-mounted effect would always fail and report a
    // sign-in that actually worked as broken.
    const completeCallback = vi.fn(() => Promise.resolve("/"));

    await renderRoute("/auth/callback?code=abc&state=xyz", {
      auth: { status: "signed-out", completeCallback },
    });

    await waitFor(() => {
      expect(completeCallback).toHaveBeenCalledTimes(1);
    });
  });

  it("explains what to do when the callback is refused", async () => {
    const completeCallback = vi.fn(() => Promise.resolve(null));

    await renderRoute("/auth/callback?code=abc&state=wrong", {
      auth: { status: "signed-out", completeCallback },
    });

    const heading = await screen.findByRole("heading", {
      name: /sign-in could not be completed/i,
    });
    expect(heading).toBeInTheDocument();
    // Errors say what to do next, never what the server said (§17.2).
    expect(document.body.textContent).not.toMatch(/invalid_grant|HTTP \d{3}/);
  });

  it("offers a way back to sign-in and home from the failed screen, with no axe violations", async () => {
    const completeCallback = vi.fn(() => Promise.resolve(null));

    const { container } = await renderRoute("/auth/callback?code=abc&state=wrong", {
      auth: { status: "signed-out", completeCallback },
    });

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Sign-in could not be completed",
      }),
    ).toHaveAttribute("id", "callback-heading");
    const main = within(screen.getByRole("main"));
    expect(main.getByRole("link", { name: "Go to the sign-in page" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
    expect(main.getByRole("link", LANDING_LINK)).toHaveAttribute("href", "/");
    await expectNoA11yViolations(container);
  });
});

describe("/sign-out", () => {
  it("ends the session for a signed-in user", async () => {
    const signOut = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-out", { auth: { status: "signed-in", signOut } });

    await waitFor(() => {
      expect(signOut).toHaveBeenCalledTimes(1);
    });
  });

  it("confirms the signed-out state without calling sign-out again", async () => {
    const signOut = vi.fn(() => Promise.resolve());

    await renderRoute("/sign-out", { auth: { status: "signed-out", signOut } });

    expect(
      await screen.findByRole("heading", { name: /you are signed out/i }),
    ).toBeInTheDocument();
    expect(signOut).not.toHaveBeenCalled();
  });

  it("offers a way home and on to the catalogue once signed out, with no axe violations", async () => {
    const { container } = await renderRoute("/sign-out", {
      auth: { status: "signed-out" },
    });

    expect(
      await screen.findByRole("heading", { level: 1, name: "You are signed out" }),
    ).toHaveAttribute("id", "sign-out-heading");
    const main = within(screen.getByRole("main"));
    expect(main.getByRole("link", LANDING_LINK)).toHaveAttribute("href", "/");
    expect(main.getByRole("link", { name: "Search the catalogue" })).toHaveAttribute(
      "href",
      "/catalogue",
    );
    await expectNoA11yViolations(container);
  });
});

describe("/register", () => {
  it("hands off to Keycloak's registration page", async () => {
    const register = vi.fn(() => Promise.resolve());

    await renderRoute("/register", { auth: { status: "signed-out", register } });

    await waitFor(() => {
      expect(register).toHaveBeenCalledTimes(1);
    });
  });

  it("waits while the session is still restoring", async () => {
    const register = vi.fn(() => Promise.resolve());

    await renderRoute("/register", { auth: { status: "restoring", register } });

    expect(register).not.toHaveBeenCalled();
  });

  it("tells an already-signed-in user there is nothing to do", async () => {
    const register = vi.fn(() => Promise.resolve());

    await renderRoute("/register", { auth: { status: "signed-in", register } });

    expect(
      await screen.findByRole("heading", { name: /already have an account/i }),
    ).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it("shows the redirect screen with a way home, and no axe violations", async () => {
    const { container } = await renderRoute("/register", {
      auth: { status: "signed-out" },
    });

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Taking you to registration",
      }),
    ).toHaveAttribute("id", "register-heading");
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toHaveAttribute("href", "/");
    await expectNoA11yViolations(container);
  });

  it("says so, with a way home and without looping, when the provider is unreachable", async () => {
    const register = vi.fn(() => Promise.resolve());

    await renderRoute("/register", { auth: { status: "unavailable", register } });

    expect(
      await screen.findByRole("heading", { name: /registration is unavailable/i }),
    ).toHaveAttribute("id", "register-heading");
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it("gives an already-signed-in user a way home", async () => {
    await renderRoute("/register", { auth: { status: "signed-in" } });

    await screen.findByRole("heading", { name: /already have an account/i });
    expect(
      within(screen.getByRole("main")).getByRole("link", LANDING_LINK),
    ).toBeInTheDocument();
  });
});

describe("keyboard access to the way out of a stuck auth screen", () => {
  /** Tabs from the top of the page until focus lands inside `main`. */
  async function tabIntoMain(user: ReturnType<typeof userEvent.setup>) {
    const main = screen.getByRole("main");
    for (let i = 0; i < 30 && !main.contains(document.activeElement); i += 1) {
      await user.tab();
    }
    expect(main.contains(document.activeElement)).toBe(true);
  }

  it("reaches the sign-in link, then the landing link, on a failed callback", async () => {
    const user = userEvent.setup();
    const completeCallback = vi.fn(() => Promise.resolve(null));
    const { router } = await renderRoute("/auth/callback?code=abc&state=wrong", {
      auth: { status: "signed-out", completeCallback },
    });
    await screen.findByRole("heading", { name: /sign-in could not be completed/i });

    await tabIntoMain(user);
    expect(document.activeElement).toBe(
      screen.getByRole("link", { name: "Go to the sign-in page" }),
    );
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", LANDING_LINK));

    await user.tab({ shift: true });
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/sign-in");
    });
  });

  it("reaches the landing link, then the catalogue link, once signed out", async () => {
    const user = userEvent.setup();
    const { router } = await renderRoute("/sign-out", {
      auth: { status: "signed-out" },
    });
    await screen.findByRole("heading", { name: /you are signed out/i });

    await tabIntoMain(user);
    expect(document.activeElement).toBe(screen.getByRole("link", LANDING_LINK));
    await user.tab();
    expect(document.activeElement).toBe(
      within(screen.getByRole("main")).getByRole("link", {
        name: "Search the catalogue",
      }),
    );
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/catalogue");
    });
  });
});
