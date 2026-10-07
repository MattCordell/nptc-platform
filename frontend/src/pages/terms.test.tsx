import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/**
 * The public terms page (NFR-45, NFR-47, NFR-31). Everything shown comes from
 * the stubbed `GET /auth/terms`: the page holds no copy of the terms.
 */

const TERMS = {
  version: "2026-10-06",
  effective_date: "2026-10-06",
  text: "# NPTC terms of use\n\n**Temporary text.** Not legal wording.\n\n## 1. Using the platform\n\nYou may read the catalogue without an account.",
  accepted: false,
};

function main() {
  return within(screen.getByRole("main"));
}

describe("terms page", () => {
  it("renders the served text, version and effective date under one h1", async () => {
    stubApi([{ method: "GET", path: "/auth/terms", status: 200, body: TERMS }]);
    const { container } = await renderRoute("/terms");

    expect(
      await screen.findByRole("heading", { level: 2, name: "NPTC terms of use" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Terms of use");
    expect(
      main().getByRole("heading", { level: 3, name: "1. Using the platform" }),
    ).toBeInTheDocument();
    expect(main().getByText(/Version 2026-10-06, effective/)).toBeVisible();
    expect(main().getByText("6 October 2026")).toBeVisible();
    expect(main().getByText("Temporary text.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows the text from the API and not a copy held in the app", async () => {
    stubApi([
      {
        method: "GET",
        path: "/auth/terms",
        status: 200,
        body: { ...TERMS, version: "2027-01-01", text: "# Replaced wording" },
      },
    ]);
    await renderRoute("/terms");

    expect(
      await screen.findByRole("heading", { level: 2, name: "Replaced wording" }),
    ).toBeInTheDocument();
    expect(main().getByText(/Version 2027-01-01/)).toBeVisible();
  });

  it("does not say the terms were accepted to an anonymous reader", async () => {
    stubApi([
      {
        method: "GET",
        path: "/auth/terms",
        status: 200,
        body: { ...TERMS, accepted: true },
      },
    ]);
    await renderRoute("/terms");

    await screen.findByRole("heading", { level: 2, name: "NPTC terms of use" });
    expect(main().queryByText(/You have accepted/)).not.toBeInTheDocument();
  });

  it("tells a signed-in user who accepted this version so, in words", async () => {
    stubApi([
      {
        method: "GET",
        path: "/auth/terms",
        status: 200,
        body: { ...TERMS, accepted: true },
      },
    ]);
    await renderRoute("/terms", { auth: { status: "signed-in" } });

    expect(
      await main().findByText("You have accepted this version of the terms."),
    ).toBeVisible();
  });

  it("does not claim acceptance for a signed-in user who has not accepted", async () => {
    stubApi([{ method: "GET", path: "/auth/terms", status: 200, body: TERMS }]);
    await renderRoute("/terms", { auth: { status: "signed-in" } });

    await screen.findByRole("heading", { level: 2, name: "NPTC terms of use" });
    expect(main().queryByText(/You have accepted/)).not.toBeInTheDocument();
  });

  it("keeps the h1 and announces the failure when the terms cannot be loaded", async () => {
    stubApi([
      {
        method: "GET",
        path: "/auth/terms",
        status: 500,
        body: { detail: "Database is down." },
      },
    ]);
    const { container } = await renderRoute("/terms");

    expect(await screen.findByRole("alert")).toHaveTextContent("Database is down.");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Terms of use");
    await expectNoA11yViolations(container);
  });

  it("falls back to its own sentence when the failure has no detail", async () => {
    stubApi([{ method: "GET", path: "/auth/terms", status: 503, body: {} }]);
    await renderRoute("/terms");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The terms of use could not be loaded.",
    );
  });
});
