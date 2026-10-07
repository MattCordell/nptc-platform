import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/**
 * The create-property screen (FR-09, FR-12, FR-77, NFR-31), driven through the
 * real route. The datatypes are invented names: the form must offer a binding
 * because the API says a datatype takes one, never because of what it is
 * called.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const DATATYPES_PATH = "/registry/datatypes";
const CREATE_PATH = "/registry/properties";

const DATATYPES_OK = {
  method: "GET",
  path: DATATYPES_PATH,
  status: 200,
  body: {
    items: [
      {
        name: "alpha",
        constraints_schema: { properties: { maxLength: { type: "integer" } } },
        uses_binding: false,
      },
      { name: "beta", constraints_schema: {}, uses_binding: true },
      // Named like a coded type but flagged as taking no binding.
      { name: "code", constraints_schema: {}, uses_binding: false },
    ],
  },
};

const CREATED = {
  key: "assay_method",
  label: "Assay method",
  datatype: "alpha",
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
  status: "active",
  display_order: 0,
  constraints: {},
  row_version: 1,
  form_control: { control: "text", params: {} },
};

function writeCalls(calls: ReturnType<typeof stubApi>) {
  return calls.filter(
    (call) => call.method === "POST" && call.path.endsWith(CREATE_PATH),
  );
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Key"), "assay_method");
  await user.type(screen.getByLabelText("Label"), "Assay method");
  await user.selectOptions(screen.getByLabelText("Datatype"), "alpha");
  await user.selectOptions(screen.getByLabelText("Cardinality"), "0..1");
  await user.selectOptions(screen.getByLabelText("Scope"), "both");
  await user.type(screen.getByLabelText("Reason"), "Needed for the immunoassay entries");
}

async function renderCreate() {
  const result = await renderRoute("/admin/properties/new", SIGNED_IN);
  await screen.findByLabelText("Key");
  return result;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminPropertyCreatePage", () => {
  it("offers the datatypes the API lists and leaves every choice unmade", async () => {
    stubApi([DATATYPES_OK]);

    await renderCreate();

    const options = Array.from(
      (screen.getByLabelText("Datatype") as HTMLSelectElement).options,
    ).map((option) => option.textContent);
    expect(options).toEqual(["Choose a datatype", "alpha", "beta", "code"]);
    expect(screen.getByLabelText("Cardinality")).toHaveValue("");
    expect(screen.getByLabelText("Scope")).toHaveValue("");
  });

  it("shows the binding fields for a datatype the API flags, not one named like a coded type", async () => {
    stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();

    await user.selectOptions(screen.getByLabelText("Datatype"), "code");
    expect(screen.queryByText("Terminology binding")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Datatype"), "beta");
    expect(screen.getByText("Terminology binding")).toBeVisible();
    expect(screen.queryByLabelText("Value set URI")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Bound to"), "value_set");
    expect(screen.getByLabelText("Value set URI")).toBeVisible();
    expect(screen.getByLabelText("Binding strength")).toBeVisible();
    expect(screen.getByLabelText("Edition")).toBeVisible();

    await user.selectOptions(screen.getByLabelText("Bound to"), "local_code_system");
    expect(screen.getByLabelText("Local code system key")).toBeVisible();
    expect(screen.queryByLabelText("Value set URI")).not.toBeInTheDocument();
  });

  it("lists the constraint names the chosen datatype allows", async () => {
    stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();

    await user.selectOptions(screen.getByLabelText("Datatype"), "alpha");

    expect(screen.getByLabelText("Constraints")).toHaveAccessibleDescription(
      "Optional. A JSON object using only these names: maxLength.",
    );
  });

  it("sends nothing and moves focus to the summary when required fields are empty", async () => {
    const calls = stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();

    await user.click(screen.getByRole("button", { name: "Create property" }));

    const summary = await screen.findByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(writeCalls(calls)).toHaveLength(0);
    expect(screen.getByRole("link", { name: "Choose a datatype." })).toBeInTheDocument();
    // The summary link reaches the field it names, and the field repeats the message.
    await user.click(screen.getByRole("link", { name: "Choose a datatype." }));
    expect(screen.getByLabelText("Datatype")).toHaveFocus();
    expect(screen.getByLabelText("Datatype")).toBeInvalid();
  });

  it("refuses a key the database would reject, before any request", async () => {
    const calls = stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);
    await user.clear(screen.getByLabelText("Key"));
    await user.type(screen.getByLabelText("Key"), "Assay-Method");

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(await screen.findByText("There is a problem")).toBeInTheDocument();
    expect(screen.getByLabelText("Key")).toBeInvalid();
    expect(writeCalls(calls)).toHaveLength(0);
  });

  it("refuses a bound datatype with no binding, before any request", async () => {
    const calls = stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);
    await user.selectOptions(screen.getByLabelText("Datatype"), "beta");

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(
      await screen.findByRole("link", { name: "Choose what the property is bound to." }),
    ).toBeInTheDocument();
    expect(writeCalls(calls)).toHaveLength(0);
  });

  it("refuses constraints that are not a JSON object, before any request", async () => {
    const calls = stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);
    await user.type(screen.getByLabelText("Constraints"), "maxLength = 80");

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(
      await screen.findByRole("link", {
        name: "Constraints are not valid JSON. Check the braces, quotes and commas.",
      }),
    ).toBeInTheDocument();
    expect(writeCalls(calls)).toHaveLength(0);
  });

  it("creates the property and opens its detail page, with focus moved to the main region", async () => {
    const calls = stubApi([
      DATATYPES_OK,
      { method: "POST", path: CREATE_PATH, status: 201, body: CREATED },
      { method: "GET", path: `${CREATE_PATH}/assay_method`, status: 200, body: CREATED },
    ]);
    const user = userEvent.setup();
    const { router } = await renderCreate();
    await fillRequired(user);
    await user.type(screen.getByLabelText("Display order"), "20");
    await user.click(screen.getByLabelText("Used as a catalogue filter"));
    await user.click(screen.getByLabelText("Required for submission"));
    await user.type(screen.getByLabelText("Constraints"), '{{"maxLength": 80}');

    await user.click(screen.getByRole("button", { name: "Create property" }));

    await screen.findByRole("heading", { level: 1, name: "Assay method" });
    expect(router.state.location.pathname).toBe("/admin/properties/assay_method");
    // The app-wide rule for every client-side navigation (NFR-31): the form
    // that held focus is gone, so focus moves to `<main>` rather than `<body>`.
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
    expect(writeCalls(calls)).toHaveLength(1);
    expect(writeCalls(calls)[0]?.body).toEqual({
      key: "assay_method",
      label: "Assay method",
      datatype: "alpha",
      cardinality: "0..1",
      scope: "both",
      required_for_submission: true,
      required_for_publication: false,
      filterable: true,
      display_order: 20,
      constraints: { maxLength: 80 },
      reason: "Needed for the immunoassay entries",
    });
  });

  it("sends a value-set binding for a datatype the API says takes one", async () => {
    const calls = stubApi([
      DATATYPES_OK,
      { method: "POST", path: CREATE_PATH, status: 201, body: CREATED },
      { method: "GET", path: `${CREATE_PATH}/assay_method`, status: 200, body: CREATED },
    ]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);
    await user.selectOptions(screen.getByLabelText("Datatype"), "beta");
    await user.selectOptions(screen.getByLabelText("Bound to"), "value_set");
    await user.type(
      screen.getByLabelText("Value set URI"),
      "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009",
    );
    await user.selectOptions(screen.getByLabelText("Binding strength"), "required");
    await user.type(screen.getByLabelText("Edition"), "au");

    await user.click(screen.getByRole("button", { name: "Create property" }));

    await screen.findByRole("heading", { level: 1, name: "Assay method" });
    expect(writeCalls(calls)[0]?.body).toMatchObject({
      datatype: "beta",
      binding_target: "value_set",
      value_set_uri: "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009",
      strength: "required",
      edition: "au",
    });
  });

  it("shows the server's duplicate-key refusal in the form error slot and keeps what was typed", async () => {
    const detail = "A property definition with this key already exists.";
    stubApi([
      DATATYPES_OK,
      { method: "POST", path: CREATE_PATH, status: 409, body: { detail } },
    ]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    const summary = screen.getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(screen.getByLabelText("Key")).toHaveValue("assay_method");
    expect(screen.getByLabelText("Reason")).toHaveValue(
      "Needed for the immunoassay entries",
    );
  });

  it("shows the refusal to a caller without registry.manage", async () => {
    const detail = "You do not have permission to manage the property registry.";
    stubApi([
      DATATYPES_OK,
      { method: "POST", path: CREATE_PATH, status: 403, body: { detail } },
    ]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    const summary = screen.getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
  });

  it("falls back to its own wording when the refusal carries no sentence", async () => {
    stubApi([
      DATATYPES_OK,
      {
        method: "POST",
        path: CREATE_PATH,
        status: 422,
        body: {
          detail: [
            { loc: ["body", "cardinality"], msg: "Input should be", type: "enum" },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderCreate();
    await fillRequired(user);

    await user.click(screen.getByRole("button", { name: "Create property" }));

    expect(
      await screen.findByText(/The property could not be created\. Check the details/),
    ).toBeInTheDocument();
  });

  it("says what to do when the datatypes cannot be loaded, and announces it", async () => {
    stubApi([
      { method: "GET", path: DATATYPES_PATH, status: 500, body: { detail: "no" } },
    ]);

    await renderRoute("/admin/properties/new", SIGNED_IN);

    const message =
      "The datatypes could not be loaded, so a property cannot be created yet. Reload the page to try again.";
    expect(await screen.findAllByText(message)).not.toHaveLength(0);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(message));
    expect(
      screen.queryByRole("button", { name: "Create property" }),
    ).not.toBeInTheDocument();
  });

  it("can be filled in and submitted by keyboard alone, in reading order", async () => {
    const calls = stubApi([
      DATATYPES_OK,
      { method: "POST", path: CREATE_PATH, status: 201, body: CREATED },
      { method: "GET", path: `${CREATE_PATH}/assay_method`, status: 200, body: CREATED },
    ]);
    const user = userEvent.setup();
    await renderCreate();
    screen.getByLabelText("Key").focus();

    const order = [
      "Key",
      "Label",
      "Datatype",
      "Cardinality",
      "Scope",
      "Display order",
      "Required for submission",
      "Required for publication",
      "Used as a catalogue filter",
      "Constraints",
      "Reason",
    ];
    for (const [index, name] of order.entries()) {
      expect(screen.getByLabelText(name)).toHaveFocus();
      if (index < order.length - 1) {
        await user.tab();
      }
    }
    await user.tab();
    expect(screen.getByRole("button", { name: "Create property" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Cancel" })).toHaveFocus();

    // Enter in a text field submits the form, as it does for a keyboard user.
    await user.type(screen.getByLabelText("Key"), "assay_method");
    await user.type(screen.getByLabelText("Label"), "Assay method");
    await user.selectOptions(screen.getByLabelText("Datatype"), "alpha");
    await user.selectOptions(screen.getByLabelText("Cardinality"), "0..1");
    await user.selectOptions(screen.getByLabelText("Scope"), "both");
    await user.type(
      screen.getByLabelText("Reason"),
      "Needed for the immunoassay entries{Enter}",
    );

    await screen.findByRole("heading", { level: 1, name: "Assay method" });
    expect(writeCalls(calls)).toHaveLength(1);
  });

  it("links back to the registry from the header and from Cancel", async () => {
    stubApi([DATATYPES_OK]);

    await renderCreate();

    expect(
      screen.getByRole("link", { name: "Back to the property registry" }),
    ).toHaveAttribute("href", "/admin/properties");
    expect(screen.getByRole("link", { name: "Cancel" })).toHaveAttribute(
      "href",
      "/admin/properties",
    );
  });

  it("has exactly one h1 and no automated accessibility violations, with and without errors", async () => {
    stubApi([DATATYPES_OK]);
    const user = userEvent.setup();
    const { container } = await renderCreate();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);

    await user.selectOptions(screen.getByLabelText("Datatype"), "beta");
    await user.click(screen.getByRole("button", { name: "Create property" }));
    await screen.findByText("There is a problem");
    await expectNoA11yViolations(container);
  });
});
