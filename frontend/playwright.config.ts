import { defineConfig, devices } from "@playwright/test";

// The suite drives an already-running compose stack (NFR-41); it never starts
// one. docs/operations/frontend-e2e-testing.md covers the stack and the origin.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.NPTC_E2E_BASE_URL ?? "http://localhost:8081",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
