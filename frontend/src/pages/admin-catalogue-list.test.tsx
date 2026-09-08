import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The admin catalogue list screen (issue #267; FR-14, FR-15, FR-16, FR-36,
 * NFR-31).
 *
 * Driven through the real route, matching `admin-catalogue-edit.test.tsx`'s
 * own convention: what is under test is the shipped screen, not a component
 * mounted in isolation.
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

const ENTRIES_PAGE = {
  items: [
    entrySummary({ business_key: DRAFT_KEY, preferred_term: "Ferritin", status: "draft" }),
    entrySummary({
      business_key: ACTIVE_KEY,
      preferred_term: "Full blood count",
      status: "active",
    }),
  ],
  next_cursor: null,
};

const ENTRIES_OK: Route = {
  method: "GET",
  path: "/catalogue/admin/entries",
  status: 200,
  body: ENTRIES_PAGE,
};

const SEARCH_OK: Route = {
  method: "GET",
  path: "/catalogue/admin/search",
  status: 200,
  body: {
    items: [{ ...entrySummary({ business_key: ACTIVE_KEY }), score: 0.9 }],
    next_cursor: null,
    facets: [],
  },
};

// A filterable, active, concept_picker property (issue #267's browse-mode
// resolution) - matching `properties-panel.test.tsx`'s own `discipline`
// fixture shape.
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
      // Filterable but not a concept_picker - omitted from the browse-mode
      // panel (issue #267's own open-question resolution).
      {
        key: "volume_ml",
        label: "Volume",
        datatype: "number",
        cardinality: "0..1",
        scope: "both",
        required_for_submission: false,
        required_for_publication: false,
        binding_target: null,
        value_set_uri: null,
        strength: null,
        edition: null,
        local_code_system_key: null,
        filterable: true,
        origin: "system",
        status: "active",
        display_order: 20,
        constraints: {},
        row_version: 1,
        form_control: { control: "number", params: {} },
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

describe("AdminCatalogueListPage", () => {
  it("browses by default (no q) and lists every status", async () => {
    const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByRole("link", { name: DRAFT_KEY })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: ACTIVE_KEY })).toBeInTheDocument();
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/entries"))).toBe(true);
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/search"))).toBe(false);
  });

  it("dispatches to the search route once q is set in the URL", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(`${LIST_URL}?q=glucose`, SIGNED_IN);

    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith("/catalogue/admin/search"))).toBe(true),
    );
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/entries"))).toBe(false);
  });

  // Acceptance criterion: an administrator can find a draft entry from the
  // list screen and reach its edit screen without typing a URL.
  it("finds a draft entry and follows it to its edit screen", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
      { method: "GET", path: `/catalogue/admin/entries/${DRAFT_KEY}`, status: 200, body: {} },
    ]);
    const user = userEvent.setup();

    const { router } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    await user.click(screen.getByRole("link", { name: DRAFT_KEY }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`/admin/catalogue/${DRAFT_KEY}/edit`),
    );
  });

  // Acceptance criterion: filter state survives a page reload and a pasted
  // link.
  it("restores filter selections from a pasted link", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(`${LIST_URL}?filter.status=draft`, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    expect(screen.getByRole("checkbox", { name: "Draft" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Active" })).not.toBeChecked();
  });

  it("navigates with the toggled filter value when a facet box is checked", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    await user.click(screen.getByRole("checkbox", { name: "Draft" }));

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Draft" })).toBeChecked());
    expect(router.state.location.href).toContain("filter.status=draft");
  });

  it("omits a filterable property with no concept_picker control from the panel", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    expect(
      await screen.findByRole("checkbox", { name: "Chemistry" }, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Volume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Filter Volume" })).not.toBeInTheDocument();
  });

  // Acceptance criterion: rows can be selected individually and all at
  // once, and the selection is announced accessibly.
  describe("row selection", () => {
    it("selects one row, then all rows, announcing the count each time", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
      expect(await screen.findByRole("status")).toHaveTextContent("1 row selected.");

      await user.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
      expect(await screen.findByRole("status")).toHaveTextContent("2 rows selected.");

      await user.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
      expect(await screen.findByRole("status")).toHaveTextContent("No rows selected.");
    });

    it("clears the selection once the search query changes the population", async () => {
      stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });
      await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
      expect(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` })).toBeChecked();

      await user.type(
        screen.getByRole("textbox", { name: "Search term or SNOMED CT code" }),
        "glucose",
      );
      await user.click(screen.getByRole("button", { name: "Search" }));

      await screen.findByRole("link", { name: ACTIVE_KEY });
      expect(
        screen.queryByRole("checkbox", { name: `Select ${ACTIVE_KEY}` }),
      ).not.toBeChecked();
    });

    it("is operable by keyboard alone", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      const rowCheckbox = screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` });
      rowCheckbox.focus();
      await user.keyboard("{ }");

      expect(rowCheckbox).toBeChecked();
    });
  });

  it("shows a refusal message when the listing cannot be loaded", async () => {
    stubApi([
      { method: "GET", path: "/catalogue/admin/entries", status: 500, body: { detail: "boom" } },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("shows the empty state, not a headers-only table, when there are no entries", async () => {
    stubApi([
      { method: "GET", path: "/catalogue/admin/entries", status: 200, body: { items: [], next_cursor: null } },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByText("No catalogue entries match this filter.")).toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    const { container } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    await expectNoA11yViolations(container);
  });
});
