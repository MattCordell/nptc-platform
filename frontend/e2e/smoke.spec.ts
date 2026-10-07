import { expect, test, type Page } from "@playwright/test";

import { expectNoA11yViolations } from "./axe.ts";

// Real-browser smoke suite against the compose stack (NFR-41), with colour
// contrast checked by axe on every page (NFR-31).

// The header shows an inert placeholder while the session check runs and the
// "Sign in" link once it reports signed out, so axe sees the settled page. A
// "Sign-in unavailable" header fails here, which is the clearer failure.
async function waitForSessionCheck(page: Page): Promise<void> {
  await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
}

test("landing page renders and has no axe violations", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "NPTC Catalogue Maintenance Platform" }),
  ).toBeVisible();
  await waitForSessionCheck(page);
  await expectNoA11yViolations(page);
});

test("sign-in redirects to the Keycloak authorisation endpoint", async ({
  page,
  baseURL,
}) => {
  // An anonymous cold load waits out the silent-session probe before the
  // redirect starts, so this test needs more than the default budget.
  test.setTimeout(60_000);
  await page.goto("/sign-in");
  await page.waitForURL(/\/realms\/[^/]+\/protocol\/openid-connect\/auth/, {
    waitUntil: "commit",
    timeout: 45_000,
  });

  const url = new URL(page.url());
  expect(url.searchParams.get("client_id")).toBe("nptc-frontend");
  expect(url.searchParams.get("code_challenge")).toBeTruthy();
  // The app derives redirect_uri from the page origin, so this checks the app,
  // not the stack's NPTC_FRONTEND_BASE_URL.
  expect(url.searchParams.get("redirect_uri")).toBe(
    new URL("/auth/callback", baseURL).href,
  );
  // Keycloak shows its login form only for a redirect_uri it has registered. A
  // mismatch with NPTC_FRONTEND_BASE_URL shows an error page instead.
  await expect(page.getByRole("textbox", { name: /username/i })).toBeVisible();
});

test("stub page renders and has no axe violations", async ({ page }) => {
  await page.goto("/about");
  await expect(
    page.getByRole("heading", { level: 1, name: "About the catalogue" }),
  ).toBeVisible();
  await waitForSessionCheck(page);
  await expectNoA11yViolations(page);
});

test("not-found page renders and has no axe violations", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(
    page.getByRole("heading", { level: 1, name: "We couldn't find that page" }),
  ).toBeVisible();
  await waitForSessionCheck(page);
  await expectNoA11yViolations(page);
});
