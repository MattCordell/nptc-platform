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
    {
      key: "specimen",
      label: "Specimen",
      datatype: "code",
      cardinality: "0..*",
      scope: "both",
      required_for_submission: false,
      required_for_publication: false,
      binding_target: "value_set",
      value_set_uri: "http://snomed.info/sct?fhir_vs=ecl/%3C123038009",
      strength: "required",
      edition: null,
      local_code_system_key: null,
      filterable: true,
      origin: "system",
      status: "active",
      display_order: 30,
      constraints: { forbidden_codes: ["Any"] },
      row_version: 1,
      form_control: { control: "concept_picker", params: { allowJustification: false } },
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

const SPECIMEN_VALUE_OPTIONS_OK: Route = {
  method: "GET",
  path: "/registry/properties/specimen/values",
  status: 200,
  body: {
    items: [
      { code: "119361006", display: "Plasma specimen" },
      { code: "122554006", display: "Serum specimen" },
    ],
    total: 2,
  },
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
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK, SPECIMEN_VALUE_OPTIONS_OK]);

    await renderLoaded();

    const rows = panel().getAllByRole("row");
    // Header + discipline + specimen + usage_guidance + retired_note
    // (never_used has no recorded value on this entry, so FR-11's own rule
    // drops it entirely), in display_order (10, 30, 40, 50).
    expect(rows).toHaveLength(5);
    expect(within(rows[1]).getByText("Discipline")).toBeInTheDocument();
    expect(within(rows[1]).getByText("HAEM")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Specimen")).toBeInTheDocument();
  });

  it("shows an active property with nothing recorded yet as editable, not hidden", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);

    await renderLoaded();

    expect(panel().getAllByText("No value recorded.").length).toBeGreaterThan(0);
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

    await expectNoA11yViolations(
      screen.getByRole("region", { name: "Registry properties" }),
    );
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
          values: [
            {
              key: "usage_guidance",
              label: "Usage guidance",
              datatype: "string",
              cardinality: "0..1",
              status: "active",
              ordinal: 0,
              value: "Fasting required",
              justification: null,
            },
          ],
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
        call.path ===
          `/api/v1/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`,
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
      expect(screen.getAllByRole("status").map((region) => region.textContent)).toContain(
        "Usage guidance saved.",
      ),
    );
  });

  it("refuses to save with no changelog note", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Usage guidance" }));
    await user.click(screen.getByRole("dialog").querySelector("button[type=submit]")!);

    // The gate (issue #62) refuses the submit before it reaches the server,
    // and the reason is announced through the error summary - twice over,
    // once as the summary link and once as the field's own inline message,
    // even though the note field was never focused (issue #62 review).
    expect(await screen.findAllByText("A changelog note is required.")).toHaveLength(2);
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

  // Regression: `save.mutate` sends only the slots `isEmptySlotValue` keeps,
  // so a server issue's `ordinal` indexes that filtered array, not the
  // dialog's own rendered slots. Leaving slot 1 blank and slot 2 filled
  // means the server's `ordinal: 0` names the *second* rendered slot - a
  // naive `slotFieldId(key, issue.ordinal)` would attach the message to the
  // first (blank) one instead.
  it("maps a server issue's ordinal back through the slots a blank leading slot shifted", async () => {
    stubApi([
      READ_OK,
      PROPERTIES_OK,
      VALUE_OPTIONS_OK,
      {
        method: "PUT",
        path: `/catalogue/entries/${BUSINESS_KEY}/properties/discipline`,
        status: 422,
        body: {
          detail: "One or more values failed validation.",
          issues: [
            {
              property_key: "discipline",
              label: "Discipline",
              code: "schema-violation",
              message: "Not a recognised discipline code.",
              ordinal: 0,
            },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Discipline" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: /add another value/i }));
    // Slot 1 ("Discipline 1") already holds "HAEM" from the entry - clear it
    // so only slot 2 is submitted, making the server's `ordinal: 0` refer to
    // slot 2's rendered position.
    await user.selectOptions(dialog.getByLabelText("Discipline 1"), "");
    await user.selectOptions(dialog.getByLabelText("Discipline 2"), "HAEM");
    await user.type(
      dialog.getByLabelText("Changelog note"),
      "Correct the discipline code",
    );
    await user.click(dialog.getByRole("button", { name: "Save" }));

    const slotTwo = (await dialog.findByLabelText("Discipline 2")) as HTMLSelectElement;
    const describedBy = slotTwo.getAttribute("aria-describedby") ?? "";
    expect(describedBy).toContain("discipline-1-error");
    expect(document.getElementById("discipline-1-error")?.textContent).toBe(
      "Not a recognised discipline code.",
    );

    const slotOne = dialog.getByLabelText("Discipline 1") as HTMLSelectElement;
    expect(slotOne.getAttribute("aria-describedby") ?? "").not.toContain("error");
  });

  // FR-10's extensible-binding path: a coded property whose handler set
  // `allowJustification` must both render the field and put it on the wire -
  // untested until now because every other fixture leaves it `false`.
  it("saves a justification alongside a coded value when the property allows one", async () => {
    const justifiableDefinitions = {
      items: DEFINITIONS.items.map((definition) =>
        definition.key === "discipline"
          ? {
              ...definition,
              form_control: {
                control: "concept_picker",
                params: { allowJustification: true },
              },
            }
          : definition,
      ),
    };
    const calls = stubApi([
      READ_OK,
      { ...PROPERTIES_OK, body: justifiableDefinitions },
      VALUE_OPTIONS_OK,
      {
        method: "PUT",
        path: `/catalogue/entries/${BUSINESS_KEY}/properties/discipline`,
        status: 200,
        body: {
          values: [
            {
              key: "discipline",
              label: "Discipline",
              datatype: "code",
              cardinality: "0..*",
              status: "active",
              ordinal: 0,
              value: "HAEM",
              justification: "Locally agreed substitute",
            },
          ],
          row_version: 5,
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Discipline" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByLabelText("Justification")).toBeInTheDocument();
    await user.type(dialog.getByLabelText("Justification"), "Locally agreed substitute");
    await user.type(dialog.getByLabelText("Changelog note"), "Add a justification");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) =>
        call.method === "PUT" &&
        call.path === `/api/v1/catalogue/entries/${BUSINESS_KEY}/properties/discipline`,
    );
    expect(write?.body).toEqual({
      values: [{ value: "HAEM", justification: "Locally agreed substitute" }],
      reason: "Add a justification",
      expected_row_version: 4,
    });
  });

  // FR-88: specimen is 0..* - the sample's own worst case carries seven
  // values, so the panel must not impose a lower cap of its own.
  it("lets a 0..* coded property like specimen grow past a single value", async () => {
    stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK, SPECIMEN_VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Specimen" }));
    const dialog = within(screen.getByRole("dialog"));
    for (let i = 0; i < 6; i += 1) {
      await user.click(dialog.getByRole("button", { name: /add another value/i }));
    }

    expect(dialog.getAllByLabelText(/^Specimen \d$/)).toHaveLength(7);
  });

  // FR-89: the literal "Any" is refused server-side (a forbidden code on the
  // specimen definition's own constraints), and the refusal must land on the
  // specimen value it names rather than as a generic sentence.
  it("shows the literal-Any refusal against the specimen value it names", async () => {
    stubApi([
      READ_OK,
      PROPERTIES_OK,
      VALUE_OPTIONS_OK,
      SPECIMEN_VALUE_OPTIONS_OK,
      {
        method: "PUT",
        path: `/catalogue/entries/${BUSINESS_KEY}/properties/specimen`,
        status: 422,
        body: {
          detail: "One or more values failed validation.",
          issues: [
            {
              property_key: "specimen",
              label: "Specimen",
              code: "forbidden-code",
              message:
                "Any is not a valid specimen code (FR-89). Use the Any setting instead.",
              ordinal: 0,
            },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit Specimen" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText("Changelog note"), "Try the literal Any code");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    expect(
      await dialog.findAllByText(/Any is not a valid specimen code/),
    ).not.toHaveLength(0);
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
    await user.type(
      dialog.getByLabelText("Changelog note"),
      "This entry accepts any specimen",
    );
    await user.click(dialog.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const write = calls.find(
      (call) =>
        call.method === "PATCH" &&
        call.path === `/api/v1/catalogue/entries/${BUSINESS_KEY}`,
    );
    expect(write?.body).toEqual({
      specimen_unconstrained: true,
      reason: "This entry accepts any specimen",
      expected_row_version: 4,
    });
  });

  it("gates Save on a changelog note (FR-37, issue #62)", async () => {
    const calls = stubApi([READ_OK, PROPERTIES_OK, VALUE_OPTIONS_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(panel().getByRole("button", { name: "Edit" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByLabelText("This entry accepts any specimen (Any)"));
    expect(dialog.getByRole("button", { name: "Save" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    await user.click(dialog.getByRole("button", { name: "Save" }));
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });
});
