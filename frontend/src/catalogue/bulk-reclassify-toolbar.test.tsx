import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The bulk reclassify launch point on the admin catalogue list (issue #63,
 * FR-38, FR-39). Driven through the real `/admin/catalogue/` route, matching
 * every other panel test in this app.
 */

const LIST_URL = "/admin/catalogue/";
const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const DRAFT_KEY = "NPTC-000901";
const ACTIVE_KEY = "NPTC-000247";

function entrySummary(overrides: Record<string, unknown>) {
  return {
    business_key: "NPTC-000000",
    preferred_term: "Placeholder",
    length: 11,
    status: "active",
    specimen_unconstrained: false,
    updated_at: "2026-09-01T04:30:00Z",
    has_open_finding: false,
    label_provenance: { preferred_term: { source: "catalogue" } },
    row_version: 1,
    ...overrides,
  };
}

const ENTRIES_OK: Route = {
  method: "GET",
  path: "/catalogue/admin/entries",
  status: 200,
  body: {
    items: [
      entrySummary({
        business_key: DRAFT_KEY,
        preferred_term: "Ferritin",
        status: "draft",
      }),
      entrySummary({
        business_key: ACTIVE_KEY,
        preferred_term: "Full blood count",
        status: "active",
      }),
    ],
    next_cursor: null,
  },
};

const PROPERTIES_OK: Route = {
  method: "GET",
  path: "/registry/properties",
  status: 200,
  body: {
    items: [
      {
        key: "discipline",
        label: "Discipline",
        datatype: "code",
        cardinality: "0..*",
        scope: "both",
        required_for_submission: false,
        required_for_publication: false,
        binding_target: "local_code_system",
        value_set_uri: null,
        strength: "required",
        edition: null,
        local_code_system_key: "discipline",
        filterable: true,
        origin: "system",
        status: "active",
        display_order: 10,
        constraints: {},
        row_version: 1,
        form_control: { control: "concept_picker", params: {} },
      },
    ],
  },
};

const DISCIPLINE_VALUES_OK: Route = {
  method: "GET",
  path: "/registry/properties/discipline/values",
  status: 200,
  body: { items: [{ code: "chemistry", display: "Chemistry" }], total: 1 },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BulkReclassifyToolbar", () => {
  it("does not render until at least one row is selected", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    expect(
      screen.queryByRole("button", { name: "Reclassify selected" }),
    ).not.toBeInTheDocument();
  });

  it("shows the selection count and a launch button once a row is selected", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = userEvent.setup();

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });
    await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));

    expect(screen.getByText("1 entry selected.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reclassify selected" }),
    ).toBeInTheDocument();
  });

  it("pluralises the count for more than one row", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = userEvent.setup();

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });
    await user.click(
      screen.getByRole("checkbox", { name: "Select all rows on this page" }),
    );

    expect(screen.getByText("2 entries selected.")).toBeInTheDocument();
  });

  it("opens the bulk reclassify dialog when the launch button is clicked", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = userEvent.setup();

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });
    await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
    await user.click(screen.getByRole("button", { name: "Reclassify selected" }));

    expect(
      await screen.findByRole("dialog", { name: "Reclassify selected entries" }),
    ).toBeInTheDocument();
  });

  it("has no accessibility violations once shown", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = userEvent.setup();

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });
    await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));

    await expectNoA11yViolations(screen.getByRole("group", { name: "Bulk reclassify" }));
  });
});
