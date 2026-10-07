import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/**
 * The amend-property screen (FR-09, FR-12, NFR-31), driven through the real
 * route.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const KEY = "assay_method";
const EDIT_URL = `/admin/properties/${KEY}/edit`;
const PROPERTY_PATH = `/registry/properties/${KEY}`;

function definition(overrides: Record<string, unknown> = {}) {
  return {
    key: KEY,
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
    display_order: 20,
    constraints: { maxLength: 80 },
    row_version: 3,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const READ_OK = { method: "GET", path: PROPERTY_PATH, status: 200, body: definition() };
const DATATYPES_OK = {
  method: "GET",
  path: "/registry/datatypes",
  status: 200,
  body: {
    items: [
      {
        name: "alpha",
        constraints_schema: { properties: { maxLength: { type: "integer" } } },
        uses_binding: false,
      },
    ],
  },
};

function patchCalls(calls: ReturnType<typeof stubApi>) {
  return calls.filter((call) => call.method === "PATCH");
}

async function renderEdit() {
  const result = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByLabelText("Label");
  return result;
}

async function typeReason(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Reason"), "Clarify the label");
}

async function changeLabel(user: ReturnType<typeof userEvent.setup>) {
  await user.clear(screen.getByLabelText("Label"));
  await user.type(screen.getByLabelText("Label"), "Assay method (immunoassay)");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminPropertyEditPage", () => {
  it("fills the form from the property and shows what cannot change as text", async () => {
    stubApi([READ_OK, DATATYPES_OK]);

    await renderEdit();

    expect(
      screen.getByRole("heading", { level: 1, name: "Edit Assay method" }),
    ).toBeVisible();
    expect(screen.getByLabelText("Label")).toHaveValue("Assay method");
    expect(screen.getByLabelText("Display order")).toHaveValue("20");
    expect(screen.getByLabelText("Constraints")).toHaveValue('{\n  "maxLength": 80\n}');
    const fixed = screen.getByRole("heading", { name: "Fixed once created" })
      .parentElement as HTMLElement;
    expect(within(fixed).getByText(KEY)).toBeVisible();
    expect(within(fixed).getByText("alpha")).toBeVisible();
    expect(within(fixed).getByText("Zero or one")).toBeVisible();
    expect(within(fixed).getByText("Both")).toBeVisible();
    // Nothing the API refuses to amend is an input.
    expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Datatype")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Scope")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cardinality")).not.toBeInTheDocument();
  });

  it("lists the constraint names the datatype allows once the datatypes load", async () => {
    stubApi([READ_OK, DATATYPES_OK]);

    await renderEdit();

    await waitFor(() =>
      expect(screen.getByLabelText("Constraints")).toHaveAccessibleDescription(
        "Optional. A JSON object using only these names: maxLength. Saving replaces the whole object.",
      ),
    );
  });

  it("still works when the datatypes cannot be loaded", async () => {
    stubApi([READ_OK, { ...DATATYPES_OK, status: 500, body: { detail: "no" } }]);

    await renderEdit();

    expect(screen.getByLabelText("Constraints")).toHaveAccessibleDescription(
      "Optional. A JSON object. Saving replaces the whole object.",
    );
  });

  it("saves only the changed field and returns to the detail page", async () => {
    const calls = stubApi([
      READ_OK,
      DATATYPES_OK,
      {
        method: "PATCH",
        path: PROPERTY_PATH,
        status: 200,
        body: definition({ label: "Assay method (immunoassay)", row_version: 4 }),
      },
    ]);
    const user = userEvent.setup();
    const { router } = await renderEdit();
    await changeLabel(user);
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByRole("heading", { level: 1, name: "Assay method" });
    expect(router.state.location.pathname).toBe(`/admin/properties/${KEY}`);
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
    expect(patchCalls(calls)).toHaveLength(1);
    expect(patchCalls(calls)[0]?.body).toEqual({
      label: "Assay method (immunoassay)",
      expected_row_version: 3,
      reason: "Clarify the label",
    });
  });

  it("sends nothing and says so when nothing was changed", async () => {
    const calls = stubApi([READ_OK, DATATYPES_OK]);
    const user = userEvent.setup();
    await renderEdit();
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    const summary = await screen.findByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(
      screen.getByRole("link", { name: "Change at least one field before saving." }),
    ).toBeInTheDocument();
    expect(patchCalls(calls)).toHaveLength(0);
  });

  it("refuses bad constraints and a missing reason before any request", async () => {
    const calls = stubApi([READ_OK, DATATYPES_OK]);
    const user = userEvent.setup();
    await renderEdit();
    await user.clear(screen.getByLabelText("Constraints"));
    await user.type(screen.getByLabelText("Constraints"), "maxLength = 80");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(
      await screen.findByRole("link", {
        name: "Constraints are not valid JSON. Check the braces, quotes and commas.",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Enter the reason for this change." }),
    ).toBeVisible();
    expect(patchCalls(calls)).toHaveLength(0);
  });

  it("reloads the property after a stale-version refusal and then saves against the new version", async () => {
    const conflict = {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: KEY,
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-10-07T00:00:00Z",
    };
    // Keyed on the first write having happened, not on a call count: StrictMode
    // fetches the property twice on the first render.
    let written = false;
    const calls = stubApi(
      [
        READ_OK,
        DATATYPES_OK,
        {
          method: "PATCH",
          path: PROPERTY_PATH,
          status: 200,
          body: definition({ row_version: 5 }),
        },
      ],
      {
        vary: (call, prior) => {
          if (call.method === "PATCH" && prior === 0) {
            written = true;
            return { method: "PATCH", path: call.path, status: 409, body: conflict };
          }
          if (written && call.method === "GET" && call.path.endsWith(PROPERTY_PATH)) {
            // What another administrator saved, including a label this editor never touched.
            return {
              method: "GET",
              path: call.path,
              status: 200,
              body: definition({ label: "Assay method v2", row_version: 4 }),
            };
          }
          return null;
        },
      },
    );
    const user = userEvent.setup();
    await renderEdit();
    await user.clear(screen.getByLabelText("Display order"));
    await user.type(screen.getByLabelText("Display order"), "5");
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(
      await screen.findByText(/Someone else changed this property while you had it open/),
    ).toBeInTheDocument();
    expect(screen.getByText(/most recently A Curator/)).toBeInTheDocument();
    expect(screen.getByText("Nothing has been saved.")).toBeInTheDocument();
    const summary = screen.getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    await waitFor(() =>
      expect(
        calls.filter((call) => call.method === "GET" && call.path.endsWith(PROPERTY_PATH))
          .length,
      ).toBeGreaterThan(1),
    );

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchCalls(calls)).toHaveLength(2));
    // The first save used the version the form was filled from, the second the
    // reloaded one. Neither sends the untouched label, so the other
    // administrator's label survives.
    expect(patchCalls(calls).map((call) => call.body)).toEqual([
      { display_order: 5, expected_row_version: 3, reason: "Clarify the label" },
      { display_order: 5, expected_row_version: 4, reason: "Clarify the label" },
    ]);
  });

  it("shows the server's sentence when it refuses the constraints", async () => {
    const detail =
      "The constraints given for this property are not valid for its datatype.";
    stubApi([
      READ_OK,
      DATATYPES_OK,
      { method: "PATCH", path: PROPERTY_PATH, status: 422, body: { detail } },
    ]);
    const user = userEvent.setup();
    await renderEdit();
    await user.clear(screen.getByLabelText("Constraints"));
    await user.type(screen.getByLabelText("Constraints"), '{{"unknown": 1}');
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    const summary = screen.getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(screen.getByLabelText("Reason")).toHaveValue("Clarify the label");
  });

  it("shows the refusal to a caller without registry.manage", async () => {
    const detail = "You do not have permission to manage the property registry.";
    stubApi([
      READ_OK,
      DATATYPES_OK,
      { method: "PATCH", path: PROPERTY_PATH, status: 403, body: { detail } },
    ]);
    const user = userEvent.setup();
    await renderEdit();
    await changeLabel(user);
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    const summary = screen.getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
  });

  it("falls back to its own wording when the refusal carries no sentence", async () => {
    stubApi([
      READ_OK,
      DATATYPES_OK,
      {
        method: "PATCH",
        path: PROPERTY_PATH,
        status: 422,
        body: { detail: [{ loc: ["body", "key"], msg: "Extra inputs", type: "extra" }] },
      },
    ]);
    const user = userEvent.setup();
    await renderEdit();
    await changeLabel(user);
    await typeReason(user);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(
      await screen.findByText(/The change could not be saved\. Check the details/),
    ).toBeInTheDocument();
  });

  it("names the key and the next step when the property does not exist, and announces it", async () => {
    stubApi([
      {
        method: "GET",
        path: "/registry/properties/missing",
        status: 404,
        body: { detail: "No property definition matches the given key." },
      },
      DATATYPES_OK,
    ]);

    await renderRoute("/admin/properties/missing/edit", SIGNED_IN);

    const message = "No property was found for missing. Check the key.";
    expect(await screen.findByText(message)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(message));
    expect(
      screen.queryByRole("button", { name: "Save changes" }),
    ).not.toBeInTheDocument();
  });

  it("links back to the property from the header and from Cancel", async () => {
    stubApi([READ_OK, DATATYPES_OK]);

    await renderEdit();

    expect(screen.getByRole("link", { name: "Back to the property" })).toHaveAttribute(
      "href",
      `/admin/properties/${KEY}`,
    );
    expect(screen.getByRole("link", { name: "Cancel" })).toHaveAttribute(
      "href",
      `/admin/properties/${KEY}`,
    );
  });

  it("has exactly one h1 and no automated accessibility violations, with and without errors", async () => {
    stubApi([READ_OK, DATATYPES_OK]);
    const user = userEvent.setup();
    const { container } = await renderEdit();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("There is a problem");
    await expectNoA11yViolations(container);
  });
});
