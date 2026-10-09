import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The catalogue entry edit form (FR-09, FR-11, FR-24, FR-36,
 * FR-37, FR-38, FR-44, FR-85, FR-89).
 *
 * Driven through the real admin edit route, like the panels it replaced.
 */

const BUSINESS_KEY = "NPTC-000901";
const EDIT_URL = `/admin/catalogue/${BUSINESS_KEY}/edit`;
const ENTRY_PATH = `/catalogue/admin/entries/${BUSINESS_KEY}`;
const AMEND_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations/amendment`;
const DISCIPLINE_PATH = `/catalogue/entries/${BUSINESS_KEY}/properties/discipline`;
const USAGE_PATH = `/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`;
const SPECIMEN_PATH = `/catalogue/entries/${BUSINESS_KEY}/properties/specimen`;

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

function definition(overrides: Record<string, unknown>) {
  return {
    key: "x",
    label: "X",
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
    origin: "system",
    status: "active",
    display_order: 10,
    constraints: {},
    row_version: 1,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const DEFINITIONS = {
  items: [
    definition({
      key: "discipline",
      label: "Discipline",
      datatype: "code",
      cardinality: "0..*",
      binding_target: "local_code_system",
      local_code_system_key: "discipline",
      display_order: 10,
      form_control: { control: "concept_picker", params: { allowJustification: false } },
    }),
    definition({
      key: "specimen",
      label: "Specimen",
      datatype: "code",
      cardinality: "0..*",
      binding_target: "value_set",
      value_set_uri: "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009",
      display_order: 30,
      form_control: { control: "concept_picker", params: { allowJustification: false } },
    }),
    definition({
      key: "usage_guidance",
      label: "Usage guidance",
      display_order: 40,
      form_control: { control: "textarea", params: {} },
    }),
    definition({
      key: "retired_note",
      label: "Retired note",
      origin: "admin",
      status: "deprecated",
      display_order: 50,
    }),
    definition({
      key: "never_used",
      label: "Never used",
      origin: "admin",
      status: "deprecated",
      display_order: 60,
    }),
  ],
};

const ENTRY = {
  business_key: BUSINESS_KEY,
  preferred_term: "Full blood count",
  length: 16,
  status: "active",
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

const READ_OK: Route = { method: "GET", path: ENTRY_PATH, status: 200, body: ENTRY };
const PROPERTIES_OK: Route = {
  method: "GET",
  path: "/registry/properties",
  status: 200,
  body: DEFINITIONS,
};
const DISCIPLINE_OPTIONS_OK: Route = {
  method: "GET",
  path: "/registry/properties/discipline/values",
  status: 200,
  body: {
    items: [
      { code: "HAEM", display: "Haematology" },
      { code: "BIOC", display: "Biochemistry" },
    ],
    total: 2,
  },
};
const SPECIMEN_OPTIONS_OK: Route = {
  method: "GET",
  path: "/registry/properties/specimen/values",
  status: 200,
  body: {
    items: [
      { code: "123038009", display: "Specimen" },
      { code: "119361006", display: "Plasma specimen" },
    ],
    total: 2,
  },
};
const BASE_ROUTES = [READ_OK, PROPERTIES_OK, DISCIPLINE_OPTIONS_OK, SPECIMEN_OPTIONS_OK];

function amendOk(
  overrides: { term?: string; length?: number; warnings?: unknown[] } = {},
) {
  const { term = "Serum ferritin", length = 14, warnings = [] } = overrides;
  return {
    method: "POST",
    path: AMEND_PATH,
    status: 200,
    body: {
      designation: {
        term,
        status: "active",
        length,
        label_provenance: {},
      },
      warnings,
      row_version: 5,
    },
  } satisfies Route;
}

function propertyOk(path: string, rowVersion: number): Route {
  return {
    method: "PUT",
    path,
    status: 200,
    body: { values: [], row_version: rowVersion },
  };
}

function propertyRefusal(path: string, message: string, ordinal = 0): Route {
  const key = path.split("/").at(-1);
  return {
    method: "PUT",
    path,
    status: 422,
    body: {
      detail: "One or more values failed validation.",
      issues: [
        {
          property_key: key,
          label: key,
          code: "schema-violation",
          message,
          ordinal,
        },
      ],
    },
  };
}

const VERSION_CONFLICT = {
  detail: "This entry changed after you opened it.",
  business_key: BUSINESS_KEY,
  expected_row_version: 4,
  current_row_version: 9,
  conflicts: [],
  changed_by: "another.editor",
  changed_at: "2026-09-01T05:00:00Z",
};

function form() {
  return within(screen.getByRole("region", { name: "Edit entry" }));
}

async function renderLoaded() {
  const rendered = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByRole("heading", { name: "Full blood count", level: 1 });
  await screen.findByLabelText("Usage guidance");
  return rendered;
}

function summary() {
  return within(screen.getByRole("region", { name: "Some changes were not saved" }));
}

function writes(calls: { method: string; path: string; body: unknown }[]) {
  return calls.filter((call) => call.method !== "GET");
}

function bodyOf(
  calls: { method: string; path: string; body: unknown }[],
  suffix: string,
) {
  return calls.find((call) => call.method !== "GET" && call.path.endsWith(suffix))?.body;
}

/** Picks a code once the property's value list has loaded into the select. */
async function choose(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  code: string,
) {
  const select = form().getByLabelText(label);
  await within(select).findByRole("option", { name: new RegExp(`^${code} `) });
  await user.selectOptions(select, code);
}

async function fillNote(user: ReturnType<typeof userEvent.setup>, note: string) {
  await user.type(form().getByLabelText("Changelog note"), note);
}

async function save(user: ReturnType<typeof userEvent.setup>) {
  await user.click(form().getByRole("button", { name: "Save" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what the form shows", () => {
  it("shows the identifier as text, with no control to change it (FR-03)", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(form().getByText(BUSINESS_KEY)).toBeInTheDocument();
    expect(
      form().queryByRole("textbox", { name: /identifier/i }),
    ).not.toBeInTheDocument();
  });

  it("generates one control per active property, in display_order (FR-09)", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    const labels = ["Discipline 1", "Specimen 1", "Usage guidance"].map((name) =>
      form().getByLabelText(name),
    );
    const positions = labels.map((label) =>
      Array.from(document.querySelectorAll("input, select, textarea")).indexOf(label),
    );
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("offers a property added to the registry with no change to the screen (FR-09)", async () => {
    stubApi([
      READ_OK,
      {
        ...PROPERTIES_OK,
        body: {
          items: [
            ...DEFINITIONS.items,
            definition({
              key: "turnaround",
              label: "Turnaround note",
              display_order: 45,
            }),
          ],
        },
      },
      DISCIPLINE_OPTIONS_OK,
      SPECIMEN_OPTIONS_OK,
    ]);
    await renderLoaded();

    expect(form().getByLabelText("Turnaround note")).toBeInTheDocument();
  });

  it("keeps a deprecated property's value visible and read only (FR-11)", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(
      form().getByText("Kept from before this property was deprecated"),
    ).toBeInTheDocument();
    expect(form().queryByLabelText("Retired note")).not.toBeInTheDocument();
  });

  it("does not show a deprecated property that holds no value", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(form().queryByText(/Never used/)).not.toBeInTheDocument();
  });

  it("explains the any-specimen value on the specimen property only (FR-89)", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(form().getAllByText(/choose Specimen \(123038009\) on its own/)).toHaveLength(
      1,
    );
  });

  it("has no accessibility violations", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    await expectNoA11yViolations(screen.getByRole("region", { name: "Edit entry" }));
  });
});

describe("the live term length (FR-85)", () => {
  it("shows the length of the term on load", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(form().getByText(/Length: 16 characters/)).toBeInTheDocument();
  });

  it("updates with each keystroke", async () => {
    stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("RCPA Preferred"), "!");

    expect(form().getByText(/Length: 17 characters/)).toBeInTheDocument();
  });

  it("counts after whitespace cleaning, as the server does", async () => {
    stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    const input = form().getByLabelText("RCPA Preferred");
    await user.clear(input);
    await user.paste("Ferritin ");

    expect(form().getByText(/Length: 8 characters/)).toBeInTheDocument();
  });

  it("counts a decomposed accent once, as the server does", async () => {
    stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    const input = form().getByLabelText("RCPA Preferred");
    await user.clear(input);
    await user.paste("Café screen");

    expect(form().getByText(/Length: 11 characters/)).toBeInTheDocument();
  });

  it("shows the server's count after a save, and it matches the live count", async () => {
    stubApi([...BASE_ROUTES, amendOk({ term: "Ferritin", length: 8 })]);
    const user = userEvent.setup();
    await renderLoaded();

    const input = form().getByLabelText("RCPA Preferred");
    await user.clear(input);
    await user.paste("Ferritin ");
    await fillNote(user, "Correct the preferred term");
    await save(user);

    expect(
      await form().findByText(/The server counted 8 when you last saved/),
    ).toBeInTheDocument();
    // The stored, cleaned term replaces the editor's text, and counts the same.
    expect(form().getByLabelText("RCPA Preferred")).toHaveValue("Ferritin");
    expect(form().getByText(/Length: 8 characters/)).toBeInTheDocument();
  });

  it("has no control for the length", async () => {
    stubApi(BASE_ROUTES);
    await renderLoaded();

    expect(form().queryByLabelText(/^length/i)).not.toBeInTheDocument();
  });
});

describe("saving", () => {
  it("sends only the changed fields, term first, each with the previous row version", async () => {
    const calls = stubApi([
      ...BASE_ROUTES,
      amendOk(),
      propertyOk(DISCIPLINE_PATH, 6),
      propertyOk(USAGE_PATH, 7),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await choose(user, "Discipline 1", "BIOC");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct three fields together");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(
      writes(calls).map((call) => [call.method, call.path.split("/api/v1")[1]]),
    ).toEqual([
      ["POST", AMEND_PATH],
      ["PUT", DISCIPLINE_PATH],
      ["PUT", USAGE_PATH],
    ]);
    expect(bodyOf(calls, AMEND_PATH)).toEqual({
      term: "Full blood count",
      new_term: "Serum ferritin",
      target: "preferred_term",
      expected_row_version: 4,
      reason: "Correct three fields together",
    });
    expect(bodyOf(calls, DISCIPLINE_PATH)).toEqual({
      values: [{ value: "BIOC", justification: null }],
      reason: "Correct three fields together",
      expected_row_version: 5,
    });
    expect(bodyOf(calls, USAGE_PATH)).toEqual({
      values: [{ value: "Fasting sample.", justification: null }],
      reason: "Correct three fields together",
      expected_row_version: 6,
    });
  });

  it("does not refetch the entry until the run is over", async () => {
    const calls = stubApi([...BASE_ROUTES, amendOk(), propertyOk(USAGE_PATH, 6)]);
    const user = userEvent.setup();
    await renderLoaded();
    const readsBefore = calls.filter((call) => call.path.endsWith(ENTRY_PATH)).length;

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct two fields together");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    await waitFor(() =>
      expect(calls.filter((call) => call.path.endsWith(ENTRY_PATH)).length).toBe(
        readsBefore + 1,
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((call) => call.path.endsWith(ENTRY_PATH)).length).toBe(
      readsBefore + 1,
    );
  });

  it("announces which fields saved", async () => {
    stubApi([...BASE_ROUTES, propertyOk(USAGE_PATH, 5)]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Record fasting guidance");
    await save(user);

    await waitFor(() =>
      expect(screen.getAllByRole("status").map((region) => region.textContent)).toContain(
        "Saved: Usage guidance.",
      ),
    );
  });

  it("saves a justification alongside a coded value that allows one", async () => {
    const calls = stubApi([
      READ_OK,
      {
        ...PROPERTIES_OK,
        body: {
          items: DEFINITIONS.items.map((item) =>
            item.key === "discipline"
              ? {
                  ...item,
                  form_control: {
                    control: "concept_picker",
                    params: { allowJustification: true },
                  },
                }
              : item,
          ),
        },
      },
      DISCIPLINE_OPTIONS_OK,
      SPECIMEN_OPTIONS_OK,
      propertyOk(DISCIPLINE_PATH, 5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Justification"), "Locally agreed substitute");
    await fillNote(user, "Add a justification");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, DISCIPLINE_PATH)).toEqual({
      values: [{ value: "HAEM", justification: "Locally agreed substitute" }],
      reason: "Add a justification",
      expected_row_version: 4,
    });
  });

  it("records a first value on a coded property that had none", async () => {
    const calls = stubApi([...BASE_ROUTES, propertyOk(SPECIMEN_PATH, 5)]);
    const user = userEvent.setup();
    await renderLoaded();

    await choose(user, "Specimen 1", "119361006");
    await fillNote(user, "Record the plasma specimen");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, SPECIMEN_PATH)).toEqual({
      values: [{ value: "119361006", justification: null }],
      reason: "Record the plasma specimen",
      expected_row_version: 4,
    });
  });
});

describe("a save that cannot go ahead", () => {
  it("says there is nothing to save, and sends nothing", async () => {
    const calls = stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await fillNote(user, "Nothing was changed here");
    await save(user);

    expect(await screen.findAllByText("There are no changes to save.")).not.toHaveLength(
      0,
    );
    expect(writes(calls)).toHaveLength(0);
  });

  it("requires a changelog note (FR-37), and sends nothing", async () => {
    const calls = stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await save(user);

    expect(await screen.findAllByText("A changelog note is required.")).not.toHaveLength(
      0,
    );
    expect(writes(calls)).toHaveLength(0);
  });

  it("refuses an empty preferred term before it reaches the server", async () => {
    const calls = stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await user.clear(form().getByLabelText("RCPA Preferred"));
    await fillNote(user, "Clear the preferred term");
    await save(user);

    expect(await screen.findAllByText("Enter the preferred term.")).not.toHaveLength(0);
    expect(writes(calls)).toHaveLength(0);
  });
});

describe("the Save button", () => {
  it("stays unavailable until something has changed and the note is valid", async () => {
    stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    const button = form().getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("aria-disabled", "true");

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    expect(button).toHaveAttribute("aria-disabled", "true");

    await fillNote(user, "Record fasting guidance");
    expect(button).not.toHaveAttribute("aria-disabled");
  });

  it("does not count a change that cleans to the stored term", async () => {
    // The server treats a term that differs only in whitespace it cleans away as
    // no change, so the form must not offer to save it.
    const calls = stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("RCPA Preferred"), "\u00a0");
    await fillNote(user, "Nothing real changed");
    await save(user);

    expect(await screen.findAllByText("There are no changes to save.")).not.toHaveLength(
      0,
    );
    expect(writes(calls)).toHaveLength(0);
  });

  it("shows an empty term and a missing note together on the same click", async () => {
    const calls = stubApi(BASE_ROUTES);
    const user = userEvent.setup();
    await renderLoaded();

    await user.clear(form().getByLabelText("RCPA Preferred"));
    await save(user);

    expect(await screen.findAllByText("Enter the preferred term.")).not.toHaveLength(0);
    expect(screen.getAllByText(/A changelog note is required\./).length).toBeGreaterThan(
      0,
    );
    expect(writes(calls)).toHaveLength(0);
  });
});

describe("a refused field", () => {
  it("marks the field, keeps its value, and still saves the others", async () => {
    const calls = stubApi([
      ...BASE_ROUTES,
      amendOk(),
      propertyRefusal(DISCIPLINE_PATH, "Not a recognised discipline code."),
      propertyOk(USAGE_PATH, 6),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await choose(user, "Discipline 1", "BIOC");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct three fields together");
    await save(user);

    await form().findByRole("heading", { name: "Some changes were not saved" });
    // The summary names the refused field and why, and the fields that saved.
    expect(summary().getByText(/Not a recognised discipline code\./)).toBeInTheDocument();
    expect(summary().getByText("RCPA Preferred")).toBeInTheDocument();
    expect(summary().getByText("Usage guidance")).toBeInTheDocument();
    // The refusal is also marked on the field, whose value is kept.
    const select = form().getByLabelText("Discipline 1");
    expect(select).toHaveValue("BIOC");
    expect(select.getAttribute("aria-describedby") ?? "").toContain("discipline-0-error");
    // A refusal changed nothing, so the next request carries the version after the first save.
    expect(bodyOf(calls, USAGE_PATH)).toMatchObject({ expected_row_version: 5 });
    expect(form().getByLabelText("Usage guidance")).toHaveValue("Fasting sample.");
  });

  it("moves focus to the summary", async () => {
    stubApi([...BASE_ROUTES, propertyRefusal(DISCIPLINE_PATH, "Not a recognised code.")]);
    const user = userEvent.setup();
    await renderLoaded();

    await choose(user, "Discipline 1", "BIOC");
    await fillNote(user, "Change the discipline");
    await save(user);

    const heading = await screen.findByRole("heading", {
      name: "Some changes were not saved",
    });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it("sends only the fields still unsaved on the next Save", async () => {
    const calls = stubApi([
      ...BASE_ROUTES,
      amendOk(),
      propertyRefusal(DISCIPLINE_PATH, "Not a recognised discipline code."),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await choose(user, "Discipline 1", "BIOC");
    await fillNote(user, "Correct two fields together");
    await save(user);
    await form().findByText(/Correct the marked fields/);
    const before = writes(calls).length;

    await save(user);

    await waitFor(() => expect(writes(calls).length).toBe(before + 1));
    expect(writes(calls).at(-1)?.path).toContain("/properties/discipline");
  });

  it("shows a length warning from the amendment (FR-86), announces it, and offers no Acknowledge", async () => {
    stubApi([
      ...BASE_ROUTES,
      amendOk({ warnings: [{ kind: "length", length: 80, max_length: 60 }] }),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await fillNote(user, "Reword the preferred term");
    await save(user);

    expect(
      await screen.findByText(/80 characters long, which is over the maximum of 60/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Acknowledge/ }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByRole("status").map((region) => region.textContent)).toContain(
        "Saved: RCPA Preferred. 1 warning to review.",
      ),
    );
  });

  it("marks a duplicate preferred term, names the other entry, and still saves the rest", async () => {
    const calls = stubApi([
      ...BASE_ROUTES,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This term is already in use on another entry.",
          collisions: [
            {
              severity: "error",
              business_key: "NPTC-000900",
              preferred_term: "Iron studies",
              label_provenance: {},
            },
          ],
        },
      },
      propertyOk(USAGE_PATH, 5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Iron studies");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Rename and add guidance");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(summary().getByText(/NPTC-000900/)).toBeInTheDocument();
    expect(form().getByLabelText("RCPA Preferred")).toHaveValue("Iron studies");
    // A refusal changed nothing, so the property is sent with the version the page loaded.
    expect(bodyOf(calls, USAGE_PATH)).toMatchObject({ expected_row_version: 4 });
    expect(summary().queryByText(/Not sent/)).not.toBeInTheDocument();
  });

  it("has no accessibility violations with the summary showing", async () => {
    stubApi([...BASE_ROUTES, propertyRefusal(DISCIPLINE_PATH, "Not a recognised code.")]);
    const user = userEvent.setup();
    await renderLoaded();

    await choose(user, "Discipline 1", "BIOC");
    await fillNote(user, "Change the discipline");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });

    await expectNoA11yViolations(screen.getByRole("region", { name: "Edit entry" }));
  });
});

describe("a failure that stops the run", () => {
  it("lists the fields that disagree, whatever type their values are (FR-38)", async () => {
    stubApi([
      ...BASE_ROUTES,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          ...VERSION_CONFLICT,
          conflicts: [
            {
              field: "preferred_term",
              submitted: "Serum ferritin",
              current: "Ferritin (S)",
            },
            { field: "provisional", submitted: false, current: true },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await fillNote(user, "Reword the preferred term");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(summary().getByText(/another\.editor/)).toBeInTheDocument();
    expect(summary().getByText(/Ferritin \(S\)/)).toBeInTheDocument();
    const item = summary().getByText("provisional").closest("li");
    expect(item).toHaveTextContent("you sent false");
    expect(item).toHaveTextContent("it is now true");
    expect(summary().getByText(/The entry is reloading/)).toBeInTheDocument();
  });

  it("reads correctly when the concurrent edit touched a different field", async () => {
    stubApi([
      ...BASE_ROUTES,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: { ...VERSION_CONFLICT, conflicts: [], changed_by: null, changed_at: null },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await fillNote(user, "Reword the preferred term");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(summary().getByText(/Someone else changed this entry/)).toBeInTheDocument();
    expect(summary().queryByText(/What you sent/)).not.toBeInTheDocument();
  });

  it("stops at a stale row version, leaves the rest on screen, and refetches the entry (FR-38)", async () => {
    const calls = stubApi([
      ...BASE_ROUTES,
      { method: "POST", path: AMEND_PATH, status: 409, body: VERSION_CONFLICT },
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    const readsBefore = calls.filter((call) => call.path.endsWith(ENTRY_PATH)).length;

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct two fields together");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(
      summary().getAllByText(/This entry changed after you opened it\./),
    ).toHaveLength(2);
    expect(summary().getByText(/Someone else changed this entry/)).toBeInTheDocument();
    expect(
      summary().getByText(/Not sent, because an earlier field failed/),
    ).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    // The unsent field is still on screen, with the editor's value.
    expect(form().getByLabelText("Usage guidance")).toHaveValue("Fasting sample.");
    await waitFor(() =>
      expect(
        calls.filter((call) => call.path.endsWith(ENTRY_PATH)).length,
      ).toBeGreaterThan(readsBefore),
    );
  });

  it.each([500, 403])("stops on a %i and says what was not sent", async (status) => {
    const calls = stubApi([
      ...BASE_ROUTES,
      {
        method: "PUT",
        path: DISCIPLINE_PATH,
        status,
        body: { detail: "The server said no." },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await choose(user, "Discipline 1", "BIOC");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct two fields together");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(summary().getAllByText(/The server said no\./).length).toBeGreaterThan(0);
    expect(
      summary().getByText(/Not sent, because an earlier field failed/),
    ).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
  });
});

describe("a field someone else changed", () => {
  const BIOC = { ...ENTRY.properties[0], value: "BIOC" };

  /** The entry as another editor left it: a new term and a new discipline, at version 9. */
  function changedByOthers(): Route {
    return {
      ...READ_OK,
      body: {
        ...ENTRY,
        preferred_term: "Ferritin (S)",
        row_version: 9,
        properties: [BIOC, ENTRY.properties[1]],
      },
    };
  }

  function stubConflictOnUsage(extra: Route[] = []) {
    let conflicted = false;
    return stubApi(
      [
        ...BASE_ROUTES,
        ...extra,
        { method: "PUT", path: USAGE_PATH, status: 409, body: VERSION_CONFLICT },
        propertyOk(USAGE_PATH, 10),
      ],
      {
        vary: (call, priorSameCalls) => {
          if (call.method === "PUT") {
            if (priorSameCalls === 0) {
              conflicted = true;
              return null;
            }
            return propertyOk(USAGE_PATH, 10);
          }
          return conflicted && call.path.endsWith(ENTRY_PATH) ? changedByOthers() : null;
        },
      },
    );
  }

  it("shows an untouched field's new value after a version conflict (FR-38)", async () => {
    stubConflictOnUsage();
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Record fasting guidance");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });
    await screen.findByRole("heading", { name: "Ferritin (S)", level: 1 });

    // The term and the discipline were not touched here, so they now show the other editor's.
    expect(form().getByLabelText("RCPA Preferred")).toHaveValue("Ferritin (S)");
    await waitFor(() =>
      expect(form().getByLabelText("Discipline 1")).toHaveValue("BIOC"),
    );
    // What this editor typed is kept.
    expect(form().getByLabelText("Usage guidance")).toHaveValue("Fasting sample.");
  });

  it("does not send the other editor's values back as this editor's change", async () => {
    const calls = stubConflictOnUsage();
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Record fasting guidance");
    await save(user);
    await screen.findByRole("heading", { name: "Ferritin (S)", level: 1 });
    await waitFor(() =>
      expect(form().getByLabelText("Discipline 1")).toHaveValue("BIOC"),
    );

    await save(user);

    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.path).toContain("/properties/usage_guidance");
    expect(writes(calls)[1]?.body).toMatchObject({ expected_row_version: 9 });
  });

  it("keeps a field the editor changed, even when someone else changed it too", async () => {
    // The term save is refused, so the editor's term stays unsaved while the
    // usage guidance then conflicts and the entry reloads with another term.
    stubConflictOnUsage([
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This term is already in use on another entry.",
          collisions: [
            {
              severity: "error",
              business_key: "NPTC-000900",
              preferred_term: "Iron studies",
              label_provenance: {},
            },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Reword and add guidance");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });
    await screen.findByRole("heading", { name: "Ferritin (S)", level: 1 });

    expect(form().getByLabelText("RCPA Preferred")).toHaveValue("Serum ferritin");
  });

  it("does not rebuild a field the editor is still working in after their own save", async () => {
    stubApi([...BASE_ROUTES, propertyOk(USAGE_PATH, 5)]);
    const user = userEvent.setup();
    await renderLoaded();

    const usage = form().getByLabelText("Usage guidance");
    await user.type(usage, "Fasting sample.");
    await fillNote(user, "Record fasting guidance");
    await save(user);
    await form().findByRole("heading", { name: "Changes saved" });

    // The same element is still on screen: nothing remounted it under the editor.
    expect(form().getByLabelText("Usage guidance")).toBe(usage);
  });
});

describe("saving again after a run", () => {
  it("addresses the next save from the run's own result while the entry is still reloading", async () => {
    let written = false;
    const calls = stubApi(
      [
        ...BASE_ROUTES,
        amendOk(),
        propertyRefusal(DISCIPLINE_PATH, "Not a recognised discipline code."),
      ],
      {
        vary: (call) => {
          if (call.method !== "GET") {
            written = true;
            return null;
          }
          // After the first write the refetch never answers, so the entry on screen
          // still holds the version and term the page loaded with.
          return written && call.path.endsWith(ENTRY_PATH)
            ? { ...READ_OK, neverSettles: true }
            : null;
        },
      },
    );
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await choose(user, "Discipline 1", "BIOC");
    await fillNote(user, "Correct two fields together");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });

    await user.type(term, " level");
    await save(user);

    await waitFor(() => expect(writes(calls)).toHaveLength(4));
    const second = writes(calls).filter((call) => call.path.endsWith(AMEND_PATH))[1];
    expect(second?.body).toMatchObject({
      term: "Serum ferritin",
      new_term: "Serum ferritin level",
      expected_row_version: 5,
    });
  });

  it("addresses the next save from the reloaded entry after someone else changed it", async () => {
    let conflicted = false;
    const calls = stubApi(
      [
        ...BASE_ROUTES,
        { method: "POST", path: AMEND_PATH, status: 409, body: VERSION_CONFLICT },
      ],
      {
        vary: (call) => {
          if (call.method === "POST") {
            conflicted = true;
            return null;
          }
          return conflicted && call.path.endsWith(ENTRY_PATH)
            ? {
                ...READ_OK,
                body: { ...ENTRY, preferred_term: "Ferritin (S)", row_version: 9 },
              }
            : null;
        },
      },
    );
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await fillNote(user, "Reword the preferred term");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });
    await screen.findByRole("heading", { name: "Ferritin (S)", level: 1 });

    await save(user);

    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.body).toMatchObject({
      term: "Ferritin (S)",
      new_term: "Serum ferritin",
      expected_row_version: 9,
    });
  });
});
