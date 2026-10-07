import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/**
 * Deprecating a property from its detail screen (FR-09, FR-11, NFR-31), driven
 * through the real route.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const KEY = "assay_method";
const DETAIL_URL = `/admin/properties/${KEY}`;
const PROPERTY_PATH = `/registry/properties/${KEY}`;
const DEPRECATION_PATH = `${PROPERTY_PATH}/deprecation`;

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
    constraints: {},
    row_version: 3,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const READ_OK = { method: "GET", path: PROPERTY_PATH, status: 200, body: definition() };
const REASON = "Replaced by a coded property";

function deprecationCalls(calls: ReturnType<typeof stubApi>) {
  return calls.filter(
    (call) => call.method === "POST" && call.path.endsWith(DEPRECATION_PATH),
  );
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await renderRoute(DETAIL_URL, SIGNED_IN);
  const trigger = await screen.findByRole("button", { name: "Deprecate property" });
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "Deprecate Assay method" });
  return { trigger, dialog };
}

async function confirm(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.type(within(dialog).getByLabelText("Reason"), REASON);
  await user.click(within(dialog).getByRole("button", { name: "Deprecate property" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Deprecate property", () => {
  it("is offered for an active property and not for one already deprecated", async () => {
    stubApi([{ ...READ_OK, body: definition({ status: "deprecated" }) }]);

    await renderRoute(DETAIL_URL, SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Assay method" });
    expect(screen.getByRole("link", { name: "Edit property" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Deprecate property" }),
    ).not.toBeInTheDocument();
  });

  it("warns that deprecation cannot be undone before asking for a reason", async () => {
    stubApi([READ_OK]);
    const user = userEvent.setup();

    const { dialog } = await openDialog(user);

    expect(within(dialog).getByText(/cannot be undone/)).toBeVisible();
    expect(within(dialog).getByText(/create a new property/)).toBeVisible();
    expect(within(dialog).getByLabelText("Reason")).toHaveFocus();
  });

  it("closes with Cancel and Escape and returns focus to the button that opened it", async () => {
    stubApi([READ_OK]);
    const user = userEvent.setup();
    const { trigger, dialog } = await openDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await screen.findByRole("dialog", { name: "Deprecate Assay method" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("sends nothing and asks for a reason when it is empty", async () => {
    const calls = stubApi([READ_OK]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Deprecate property" }));

    const summary = await within(dialog).findByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(
      within(dialog).getByRole("link", {
        name: "Enter the reason for deprecating this property.",
      }),
    ).toBeVisible();
    expect(deprecationCalls(calls)).toHaveLength(0);
  });

  it("deprecates, closes the dialog, shows the new status and announces it", async () => {
    // Keyed on the write having happened, not on a call count: StrictMode
    // fetches the property twice on the first render.
    let written = false;
    const calls = stubApi(
      [
        READ_OK,
        {
          method: "POST",
          path: DEPRECATION_PATH,
          status: 200,
          body: definition({ status: "deprecated", row_version: 4 }),
        },
      ],
      {
        vary: (call) => {
          if (call.method === "POST") {
            written = true;
          }
          return written && call.method === "GET" && call.path.endsWith(PROPERTY_PATH)
            ? {
                method: "GET",
                path: call.path,
                status: 200,
                body: definition({ status: "deprecated", row_version: 4 }),
              }
            : null;
        },
      },
    );
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(deprecationCalls(calls)).toHaveLength(1);
    expect(deprecationCalls(calls)[0]?.body).toEqual({
      expected_row_version: 3,
      reason: REASON,
    });
    // The status shows as text, and the control that opened the dialog is gone.
    await waitFor(() => expect(screen.getByText("Deprecated")).toBeVisible());
    expect(
      screen.queryByRole("button", { name: "Deprecate property" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Assay method is now deprecated.",
      ),
    );
    // Focus lands on the heading, not on a button that no longer exists.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: "Assay method" }),
      ).toHaveFocus(),
    );
  });

  it("shows the system-property refusal in the dialog and leaves it open", async () => {
    const detail = "A built-in system property cannot be deprecated.";
    stubApi([
      READ_OK,
      { method: "POST", path: DEPRECATION_PATH, status: 409, body: { detail } },
    ]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    expect(await within(dialog).findByText(detail)).toBeVisible();
    const summary = within(dialog).getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(within(dialog).getByLabelText("Reason")).toHaveValue(REASON);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the already-deprecated refusal in the dialog", async () => {
    const detail = "This property is already deprecated.";
    stubApi([
      READ_OK,
      { method: "POST", path: DEPRECATION_PATH, status: 409, body: { detail } },
    ]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    expect(await within(dialog).findByText(detail)).toBeVisible();
  });

  it("reloads after an already-deprecated refusal, so the button goes and Cancel lands on the heading", async () => {
    const detail = "This property is already deprecated.";
    let written = false;
    stubApi(
      [
        READ_OK,
        { method: "POST", path: DEPRECATION_PATH, status: 409, body: { detail } },
      ],
      {
        vary: (call) => {
          if (call.method === "POST") {
            written = true;
          }
          // Someone else deprecated it while the dialog was open.
          return written && call.method === "GET" && call.path.endsWith(PROPERTY_PATH)
            ? {
                method: "GET",
                path: call.path,
                status: 200,
                body: definition({ status: "deprecated", row_version: 4 }),
              }
            : null;
        },
      },
    );
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    expect(await within(dialog).findByText(detail)).toBeVisible();
    // Only the dialog's own button is left: the one on the page went with the status.
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Deprecate property" })).toHaveLength(
        1,
      ),
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1, name: "Assay method" })).toHaveFocus();
    expect(screen.getByText("Deprecated")).toBeVisible();
  });

  it("drops an earlier server refusal when the reason check fails on the next try", async () => {
    const detail = "A built-in system property cannot be deprecated.";
    stubApi([
      READ_OK,
      { method: "POST", path: DEPRECATION_PATH, status: 409, body: { detail } },
    ]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);
    await confirm(user, dialog);
    expect(await within(dialog).findByText(detail)).toBeVisible();

    await user.clear(within(dialog).getByLabelText("Reason"));
    await user.click(within(dialog).getByRole("button", { name: "Deprecate property" }));

    expect(
      await within(dialog).findByRole("link", {
        name: "Enter the reason for deprecating this property.",
      }),
    ).toBeVisible();
    expect(within(dialog).queryByText(detail)).not.toBeInTheDocument();
  });

  it("shows the refusal to a caller without registry.manage", async () => {
    const detail = "You do not have permission to manage the property registry.";
    stubApi([
      READ_OK,
      { method: "POST", path: DEPRECATION_PATH, status: 403, body: { detail } },
    ]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    expect(await within(dialog).findByText(detail)).toBeVisible();
    const summary = within(dialog).getByText("There is a problem");
    await waitFor(() => expect(summary.closest("[tabindex='-1']")).toHaveFocus());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("explains a stale-version refusal and reloads the property behind the dialog", async () => {
    const conflict = {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: KEY,
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: null,
      changed_at: null,
    };
    // Keyed on the write having happened, not on a call count: StrictMode
    // fetches the property twice on the first render.
    let written = false;
    const calls = stubApi(
      [READ_OK, { method: "POST", path: DEPRECATION_PATH, status: 409, body: conflict }],
      {
        vary: (call) => {
          if (call.method === "POST") {
            written = true;
          }
          return written && call.method === "GET" && call.path.endsWith(PROPERTY_PATH)
            ? {
                method: "GET",
                path: call.path,
                status: 200,
                body: definition({ label: "Assay method v2", row_version: 4 }),
              }
            : null;
        },
      },
    );
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await confirm(user, dialog);

    expect(
      await within(dialog).findByText(
        /Someone else changed this property while you had it open/,
      ),
    ).toBeVisible();
    // The property reloads behind the dialog, so its title now names the new label.
    expect(
      await screen.findByRole("dialog", { name: "Deprecate Assay method v2" }),
    ).toBeVisible();

    await user.click(within(dialog).getByRole("button", { name: "Deprecate property" }));

    await waitFor(() => expect(deprecationCalls(calls)).toHaveLength(2));
    expect(deprecationCalls(calls).map((call) => call.body)).toEqual([
      { expected_row_version: 3, reason: REASON },
      { expected_row_version: 4, reason: REASON },
    ]);
  });

  it("has no automated accessibility violations with the dialog open, with and without errors", async () => {
    stubApi([READ_OK]);
    const user = userEvent.setup();
    const { dialog } = await openDialog(user);

    await expectNoA11yViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Deprecate property" }));
    await within(dialog).findByText("There is a problem");
    await expectNoA11yViolations(dialog);
  });
});
