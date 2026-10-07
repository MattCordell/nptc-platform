import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The property registry list screen (FR-08..13, NFR-31), driven through the
 * real route like the other admin screen tests.
 */

const LIST_URL = "/admin/properties/";

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

function property(overrides: Record<string, unknown>) {
  return {
    key: "placeholder",
    label: "Placeholder",
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

const ACTIVE_ITEM = property({
  key: "discipline",
  label: "Discipline",
  datatype: "code",
  scope: "both",
  form_control: { control: "concept_picker", params: {} },
});
const MAINTENANCE_ITEM = property({
  key: "usage_guidance",
  label: "Usage guidance",
  scope: "maintenance",
  display_order: 20,
});
const DEPRECATED_ITEM = property({
  key: "legacy_flag",
  label: "Legacy flag",
  scope: "submission",
  origin: "admin",
  status: "deprecated",
  display_order: 30,
});

function propertiesRoute(items: unknown[]): Route {
  return { method: "GET", path: "/registry/properties", status: 200, body: { items } };
}

const PROPERTIES_OK = propertiesRoute([ACTIVE_ITEM, MAINTENANCE_ITEM, DEPRECATED_ITEM]);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminPropertyListPage", () => {
  it("lists key, label, datatype, scope and status for each active property", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    const link = await screen.findByRole("link", { name: "discipline" });
    const row = within(link.closest("tr") as HTMLElement);
    expect(row.getByText("Discipline")).toBeInTheDocument();
    expect(row.getByText("code")).toBeInTheDocument();
    expect(row.getByText("Both")).toBeInTheDocument();
    expect(row.getByText("Active")).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: "Registry properties" }),
    ).toBeInTheDocument();
  });

  it("links each key to its detail screen", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByRole("link", { name: "usage_guidance" })).toHaveAttribute(
      "href",
      "/admin/properties/usage_guidance",
    );
  });

  it("has exactly one h1 and summarises the registry in its meta line", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    await screen.findByRole("link", { name: "discipline" });
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Property registry");
    expect(screen.getByText("3 properties: 2 active, 1 deprecated")).toBeInTheDocument();
  });

  it("gives each key link a 24px minimum target height", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByRole("link", { name: "discipline" })).toHaveClass(
      "min-h-6",
    );
  });

  it("hides deprecated properties until asked to show them", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    await screen.findByRole("link", { name: "discipline" });
    expect(screen.queryByRole("link", { name: "legacy_flag" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Show deprecated properties" }),
    ).not.toBeChecked();
  });

  it("shows a deprecated row with the word Deprecated, not colour alone, once toggled on", async () => {
    stubApi([PROPERTIES_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: "discipline" });

    await user.click(
      screen.getByRole("checkbox", { name: "Show deprecated properties" }),
    );

    const link = await screen.findByRole("link", { name: "legacy_flag" });
    expect(
      within(link.closest("tr") as HTMLElement).getByText("Deprecated"),
    ).toBeVisible();
    await waitFor(() => expect(router.state.location.href).toContain("deprecated=show"));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Showing 3 properties."),
    );
  });

  it("hides deprecated properties again when toggled off, and drops the URL flag", async () => {
    stubApi([PROPERTIES_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute(`${LIST_URL}?deprecated=show`, SIGNED_IN);
    await screen.findByRole("link", { name: "legacy_flag" });

    await user.click(
      screen.getByRole("checkbox", { name: "Show deprecated properties" }),
    );

    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "legacy_flag" })).not.toBeInTheDocument(),
    );
    expect(router.state.location.href).not.toContain("deprecated");
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Showing 2 properties."),
    );
  });

  it("restores the deprecated view from a pasted link", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(`${LIST_URL}?deprecated=show`, SIGNED_IN);

    expect(await screen.findByRole("link", { name: "legacy_flag" })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Show deprecated properties" }),
    ).toBeChecked();
  });

  it("ignores an unrecognised deprecated value rather than showing deprecated rows", async () => {
    stubApi([PROPERTIES_OK]);

    await renderRoute(`${LIST_URL}?deprecated=yes`, SIGNED_IN);

    await screen.findByRole("link", { name: "discipline" });
    expect(screen.queryByRole("link", { name: "legacy_flag" })).not.toBeInTheDocument();
  });

  it("toggles with the keyboard alone", async () => {
    stubApi([PROPERTIES_OK]);
    const user = userEvent.setup();

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: "discipline" });

    // The skip link and site navigation come first in the tab order.
    const checkbox = screen.getByRole("checkbox", { name: "Show deprecated properties" });
    for (
      let presses = 0;
      presses < 25 && document.activeElement !== checkbox;
      presses += 1
    ) {
      await user.tab();
    }
    expect(checkbox).toHaveFocus();
    await user.keyboard(" ");

    expect(await screen.findByRole("link", { name: "legacy_flag" })).toBeInTheDocument();
  });

  it("shows an empty state when the registry has no properties", async () => {
    stubApi([propertiesRoute([])]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(
      await screen.findByText("No properties are defined in the registry yet."),
    ).toBeInTheDocument();
  });

  it("says why the table is empty when every property is deprecated", async () => {
    stubApi([propertiesRoute([DEPRECATED_ITEM])]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(
      await screen.findByText(
        "Every property is deprecated. Show deprecated properties to see them.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the server's refusal when the caller lacks registry.read, and announces it", async () => {
    stubApi([
      {
        method: "GET",
        path: "/registry/properties",
        status: 403,
        body: { detail: "You do not have permission to view the property registry." },
      },
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(
      await screen.findByText(
        "You do not have permission to view the property registry.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "You do not have permission to view the property registry.",
      ),
    );
  });

  it("falls back to a next-step message when the failure carries no detail", async () => {
    stubApi([{ method: "GET", path: "/registry/properties", status: 500, body: {} }]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(
      await screen.findByText(
        "The property registry could not be loaded. Try again, or contact an administrator if the problem persists.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the rows on screen and warns when a refresh fails", async () => {
    // A flag the test flips, not a call count: StrictMode fetches the initial
    // query twice.
    let shouldFail = false;
    stubApi([], {
      vary: (call) => {
        if (call.method === "GET" && call.path.endsWith("/registry/properties")) {
          return shouldFail
            ? { method: "GET", path: call.path, status: 500, body: { detail: "boom" } }
            : PROPERTIES_OK;
        }
        return null;
      },
    });

    const { queryClient } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: "discipline" });

    shouldFail = true;
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: ["api", "/api/v1/registry/properties"],
      });
    });

    // The visible paragraph and the live region carry the same sentence. The
    // live region fills a tick later, so wait for both.
    await waitFor(() =>
      expect(
        screen.getAllByText(
          "The property registry could not be refreshed just now, so what follows may be out of date. Reload the page to try again.",
        ),
      ).toHaveLength(2),
    );
    expect(screen.getByRole("link", { name: "discipline" })).toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    stubApi([PROPERTIES_OK]);

    const { container } = await renderRoute(`${LIST_URL}?deprecated=show`, SIGNED_IN);
    await screen.findByRole("link", { name: "legacy_flag" });

    await expectNoA11yViolations(container);
  });
});
