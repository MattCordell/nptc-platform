import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The bulk reclassify results panel (issue #63; FR-38, FR-39) - rendered on
 * the admin catalogue list once a batch completes, so an operator can see
 * exactly which entries were skipped and why. Driven through the real
 * `/admin/catalogue/` route, matching every other panel test in this app.
 */

const LIST_URL = "/admin/catalogue/";
const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const APPLIED_KEY = "NPTC-000001";
const UNCHANGED_KEY = "NPTC-000002";
const CONFLICT_KEY = "NPTC-000003";
const NOT_FOUND_KEY = "NPTC-000004";
const REPLACED_KEY = "NPTC-000005";

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
      entrySummary({ business_key: APPLIED_KEY, preferred_term: "Ferritin" }),
      entrySummary({ business_key: UNCHANGED_KEY, preferred_term: "Sodium" }),
      entrySummary({ business_key: CONFLICT_KEY, preferred_term: "Potassium" }),
      entrySummary({ business_key: NOT_FOUND_KEY, preferred_term: "Glucose" }),
      entrySummary({ business_key: REPLACED_KEY, preferred_term: "Calcium" }),
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
        key: "usage_guidance",
        label: "Usage guidance",
        datatype: "string",
        cardinality: "0..1",
        scope: "maintenance",
        required_for_submission: false,
        required_for_publication: false,
        binding_target: null,
        value_set_uri: null,
        strength: null,
        edition: null,
        local_code_system_key: null,
        filterable: false,
        origin: "system",
        status: "active",
        display_order: 40,
        constraints: {},
        row_version: 1,
        form_control: { control: "textarea", params: {} },
      },
    ],
  },
};

async function submitBulkReclassify() {
  const user = userEvent.setup();
  await renderRoute(LIST_URL, SIGNED_IN);
  await screen.findByRole("link", { name: APPLIED_KEY });
  await user.click(
    screen.getByRole("checkbox", { name: "Select all rows on this page" }),
  );
  await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
  const dialog = within(
    await screen.findByRole("dialog", { name: "Reclassify selected entries" }),
  );
  await user.selectOptions(dialog.getByLabelText("Property"), "usage_guidance");
  await user.type(dialog.getByLabelText("Usage guidance"), "Fasting required");
  await user.type(dialog.getByLabelText("Changelog note"), "December reclassify");
  await user.click(dialog.getByRole("button", { name: "Reclassify" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BulkOutcomeSummary", () => {
  it("shows the tallies and every skipped row, with a real conflict's who/when/diff", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 200,
        body: {
          outcomes: [
            { business_key: APPLIED_KEY, status: "applied", row_version: 2 },
            { business_key: UNCHANGED_KEY, status: "unchanged", row_version: 1 },
            {
              business_key: CONFLICT_KEY,
              status: "conflict",
              row_version: 3,
              conflict: {
                detail: "The submitted expected_row_version no longer matches.",
                business_key: CONFLICT_KEY,
                expected_row_version: 1,
                current_row_version: 3,
                conflicts: [
                  {
                    field: "usage_guidance",
                    submitted: "Fasting required",
                    current: "Any",
                  },
                ],
                changed_by: "a.reviewer",
                changed_at: "2026-09-08T09:00:00Z",
              },
            },
            { business_key: NOT_FOUND_KEY, status: "not-found", row_version: null },
            {
              business_key: REPLACED_KEY,
              status: "conflict",
              row_version: 1,
              conflict: {
                detail: "The submitted expected_row_version no longer matches.",
                business_key: REPLACED_KEY,
                expected_row_version: 1,
                current_row_version: 1,
                conflicts: [],
                changed_by: null,
                changed_at: null,
              },
            },
          ],
          applied: 1,
          unchanged: 1,
          conflict: 2,
          not_found: 1,
        },
      },
    ]);

    await submitBulkReclassify();

    const panel = within(
      screen.getByRole("region", { name: "Reclassify Usage guidance: results" }),
    );
    expect(
      panel.getByText("1 applied, 1 unchanged, 2 conflicts, 1 not found."),
    ).toBeInTheDocument();

    // Applied and unchanged entries are not skipped rows - only the two
    // conflicts and the not-found entry get a "why" row.
    const table = within(panel.getByRole("table"));
    expect(table.queryByText(APPLIED_KEY)).not.toBeInTheDocument();
    expect(table.queryByText(UNCHANGED_KEY)).not.toBeInTheDocument();

    expect(table.getByText(NOT_FOUND_KEY)).toBeInTheDocument();
    expect(table.getByText(/No longer exists/)).toBeInTheDocument();

    expect(table.getByText(CONFLICT_KEY)).toBeInTheDocument();
    expect(table.getByText(/a\.reviewer/)).toBeInTheDocument();
    expect(table.getAllByText(/the batch sent/).length).toBeGreaterThan(0);
    expect(table.getByText(/"Fasting required"/)).toBeInTheDocument();
    expect(table.getByText(/"Any"/)).toBeInTheDocument();

    // The equal-version defensive row (an entry deleted and recreated under
    // the same business_key mid-batch) reads as its own sentence, not a
    // generic "changed by  at " conflict with nothing behind it.
    expect(table.getByText(REPLACED_KEY)).toBeInTheDocument();
    expect(
      table.getByText(
        "This entry was replaced while the change was running, so it was skipped.",
      ),
    ).toBeInTheDocument();
  });

  it("shows no skipped-rows table when every entry applied or was unchanged", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 200,
        body: {
          outcomes: [
            { business_key: APPLIED_KEY, status: "applied", row_version: 2 },
            { business_key: UNCHANGED_KEY, status: "unchanged", row_version: 1 },
          ],
          applied: 1,
          unchanged: 1,
          conflict: 0,
          not_found: 0,
        },
      },
    ]);

    await submitBulkReclassify();

    const panel = within(
      screen.getByRole("region", { name: "Reclassify Usage guidance: results" }),
    );
    expect(
      panel.getByText("1 applied, 1 unchanged, 0 conflicts, 0 not found."),
    ).toBeInTheDocument();
    expect(panel.queryByRole("table")).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 200,
        body: {
          outcomes: [
            { business_key: APPLIED_KEY, status: "applied", row_version: 2 },
            {
              business_key: NOT_FOUND_KEY,
              status: "not-found",
              row_version: null,
            },
          ],
          applied: 1,
          unchanged: 0,
          conflict: 0,
          not_found: 1,
        },
      },
    ]);

    await submitBulkReclassify();

    await expectNoA11yViolations(
      screen.getByRole("region", { name: "Reclassify Usage guidance: results" }),
    );
  });
});
