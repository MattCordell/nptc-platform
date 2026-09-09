import { act, screen, waitFor, within } from "@testing-library/react";
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
    entrySummary({
      business_key: DRAFT_KEY,
      preferred_term: "Ferritin",
      status: "draft",
    }),
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

const DISCIPLINE_VALUES_MULTI_OK: Route = {
  method: "GET",
  path: "/registry/properties/discipline/values",
  status: 200,
  body: {
    items: [
      { code: "chemistry", display: "Chemistry" },
      { code: "haematology", display: "Haematology" },
    ],
    total: 2,
  },
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
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/entries"))).toBe(
      true,
    );
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/search"))).toBe(
      false,
    );
  });

  it("dispatches to the search route once q is set in the URL", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(`${LIST_URL}?q=glucose`, SIGNED_IN);

    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith("/catalogue/admin/search"))).toBe(
        true,
      ),
    );
    expect(calls.some((call) => call.path.endsWith("/catalogue/admin/entries"))).toBe(
      false,
    );
  });

  // Acceptance criterion: an administrator can find a draft entry from the
  // list screen and reach its edit screen without typing a URL.
  it("finds a draft entry and follows it to its edit screen", async () => {
    stubApi([
      ENTRIES_OK,
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
      {
        method: "GET",
        path: `/catalogue/admin/entries/${DRAFT_KEY}`,
        status: 200,
        body: {},
      },
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

    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Draft" })).toBeChecked(),
    );
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
    expect(
      screen.queryByRole("textbox", { name: "Filter Volume" }),
    ).not.toBeInTheDocument();
  });

  // PR #285 review finding 1: a `filter.*` the panel renders no control for
  // (not `concept_picker`, or since dropped from the registry) used to be
  // unclearable from a bookmarked or shared link - the exact FR-36 scenario
  // this screen exists for.
  describe("active filters escape hatch", () => {
    it("shows a removable chip for a filter the panel has no control for, and clears it", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(`${LIST_URL}?filter.volume_ml=5`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      // `volume_ml` is filterable but not `concept_picker` - the panel
      // renders no checkbox or text box for it (see the test above), so the
      // chip is the only control that names it at all. The facet key still
      // resolves to its registry label ("Volume"), since `volume_ml` is a
      // known property - only the value stays raw, as there is no
      // value-options source to resolve a `number` datatype against (issue
      // #289's own acceptance criterion).
      expect(screen.queryByRole("checkbox", { name: /volume/i })).not.toBeInTheDocument();
      const chip = screen.getByRole("button", { name: "Remove filter Volume: 5" });
      expect(chip).toBeInTheDocument();

      await user.click(chip);

      await waitFor(() =>
        expect(router.state.location.href).not.toContain("filter.volume_ml"),
      );
      expect(
        screen.queryByRole("button", { name: "Remove filter Volume: 5" }),
      ).not.toBeInTheDocument();
    });

    // The true escape hatch (issue #289): a `filter.*` key with no
    // `PropertyDefinition` at all - not merely one the panel offers no
    // control for - stays fully raw on both key and value.
    it("keeps a chip fully raw for a facet key absent from the registry entirely", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.mystery_facet=raw_value`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        screen.getByRole("button", { name: "Remove filter mystery_facet: raw_value" }),
      ).toBeInTheDocument();
    });

    // Issue #289: a coded (`concept_picker`) property's chip resolves both
    // the facet key and the selected value to the same labels the filter
    // panel shows for the identical selection.
    it("resolves a coded property's chip to its registry label and display value", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.discipline=chemistry`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: Chemistry",
        }),
      ).toBeInTheDocument();
    });

    // Issue #289: a coded value not present in the fetched value-options page
    // (filtered out, a retired code, or the fetch erroring) falls back to the
    // raw code as its own label - mirroring `PropertyFacetGroup`'s
    // `carriedOptions` fallback - rather than showing blank or "undefined".
    it("falls back to the raw code for a coded value absent from the fetched page", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.discipline=retired_code`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: retired_code",
        }),
      ).toBeInTheDocument();
    });

    // Issue #289: the `status` facet resolves against the same
    // `STATUS_OPTIONS` labels the filter panel itself renders.
    it("resolves the status facet's chip via STATUS_OPTIONS", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.status=active`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        screen.getByRole("button", { name: "Remove filter Status: Active" }),
      ).toBeInTheDocument();
    });

    // Issue #289 review: `codedActiveFacetKeys` dedups by facet key so two
    // selected values on the same coded facet share one value-options fetch
    // rather than one per value - this is the principal failure mode of that
    // dedup, and the `calls.filter(...).toBe(1)` assertion below is what
    // actually catches a regression to one fetch per value.
    it("resolves both chips for two selected values on the same coded facet from one fetch", async () => {
      const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_MULTI_OK]);

      await renderRoute(
        `${LIST_URL}?filter.discipline=chemistry&filter.discipline=haematology`,
        SIGNED_IN,
      );
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: Chemistry",
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Remove filter Discipline: Haematology" }),
      ).toBeInTheDocument();
      // One fetch, not two - `PropertyFacetGroup`'s own value-options query
      // (fired for the same unfiltered discipline facet, matching cache key)
      // is the only other legitimate source of a call to this path, so more
      // than one indicates the dedup did not hold.
      expect(
        calls.filter((call) =>
          call.path.endsWith("/registry/properties/discipline/values"),
        ).length,
      ).toBe(1);
    });

    it("clears every active filter at once via Clear all filters", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(
        `${LIST_URL}?filter.status=draft&filter.volume_ml=5`,
        SIGNED_IN,
      );
      await screen.findByRole("link", { name: DRAFT_KEY });

      await user.click(screen.getByRole("button", { name: "Clear all filters" }));

      await waitFor(() => {
        expect(router.state.location.href).not.toContain("filter.status");
        expect(router.state.location.href).not.toContain("filter.volume_ml");
      });
      expect(screen.getByRole("checkbox", { name: "Draft" })).not.toBeChecked();
      expect(
        screen.queryByRole("button", { name: "Clear all filters" }),
      ).not.toBeInTheDocument();
    });

    // Scenario 2 from the review: a filter the server refuses (deprecated
    // or un-filterable since the link was shared) leaves the whole screen on
    // a refusal message - the chip/Clear all controls must stay reachable,
    // since they are the only way out of that state.
    it("keeps the filter controls reachable even while the listing itself is refused", async () => {
      stubApi([
        {
          method: "GET",
          path: "/catalogue/admin/entries",
          status: 422,
          body: { detail: "Filter is not available: volume_ml" },
        },
        PROPERTIES_OK,
        DISCIPLINE_VALUES_OK,
      ]);

      await renderRoute(`${LIST_URL}?filter.volume_ml=5`, SIGNED_IN);

      expect(
        await screen.findByText("Filter is not available: volume_ml"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Remove filter Volume: 5" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Clear all filters" }),
      ).toBeInTheDocument();
    });

    it("has no chip and no Clear all filters control when nothing is selected", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        screen.queryByRole("button", { name: "Clear all filters" }),
      ).not.toBeInTheDocument();
    });
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

      await user.click(
        screen.getByRole("checkbox", { name: "Select all rows on this page" }),
      );
      expect(await screen.findByRole("status")).toHaveTextContent("2 rows selected.");

      await user.click(
        screen.getByRole("checkbox", { name: "Select all rows on this page" }),
      );
      expect(await screen.findByRole("status")).toHaveTextContent("No rows selected.");
    });

    it("clears the selection once the search query changes the population", async () => {
      stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });
      await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
      expect(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` })).toBeChecked();

      const searchBox = screen.getByRole("textbox", {
        name: "Search term or SNOMED CT code",
      });
      await user.type(searchBox, "glucose");
      await user.click(screen.getByRole("button", { name: "Search" }));
      await screen.findByRole("link", { name: ACTIVE_KEY });

      // Clearing q returns to browse mode and re-renders the very same
      // DRAFT_KEY row - if the selection had merely been hidden rather than
      // actually cleared, its checkbox would still read checked here.
      await user.clear(searchBox);
      await user.click(screen.getByRole("button", { name: "Search" }));
      await screen.findByRole("link", { name: DRAFT_KEY });

      expect(
        screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }),
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

  // PR #285 review finding 2: every fixture elsewhere in this file has
  // `next_cursor: null`, so paging itself, selection surviving a page
  // change, and `after` being dropped once a filter is then toggled had no
  // coverage at all.
  describe("paging", () => {
    const PAGE_1 = {
      items: [
        entrySummary({
          business_key: DRAFT_KEY,
          preferred_term: "Ferritin",
          status: "draft",
        }),
      ],
      next_cursor: DRAFT_KEY,
    };
    const PAGE_2 = {
      items: [
        entrySummary({
          business_key: ACTIVE_KEY,
          preferred_term: "Full blood count",
          status: "active",
        }),
      ],
      next_cursor: null,
    };

    // Keyed on `after` itself, not a call count (PR #285 review round 2):
    // `path` alone can't tell a first-page request from a second's - both hit
    // the identical path, and only the query string differs - and a
    // call-count-based `vary` is flaky by construction under this app's own
    // `<StrictMode>` (`render-route.tsx`): the initial mount's own query
    // fetches `/catalogue/admin/entries` twice (matching
    // `admin-catalogue-edit.test.tsx`'s documented "two reads under
    // StrictMode"), so a counter would reach 2 - "page two" - before the
    // test ever clicks "Next page". Both duplicate initial reads carry no
    // `after` and get the identical first page.
    function stubTwoPages() {
      return stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
        vary: (call) => {
          if (call.method === "GET" && call.path.endsWith("/catalogue/admin/entries")) {
            const body = call.searchParams.get("after") === null ? PAGE_1 : PAGE_2;
            return { method: "GET", path: call.path, status: 200, body };
          }
          return null;
        },
      });
    }

    it("(a) pushes the cursor into the URL and shows the next page's entries", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await user.click(screen.getByRole("button", { name: "Next page" }));

      await screen.findByRole("link", { name: ACTIVE_KEY });
      expect(router.state.location.href).toContain(`after=${DRAFT_KEY}`);
      expect(screen.queryByRole("link", { name: DRAFT_KEY })).not.toBeInTheDocument();
      // Keyset-paginated (ADR-0024): no "previous page" control exists.
      expect(
        screen.queryByRole("button", { name: /previous page/i }),
      ).not.toBeInTheDocument();
    });

    it("(b) keeps a row checked on an earlier page selected after paging forward", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await user.click(screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }));
      expect(await screen.findByRole("status")).toHaveTextContent("1 row selected.");

      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_KEY });

      // Selecting this page's own row on top of the still-checked prior
      // one proves the earlier selection survived the page change - if it
      // had been cleared, this announcement would read "1 row selected."
      await user.click(screen.getByRole("checkbox", { name: `Select ${ACTIVE_KEY}` }));
      expect(await screen.findByRole("status")).toHaveTextContent("2 rows selected.");
    });

    it("(c) drops the after cursor once a filter is toggled from a later page", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_KEY });
      expect(router.state.location.href).toContain("after=");

      await user.click(screen.getByRole("checkbox", { name: "Active" }));

      await waitFor(() =>
        expect(router.state.location.href).toContain("filter.status=active"),
      );
      expect(router.state.location.href).not.toContain("after=");
    });
  });

  it("shows a refusal message when the listing cannot be loaded", async () => {
    stubApi([
      {
        method: "GET",
        path: "/catalogue/admin/entries",
        status: 500,
        body: { detail: "boom" },
      },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  // PR #285 review finding 3: the hard-failure paragraph used to be
  // rendered but never announced - silence for a screen-reader user.
  it("announces a refusal message when the listing cannot be loaded", async () => {
    stubApi([
      {
        method: "GET",
        path: "/catalogue/admin/entries",
        status: 500,
        body: { detail: "boom" },
      },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    // `waitFor`, not `findByRole` then a separate assertion: the live
    // region is present (and empty) from first render (`LiveRegion`'s own
    // docstring - a screen reader needs it mounted before the text change,
    // not created and filled in the same tick), so `findByRole("status")`
    // alone resolves the instant it exists, racing `useAnnounce`'s
    // `setTimeout(0)` that actually fills it in.
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("boom"));
  });

  // PR #285 review finding 2: this branch (a fetch that fails while a
  // *previous* successful fetch's data is still on screen) had no coverage
  // at all - only the initial-load failure above did. `queryClient` is
  // driven directly because nothing in this page's own UI otherwise
  // triggers a background refetch of the identical query on demand.
  it("shows a stale-data warning, not a blank screen, when a refresh fails over data already shown", async () => {
    // A flag the test itself flips, not a call count: the initial mount's
    // own query fetches this path twice under `<StrictMode>` (see the
    // "paging" describe block's own comment on this), so a counter reaching
    // 2 would already misfire the initial load rather than only the
    // deliberate refetch below.
    let shouldFail = false;
    stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
      vary: (call) => {
        if (call.method === "GET" && call.path.endsWith("/catalogue/admin/entries")) {
          return shouldFail
            ? { method: "GET", path: call.path, status: 500, body: { detail: "boom" } }
            : ENTRIES_OK;
        }
        return null;
      },
    });

    const { queryClient } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    shouldFail = true;
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: ["api", "/api/v1/catalogue/admin/entries"],
      });
    });

    // Two elements carry this text on purpose - the visible warning
    // paragraph and the live region announcing it (`STALE_DATA_WARNING`'s
    // own docstring: "one string ... so the two cannot drift apart").
    expect(
      await screen.findAllByText(
        "Catalogue entries could not be refreshed just now, so what follows may be out of date.",
      ),
    ).toHaveLength(2);
    // The previously-loaded row is still shown - a refresh failure does not
    // blank out data already on screen.
    expect(screen.getByRole("link", { name: DRAFT_KEY })).toBeInTheDocument();
  });

  it("shows the empty state, not a headers-only table, when there are no entries", async () => {
    stubApi([
      {
        method: "GET",
        path: "/catalogue/admin/entries",
        status: 200,
        body: { items: [], next_cursor: null },
      },
      PROPERTIES_OK,
      DISCIPLINE_VALUES_OK,
    ]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(
      await screen.findByText("No catalogue entries match this filter."),
    ).toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    const { container } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_KEY });

    await expectNoA11yViolations(container);
  });

  // Issue #63's bulk reclassify, driven end to end from this screen -
  // `bulk-reclassify-dialog.test.tsx` covers the dialog's own gates and
  // submit behaviour in isolation; this is the dedicated coverage that test
  // file's own "PUTs the whole selection..." test points readers at, for
  // what happens on *this* screen once that dialog completes (PR #290
  // review).
  describe("bulk reclassify", () => {
    // A text-valued active property, matching `bulk-reclassify-dialog.test
    // .tsx`'s own `usage_guidance` fixture, so a value can be typed with no
    // extra concept-picker network round trip.
    const PROPERTIES_WITH_USAGE_GUIDANCE: Route = {
      method: "GET",
      path: "/registry/properties",
      status: 200,
      body: {
        items: [
          ...(PROPERTIES_OK.body as { items: unknown[] }).items,
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

    function bulkWriteOk(): Route {
      return {
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
      };
    }

    async function runBulkReclassify(user: ReturnType<typeof userEvent.setup>) {
      await user.click(
        screen.getByRole("checkbox", { name: "Select all rows on this page" }),
      );
      await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
      const dialog = within(await screen.findByRole("dialog"));
      await user.selectOptions(dialog.getByLabelText("Property"), "usage_guidance");
      await user.type(dialog.getByLabelText("Usage guidance"), "Fasting required");
      await user.type(dialog.getByLabelText("Changelog note"), "Reclassify both");
      await user.click(dialog.getByRole("button", { name: "Reclassify" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }

    it("clears the selection, shows a durable results panel, and moves focus to it", async () => {
      stubApi([
        ENTRIES_OK,
        PROPERTIES_WITH_USAGE_GUIDANCE,
        DISCIPLINE_VALUES_OK,
        bulkWriteOk(),
      ]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await runBulkReclassify(user);

      // Selection cleared: the toolbar (only rendered while something is
      // selected) is gone, and both row checkboxes are unchecked.
      expect(
        screen.queryByRole("button", { name: "Reclassify selected" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("checkbox", { name: `Select ${DRAFT_KEY}` }),
      ).not.toBeChecked();

      // A durable results panel - present after the dialog that produced it
      // has closed, not tied to the dialog's own lifetime.
      const results = screen.getByRole("region", {
        name: "Reclassify Usage guidance: results",
      });
      expect(results).toHaveTextContent(
        "2 applied, 0 unchanged, 0 conflicts, 0 not found.",
      );

      // Focus lands on the results section itself, not on <body> - `Dialog`'s
      // own focus-restore targets the "Reclassify selected" button, which no
      // longer exists once the selection it completed against is cleared
      // (PR #290 review).
      expect(document.activeElement).toBe(results);
    });

    it("announces the results outcome, not a stale 'no rows selected'", async () => {
      stubApi([
        ENTRIES_OK,
        PROPERTIES_WITH_USAGE_GUIDANCE,
        DISCIPLINE_VALUES_OK,
        bulkWriteOk(),
      ]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });

      await runBulkReclassify(user);

      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "Reclassify Usage guidance: 2 applied, 0 unchanged, 0 conflicts, 0 not found.",
        ),
      );
    });

    it("clears the previous batch's results panel as soon as a new one is launched", async () => {
      stubApi([
        ENTRIES_OK,
        PROPERTIES_WITH_USAGE_GUIDANCE,
        DISCIPLINE_VALUES_OK,
        bulkWriteOk(),
      ]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_KEY });
      await runBulkReclassify(user);
      expect(
        screen.getByRole("region", { name: "Reclassify Usage guidance: results" }),
      ).toBeInTheDocument();

      // A second batch is launched but not yet submitted - the first
      // batch's tallies must not still be on screen behind the dialog,
      // where they would read as this batch's own outcome (PR #290 review).
      await user.click(
        screen.getByRole("checkbox", { name: "Select all rows on this page" }),
      );
      await user.click(screen.getByRole("button", { name: "Reclassify selected" }));
      await screen.findByRole("dialog");

      expect(
        screen.queryByRole("region", { name: "Reclassify Usage guidance: results" }),
      ).not.toBeInTheDocument();
    });
  });
});
