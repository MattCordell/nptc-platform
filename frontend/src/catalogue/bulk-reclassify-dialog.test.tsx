import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The bulk reclassify dialog (issue #63; FR-38, FR-39, FR-44). Driven
 * through the real `/admin/catalogue/` route, matching every other panel
 * test in this app.
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
        row_version: 3,
      }),
      entrySummary({
        business_key: ACTIVE_KEY,
        preferred_term: "Full blood count",
        status: "active",
        row_version: 7,
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
      {
        key: "retired_note",
        label: "Retired note",
        datatype: "string",
        cardinality: "0..1",
        scope: "both",
        required_for_submission: false,
        required_for_publication: false,
        binding_target: null,
        value_set_uri: null,
        strength: null,
        edition: null,
        local_code_system_key: null,
        filterable: false,
        origin: "admin",
        status: "deprecated",
        display_order: 50,
        constraints: {},
        row_version: 2,
        form_control: { control: "text", params: {} },
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

function dialog() {
  return within(screen.getByRole("dialog", { name: "Reclassify selected entries" }));
}

async function openDialogWithBothRowsSelected() {
  const user = userEvent.setup();
  await renderRoute(LIST_URL, SIGNED_IN);
  await screen.findByRole("link", { name: DRAFT_KEY });
  await user.click(
    screen.getByRole("checkbox", { name: "Select all rows on this page" }),
  );
  await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
  await screen.findByRole("dialog", { name: "Reclassify selected entries" });
  return user;
}

async function openDialogWithOneRowSelected() {
  const user = userEvent.setup();
  await renderRoute(LIST_URL, SIGNED_IN);
  await screen.findByRole("link", { name: DRAFT_KEY });
  await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
  await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
  await screen.findByRole("dialog", { name: "Reclassify selected entries" });
  return user;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("property picker", () => {
  it("lists only active property definitions, in display_order", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    await openDialogWithOneRowSelected();

    const select = dialog().getByLabelText("Property") as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((option) => option.text);
    expect(optionLabels).toEqual(["Choose a property", "Discipline", "Usage guidance"]);
    expect(optionLabels).not.toContain("Retired note");
  });

  it("resets the value slots when the property is switched", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "discipline");
    await user.selectOptions(await dialog().findByLabelText("Discipline 1"), "chemistry");
    expect((dialog().getByLabelText("Discipline 1") as HTMLSelectElement).value).toBe(
      "chemistry",
    );

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    expect(dialog().getByLabelText("Usage guidance")).toHaveValue("");

    // Switch back - if `slots` had merely been hidden rather than actually
    // reset, this would show "chemistry" selected again.
    await user.selectOptions(dialog().getByLabelText("Property"), "discipline");
    expect(
      (await dialog().findByLabelText("Discipline 1")) as HTMLSelectElement,
    ).toHaveValue("");
  });
});

describe("submit gates", () => {
  it("refuses to submit with no property chosen, making no request", async () => {
    const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = await openDialogWithOneRowSelected();

    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    expect(
      await dialog().findByText("Choose a property to reclassify."),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("/bulk/properties/"))).toBe(false);
  });

  it("refuses to submit with no changelog note, making no request", async () => {
    const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    expect(
      await dialog().findAllByText("A changelog note is required."),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.path.includes("/bulk/properties/"))).toBe(false);
  });

  // FR-39's own cap (ADR-0035's `_MAX_BULK_ENTRIES`) - composed into `Form`'s
  // single submitBlocked/blockedReason pair ahead of the changelog note gate.
  it("blocks submit over the 100-entry cap, unlinked, making no request", async () => {
    const manyItems = Array.from({ length: 101 }, (_, index) =>
      entrySummary({
        business_key: `NPTC-${String(index + 1).padStart(6, "0")}`,
        preferred_term: `Entry ${index + 1}`,
      }),
    );
    const calls = stubApi([
      {
        method: "GET",
        path: "/catalogue/admin/entries",
        status: 200,
        body: {
          items: manyItems,
          next_cursor: null,
        },
      },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);
    const user = userEvent.setup();
    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: "NPTC-000001" });
    await user.click(
      screen.getByRole("checkbox", { name: "Select all rows on this page" }),
    );
    await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
    await screen.findByRole("dialog", { name: "Reclassify selected entries" });

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(dialog().getByLabelText("Changelog note"), "Reclassify everything");
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    expect(await dialog().findByText(/Select 100 or fewer entries/)).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("/bulk/properties/"))).toBe(false);
  });
});

describe("submitting", () => {
  it("PUTs the whole selection, the values and the note to the bulk route", async () => {
    const calls = stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 200,
        body: {
          outcomes: [{ business_key: DRAFT_KEY, status: "applied", row_version: 4 }],
          applied: 1,
          unchanged: 0,
          conflict: 0,
          not_found: 0,
        },
      },
    ]);
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(
      dialog().getByLabelText("Changelog note"),
      "December discipline reclassify",
    );
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) =>
        call.method === "POST" &&
        call.path === "/api/v1/catalogue/entries/bulk/properties/usage_guidance",
    );
    expect(write?.body).toEqual({
      values: [{ value: "Fasting required", justification: null }],
      reason: "December discipline reclassify",
      entries: [{ business_key: DRAFT_KEY, expected_row_version: 3 }],
    });
    // The list's own selection is cleared and a results summary announced -
    // see admin-catalogue-list.test.tsx for the dedicated coverage of that.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Reclassify Usage guidance: 1 applied",
    );
  });

  // FR-89's specimen cross-field check is the concrete case, but any
  // whole-batch abort reaches the dialog identically: a group-level
  // PropertyValidationResponse issue (`ordinal: null`), never a per-entry
  // outcome. Nothing applied, including any entry that would have
  // succeeded - so no results panel, and the dialog stays open with the
  // operator's input intact.
  it("shows a whole-batch validation abort as a group-level error, dialog open, no results panel", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 422,
        body: {
          detail: "One or more values failed validation.",
          issues: [
            {
              property_key: "usage_guidance",
              label: "Usage guidance",
              code: "cross-field-conflict",
              message: "This value conflicts with an entry's own setting.",
              ordinal: null,
            },
          ],
        },
      },
    ]);
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(
      dialog().getByLabelText("Changelog note"),
      "Try a cross-field conflict",
    );
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    expect(
      await dialog().findAllByText("This value conflicts with an entry's own setting."),
    ).not.toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(dialog().getByLabelText("Usage guidance")).toHaveValue("Fasting required");
    expect(
      screen.queryByRole("heading", { name: /reclassify.*results/i }),
    ).not.toBeInTheDocument();
  });

  // FR-44's own negative case, applied to step-up: a refused mutation is
  // never replayed automatically (ADR-0036) - the operator keeps their
  // input and resubmits once MFA is satisfied.
  it("does not replay a refused write even after a successful silent step-up", async () => {
    const stepUp = vi.fn().mockResolvedValue("done");
    const calls = stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 403,
        body: { detail: "This action requires multi-factor authentication." },
        headers: {
          "WWW-Authenticate":
            'Bearer error="insufficient_user_authentication", acr_values="2"',
        },
      },
    ]);
    const user = userEvent.setup();
    await renderRoute(LIST_URL, { auth: { ...SIGNED_IN.auth, stepUp } });
    await screen.findByRole("link", { name: DRAFT_KEY });
    await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
    await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
    await screen.findByRole("dialog", { name: "Reclassify selected entries" });

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(
      dialog().getByLabelText("Changelog note"),
      "Reclassify while unstepped",
    );
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    await waitFor(() => expect(stepUp).toHaveBeenCalledWith("2"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(dialog().getByLabelText("Usage guidance")).toHaveValue("Fasting required");
    expect(dialog().getByLabelText("Changelog note")).toHaveValue(
      "Reclassify while unstepped",
    );
    expect(
      calls.filter(
        (call) =>
          call.method === "POST" &&
          call.path === "/api/v1/catalogue/entries/bulk/properties/usage_guidance",
      ),
    ).toHaveLength(1);
  });

  it("allows a resubmit after a generic server failure", async () => {
    let attempt = 0;
    stubApi([ENTRIES_OK, PROPERTIES_OK], {
      vary: (call) => {
        if (
          call.method === "POST" &&
          call.path.endsWith("/bulk/properties/usage_guidance")
        ) {
          attempt += 1;
          return attempt === 1
            ? { method: "POST", path: call.path, status: 500, body: { detail: "boom" } }
            : {
                method: "POST",
                path: call.path,
                status: 200,
                body: {
                  outcomes: [
                    { business_key: DRAFT_KEY, status: "applied", row_version: 4 },
                  ],
                  applied: 1,
                  unchanged: 0,
                  conflict: 0,
                  not_found: 0,
                },
              };
        }
        return null;
      },
    });
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(dialog().getByLabelText("Changelog note"), "Retry after a 500");
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    expect(await dialog().findByText("boom")).toBeInTheDocument();

    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("accessibility", () => {
  it("has no accessibility violations with no property chosen", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    await openDialogWithOneRowSelected();

    await expectNoA11yViolations(screen.getByRole("dialog"));
  });

  it("has no accessibility violations with a property and values entered", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = await openDialogWithOneRowSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "discipline");
    await dialog().findByLabelText("Discipline 1");

    await expectNoA11yViolations(screen.getByRole("dialog"));
  });
});

// Exercises the multi-row path the other describe blocks above don't -
// selecting both fixture rows rather than one.
describe("selecting multiple rows", () => {
  it("sends one entry per selected row", async () => {
    const calls = stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      {
        method: "POST",
        path: "/catalogue/entries/bulk/properties/usage_guidance",
        status: 200,
        body: {
          outcomes: [
            { business_key: DRAFT_KEY, status: "applied", row_version: 4 },
            { business_key: ACTIVE_KEY, status: "applied", row_version: 8 },
          ],
          applied: 2,
          unchanged: 0,
          conflict: 0,
          not_found: 0,
        },
      },
    ]);
    const user = await openDialogWithBothRowsSelected();

    await user.selectOptions(dialog().getByLabelText("Property"), "usage_guidance");
    await user.type(dialog().getByLabelText("Usage guidance"), "Fasting required");
    await user.type(dialog().getByLabelText("Changelog note"), "Reclassify both");
    await user.click(dialog().getByRole("button", { name: "Reclassify" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) =>
        call.method === "POST" &&
        call.path === "/api/v1/catalogue/entries/bulk/properties/usage_guidance",
    );
    expect(write?.body).toMatchObject({
      entries: [
        { business_key: DRAFT_KEY, expected_row_version: 3 },
        { business_key: ACTIVE_KEY, expected_row_version: 7 },
      ],
    });
  });
});
