import { expect, test } from "@playwright/test";

import { expectNoA11yViolations } from "./axe.ts";

// Real-browser smoke suite against the compose stack (NFR-41), with colour
// contrast checked by axe on every page (NFR-31).

test("landing page renders and has no axe violations", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "NPTC Catalogue Maintenance Platform" }),
  ).toBeVisible();
  // The page renders before the session check answers; axe must see the
  // settled page, not a mid-restore one.
  await page.waitForLoadState("networkidle");
  await expectNoA11yViolations(page);
});

test("sign-in redirects to the Keycloak authorisation endpoint", async ({
  page,
  baseURL,
}) => {
  await page.goto("/sign-in");
  await page.waitForURL(/\/realms\/[^/]+\/protocol\/openid-connect\/auth/);

  const url = new URL(page.url());
  expect(url.searchParams.get("client_id")).toBe("nptc-frontend");
  expect(url.searchParams.get("code_challenge")).toBeTruthy();
  // Keycloak refuses a redirect_uri it does not know, so a mismatch between
  // NPTC_E2E_BASE_URL and the stack's NPTC_FRONTEND_BASE_URL fails here.
  expect(url.searchParams.get("redirect_uri")).toBe(`${baseURL}/auth/callback`);
  await expect(page.getByText("Invalid parameter: redirect_uri")).toHaveCount(0);
});

test("stub page renders and has no axe violations", async ({ page }) => {
  await page.goto("/about");
  await expect(
    page.getByRole("heading", { level: 1, name: "About the catalogue" }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expectNoA11yViolations(page);
});

test("not-found page renders and has no axe violations", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(
    page.getByRole("heading", { level: 1, name: "We couldn't find that page" }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expectNoA11yViolations(page);
});
