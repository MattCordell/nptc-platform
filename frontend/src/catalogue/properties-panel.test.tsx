import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The registry properties panel (issue #151; FR-09, FR-10, FR-11, FR-36,
 * FR-37, FR-38, FR-89).
 *
 * Driven through the real admin edit route, matching
 * `admin-catalogue-edit.test.tsx`'s own convention: what is under test is
 * the shipped screen, not a component mounted in isolation.
 */

const BUSINESS_KEY = "NPTC-000901";
const EDIT_URL = `/admin/catalogue/${BUSINESS_KEY}/edit`;

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const DEFINITIONS = {
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
      form_control: { control: "concept_picker", params: { allowJustification: false } },
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
    {
      key: "never_used",
      label: "Never used",
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
      display_order: 60,
      constraints: {},
      row_version: 1,
      form_control: { control: "text", params: {} },
    },
  ],
};

const ENTRY = {
  business_key: BUSINESS_KEY,
  preferred_term: "Full blood count",
  length: 17,
  status: "active",
  specimen_unconstrained: false,
  updated_at: "2026-09-01T04:30:00Z",
  row_version: 4,
  designations: [],
  bindings: [],
  properties: [
    {
      key: "discipline",
      label: "Discipline",
      datatype: "code",
      cardinality: "0..*",
      status: "active",
      ordinal: 0,
      value: "HAEM",
      justification: null,
    },
    {
      key: "retired_note",
      label: "Retired note",
      datatype: "string",
      cardinality: "0..1",
      status: "deprecated",
      ordinal: 0,
      value: "Kept from before this property was deprecated",
      justification: null,
    },
  ],
};

const READ_OK: Route = {
  method: "GET",
  path: `/catalogue/admin/entries/${BUSINESS_KEY}`,
  status: 200,
  body: ENTRY,
};

const PROPERTIES_OK: Route = {
  method: "GET",
  path: "/registry/properties",
  status: 200,
  body: DEFINITIONS,
};

const VALUE_OPTIONS_OK: Route = {
  method: "GET",
  path: "/registry/properties/discipline/values",
  status: 200,
  body: { items: [{ code: "HAEM", display: "Haematology" }], total: 1 },
};

function panel() {
  return within(screen.getByRole("region", { name: "Registry properties" }));
}

async function renderLoaded() {
  const rendered = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByRole("heading", { name: "Full blood count", level: 1 });
  await screen.findByRole("region", { name: "Registry properties" });
  return rendered;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("generated rows", () => {
  it("shows an active property's recorded value, in display_order", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    const rows = panel().getAllByRole("row");
    // Header + discipline + usage_guidance + retired_note (never_used has no
    // recorded value on this entry, so FR-11's own rule drops it entirely).
    expect(rows).toHaveLength(4);
    expect(within(rows[1]).getByText("Discipline")).toBeInTheDocument();
    expect(within(rows[1]).getByText("HAEM")).toBeInTheDocument();
  });

  it("shows an active property with nothing recorded yet as editable, not hidden", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    expect(panel().getByText("No value recorded.")).toBeInTheDocument();
    expect(
      panel().getByRole("button", { name: "Edit Usage guidance" }),
    ).toBeInTheDocument();
  });

  it("renders a deprecated property's kept value read-only (FR-11)", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    expect(
      panel().getByText("Kept from before this property was deprecated"),
    ).toBeInTheDocument();
    expect(
      panel().queryByRole("button", { name: "Edit Retired note" }),
    ).not.toBeInTheDocument();
  });

  it("never offers a deprecated property with no recorded value", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    expect(panel().queryByText("Never used")).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    await expectNoA11yViolations(screen.getByRole("region", { name: "Registry properties" }));
  });

  it("has no accessibility violations in the property edit dialog", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Discipline" }));
    await expectNoA11yViolations(await screen.findByRole("dialog"));
  });

  it("has no accessibility violations in the specimen_unconstrained dialog", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit" }));
    await expectNoA11yViolations(await screen.findByRole("dialog"));
  });
});

describe("editing a property's values", () => {
  it("PUTs the whole filtered value set to the property's own route", async () => {
    const calls = stubApi([
      READ_OK,
      PROPERTIES_OK,
      VALUE_OPTIONS_OK,
      {
        method: "PUT",
        path: `/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`,
        status: 200,
        body: {
          values: [{ key: "usage_guidance", label: "Usage guidance", datatype: "string", cardinality: "0..1", status: "active", ordinal: 0, value: "Fasting required", justification: null }],
          row_version: 5,
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Usage guidance" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText("Usage guidance"), "Fasting required");
    await user.type(dialog.getByLabelText("Changelog note"), "Record fasting guidance");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) =>
        call.method === "PUT" &&
        call.path === `/api/v1/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`,
    );
    expect(write).toBeDefined();
    expect(write?.body).toEqual({
      values: [{ value: "Fasting required", justification: null }],
      reason: "Record fasting guidance",
      expected_row_version: 4,
    });
    // A screen-reader user closing a dialog on save needs to be told the
    // save happened, the same way DesignationsPanel/BindingsPanel announce.
    // Every panel on this page owns its own live region, so this checks
    // across all of them rather than assuming there is only one.
    await waitFor(() =>
      expect(
        screen.getAllByRole("status").map((region) => region.textContent),
      ).toContain("Usage guidance saved."),
    );
  });

  it("refuses to save with no changelog note", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Usage guidance" }));
    await user.click(screen.getByRole("dialog").querySelector("button[type=submit]")!);

    // Appears twice by design: once in the error summary link, once as the
    // field's own inline message (`ErrorSummary`'s own contract).
    expect(
      await screen.findAllByText("Enter a changelog note describing this change."),
    ).not.toHaveLength(0);
  });

  // Principal failure mode: FR-89's specimen cross-field / FR-10's cardinality
  // checks come back as PropertyValidationResponse's issues[], and each one
  // must land on the field it names rather than as a generic refusal.
  it("shows a field-level 422 issue against the value it names", async () => {
    stubApi([
      READ_OK,
      PROPERTIES_OK,
      VALUE_OPTIONS_OK,
      {
        method: "PUT",
        path: `/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`,
        status: 422,
        body: {
          detail: "One or more values failed validation.",
          issues: [
            {
              property_key: "usage_guidance",
              label: "Usage guidance",
              code: "schema-violation",
              message: "This value is too long.",
              ordinal: 0,
            },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Usage guidance" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText("Usage guidance"), "x".repeat(500));
    await user.type(dialog.getByLabelText("Changelog note"), "Try an overlong value");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    expect(await dialog.findAllByText("This value is too long.")).not.toHaveLength(0);
  });
});

describe("specimen_unconstrained", () => {
  it("is shown as a core entry setting, separate from any property row", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    expect(panel().getByText(/Accepts any specimen/)).toBeInTheDocument();
  });

  it("PATCHes the entry's own core route, not a property route", async () => {
    const calls = stubApi([
      READ_OK,
      PROPERTIES_OK,
      VALUE_OPTIONS_OK,
      {
        method: "PATCH",
        path: `/catalogue/entries/${BUSINESS_KEY}`,
        status: 200,
        body: { status: "active", specimen_unconstrained: true, row_version: 5 },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByLabelText("This entry accepts any specimen (Any)"));
    await user.type(dialog.getByLabelText("Changelog note"), "This entry accepts any specimen");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) => call.method === "PATCH" && call.path === `/api/v1/catalogue/entries/${BUSINESS_KEY}`,
    );
    expect(write?.body).toEqual({
      specimen_unconstrained: true,
      reason: "This entry accepts any specimen",
      expected_row_version: 4,
    });
  });
});
