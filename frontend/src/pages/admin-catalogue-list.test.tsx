import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The editor's entry point at `/admin/catalogue/` (FR-14, FR-15, FR-16, FR-36,
 * NFR-31), driven through the real route, as `admin-catalogue-edit.test.tsx`
 * is: what is under test is the shipped screen.
 *
 * Requests are told apart by their query string, never by a call count:
 * `<StrictMode>` fetches the first page twice.
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
const DRAFT_TERM = "Ferritin";
const ACTIVE_TERM = "Full blood count";

function entrySummary(overrides: Record<string, unknown>) {
  return {
    business_key: "NPTC-000000",
    preferred_term: "Placeholder",
    length: 11,
    status: "active",
    updated_at: "2026-09-01T04:30:00Z",
    has_open_finding: false,
    code: null,
    disciplines: [],
    label_provenance: { preferred_term: { source: "catalogue" } },
    row_version: 1,
    ...overrides,
  };
}

const DRAFT_ROW = entrySummary({
  business_key: DRAFT_KEY,
  preferred_term: DRAFT_TERM,
  status: "draft",
});
const ACTIVE_ROW = entrySummary({
  business_key: ACTIVE_KEY,
  preferred_term: ACTIVE_TERM,
  status: "active",
  disciplines: ["Haematology"],
});

const ENTRIES_OK: Route = {
  method: "GET",
  path: "/catalogue/admin/entries",
  status: 200,
  body: { items: [DRAFT_ROW, ACTIVE_ROW], next_cursor: null },
};

const SEARCH_OK: Route = {
  method: "GET",
  path: "/catalogue/admin/search",
  status: 200,
  body: {
    items: [{ ...ACTIVE_ROW, score: 0.9 }],
    next_cursor: null,
    facets: [],
  },
};

function codedDefinition(key: string, label: string, displayOrder: number) {
  return {
    key,
    label,
    datatype: "code",
    cardinality: "0..*",
    scope: "both",
    required_for_submission: false,
    required_for_publication: false,
    binding_target: "local_code_system",
    value_set_uri: null,
    strength: "required",
    edition: null,
    local_code_system_key: key,
    filterable: true,
    origin: "system",
    status: "active",
    display_order: displayOrder,
    constraints: {},
    row_version: 1,
    form_control: { control: "concept_picker", params: {} },
  };
}

const VOLUME_DEFINITION = {
  ...codedDefinition("volume_ml", "Volume", 30),
  datatype: "number",
  cardinality: "0..1",
  binding_target: null,
  strength: null,
  local_code_system_key: null,
  form_control: { control: "number", params: {} },
};

function propertiesRoute(...definitions: unknown[]): Route {
  return {
    method: "GET",
    path: "/registry/properties",
    status: 200,
    body: { items: definitions },
  };
}

// Filterable, active, coded: gets a combobox. `volume_ml` is filterable but
// not coded, so it gets none and can only appear as a chip.
const PROPERTIES_OK = propertiesRoute(
  codedDefinition("discipline", "Discipline", 10),
  VOLUME_DEFINITION,
);

const PROPERTIES_WITH_SPECIMEN = propertiesRoute(
  codedDefinition("discipline", "Discipline", 10),
  codedDefinition("specimen", "Specimen", 20),
);

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

const SPECIMEN_VALUES_OK: Route = {
  method: "GET",
  path: "/registry/properties/specimen/values",
  status: 200,
  body: { items: [{ code: "serum", display: "Serum" }], total: 1 },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function calledPath(
  calls: { path: string; searchParams: URLSearchParams }[],
  suffix: string,
) {
  return calls.filter((call) => call.path.endsWith(suffix));
}

async function chooseOption(
  user: ReturnType<typeof userEvent.setup>,
  comboboxName: string,
  optionName: string,
) {
  await user.click(await screen.findByRole("combobox", { name: comboboxName }));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("AdminCatalogueListPage", () => {
  it("browses by default (no q) and lists every status", async () => {
    const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    expect(await screen.findByRole("link", { name: DRAFT_TERM })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: ACTIVE_TERM })).toBeInTheDocument();
    expect(calledPath(calls, "/catalogue/admin/entries")).not.toHaveLength(0);
    expect(calledPath(calls, "/catalogue/admin/search")).toHaveLength(0);
    // The public routes would hide drafts, so none may be read here.
    expect(calledPath(calls, "/catalogue/entries")).toHaveLength(0);
  });

  it("shows each status as its label, beside the identifier as plain text", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    const draftRow = (await screen.findByRole("link", { name: DRAFT_TERM })).closest(
      "tr",
    );
    const activeRow = screen.getByRole("link", { name: ACTIVE_TERM }).closest("tr");
    expect(within(draftRow as HTMLElement).getByText("Draft")).toBeInTheDocument();
    expect(within(draftRow as HTMLElement).getByText(DRAFT_KEY)).toBeInTheDocument();
    expect(within(activeRow as HTMLElement).getByText("Active")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: DRAFT_KEY })).not.toBeInTheDocument();
  });

  it("dispatches to the search route once q is set in the URL", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(`${LIST_URL}?q=glucose`, SIGNED_IN);

    await waitFor(() =>
      expect(calledPath(calls, "/catalogue/admin/search")).not.toHaveLength(0),
    );
    expect(calledPath(calls, "/catalogue/admin/entries")).toHaveLength(0);
    expect(calledPath(calls, "/catalogue/search")).toHaveLength(0);
  });

  it("finds a draft entry and follows it to its edit form", async () => {
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
    await user.click(await screen.findByRole("link", { name: DRAFT_TERM }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`/admin/catalogue/${DRAFT_KEY}/edit`),
    );
  });

  // The bulk reclassify UI is removed: a row opens its entry, nothing more.
  it("has no row checkboxes, select-all box or bulk toolbar", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_TERM });

    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("group", { name: "Bulk reclassify" })).toBeNull();
    expect(screen.queryByRole("button", { name: /reclassify/i })).toBeNull();
  });

  it("announces the number of results on the page", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    await renderRoute(LIST_URL, SIGNED_IN);

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("2 results on this page."),
    );
  });

  describe("filter comboboxes", () => {
    it("offers Status and each registry property that has coded values", async () => {
      stubApi([
        ENTRIES_OK,
        PROPERTIES_WITH_SPECIMEN,
        DISCIPLINE_VALUES_OK,
        SPECIMEN_VALUES_OK,
      ]);

      await renderRoute(LIST_URL, SIGNED_IN);

      expect(await screen.findByRole("combobox", { name: "Status" })).toBeVisible();
      expect(await screen.findByRole("combobox", { name: "Discipline" })).toBeVisible();
      expect(await screen.findByRole("combobox", { name: "Specimen" })).toBeVisible();
    });

    it("gives a filterable property with no coded values no control of its own", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("combobox", { name: "Discipline" });

      expect(screen.queryByRole("combobox", { name: "Volume" })).not.toBeInTheDocument();
      expect(
        screen.queryByRole("combobox", { name: "Specimen" }),
      ).not.toBeInTheDocument();
    });

    it("lists registry values with no counts, as admin browse has none", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_MULTI_OK]);
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(await screen.findByRole("combobox", { name: "Discipline" }));

      expect(
        (await screen.findAllByRole("option")).map((option) => option.textContent),
      ).toEqual(["Chemistry", "Haematology"]);
    });

    it("asks for the largest page of values the route allows", async () => {
      const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("combobox", { name: "Discipline" });

      await waitFor(() =>
        expect(
          calledPath(calls, "/registry/properties/discipline/values").every(
            (call) => call.searchParams.get("count") === "200",
          ),
        ).toBe(true),
      );
    });

    it("restores selections from a pasted link", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.status=draft`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(screen.getByRole("combobox", { name: "Status" })).toHaveAttribute(
        "placeholder",
        "1 selected",
      );
    });

    it("navigates with the chosen value and sends it to the API", async () => {
      const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      await chooseOption(user, "Status", "Draft");

      await waitFor(() =>
        expect(router.state.location.href).toContain("filter.status=draft"),
      );
      await waitFor(() =>
        expect(
          calledPath(calls, "/catalogue/admin/entries").some(
            (call) => call.searchParams.get("filter.status") === "draft",
          ),
        ).toBe(true),
      );
    });

    it("keeps the search box and results working when the options cannot be loaded", async () => {
      stubApi([
        ENTRIES_OK,
        PROPERTIES_OK,
        {
          method: "GET",
          path: "/registry/properties/discipline/values",
          status: 502,
          body: { detail: "upstream" },
        },
      ]);

      await renderRoute(LIST_URL, SIGNED_IN);

      expect(await screen.findByRole("link", { name: DRAFT_TERM })).toBeInTheDocument();
      expect(
        await screen.findByText(/Some filter options could not be loaded/),
      ).toBeInTheDocument();
      expect(screen.getByRole("searchbox")).toBeEnabled();
    });
  });

  // A `filter.*` with no combobox of its own (not offered here, or since
  // dropped from the registry) must still be visible and clearable from a
  // bookmarked or shared link.
  describe("active filters escape hatch", () => {
    it("shows a removable chip for a filter with no control, and clears it", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(`${LIST_URL}?filter.volume_ml=5`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      // The key resolves to its registry label; the value stays raw, since a
      // number has no value-options source to resolve against.
      const chip = screen.getByRole("button", { name: "Remove filter Volume: 5" });
      await user.click(chip);

      await waitFor(() =>
        expect(router.state.location.href).not.toContain("filter.volume_ml"),
      );
      expect(
        screen.queryByRole("button", { name: "Remove filter Volume: 5" }),
      ).not.toBeInTheDocument();
    });

    it("keeps a chip fully raw for a facet key absent from the registry entirely", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.mystery_facet=raw_value`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        screen.getByRole("button", { name: "Remove filter mystery_facet: raw_value" }),
      ).toBeInTheDocument();
    });

    it("resolves a coded property's chip to its registry label and display value", async () => {
      const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.discipline=chemistry`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: Chemistry",
        }),
      ).toBeInTheDocument();
      // The page answers the value, so no resolve-by-code request follows.
      expect(calledPath(calls, "/registry/properties/discipline/values")).toHaveLength(1);
    });

    it("falls back to the raw code for a coded value absent from the fetched page", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.discipline=retired_code`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: retired_code",
        }),
      ).toBeInTheDocument();
    });

    it("resolves a value beyond the fetched page by code, for its chip and its combobox", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK], {
        vary: (call) => {
          if (!call.path.endsWith("/registry/properties/discipline/values")) {
            return null;
          }
          if (call.searchParams.has("code")) {
            return {
              method: "GET",
              path: "/registry/properties/discipline/values",
              status: 200,
              body: {
                items: [{ code: "endocrinology", display: "Endocrinology" }],
                total: 1,
              },
            };
          }
          return DISCIPLINE_VALUES_OK;
        },
      });
      const user = userEvent.setup();

      await renderRoute(`${LIST_URL}?filter.discipline=endocrinology`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: Endocrinology",
        }),
      ).toBeInTheDocument();
      await user.click(await screen.findByRole("combobox", { name: "Discipline" }));
      expect(
        await screen.findByRole("option", { name: "Endocrinology" }),
      ).toHaveAttribute("aria-selected", "true");
    });

    // Past the route's own `code` ceiling the request would 422 and lose every
    // label in the facet, so the batch is cut to the ceiling instead.
    it("caps a facet's resolve-by-code batch at the route's 200-code ceiling", async () => {
      const codes = Array.from({ length: 201 }, (_, i) => `code_${i}`);
      const resolvedBatches: string[][] = [];
      stubApi([ENTRIES_OK, PROPERTIES_OK], {
        vary: (call) => {
          if (!call.path.endsWith("/registry/properties/discipline/values")) {
            return null;
          }
          if (call.searchParams.has("code")) {
            const batch = call.searchParams.getAll("code");
            resolvedBatches.push(batch);
            return {
              method: "GET",
              path: "/registry/properties/discipline/values",
              status: 200,
              body: {
                items: batch.map((code) => ({ code, display: code })),
                total: batch.length,
              },
            };
          }
          return DISCIPLINE_VALUES_OK;
        },
      });

      const query = codes.map((code) => `filter.discipline=${code}`).join("&");
      await renderRoute(`${LIST_URL}?${query}`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      await waitFor(() => expect(resolvedBatches.length).toBeGreaterThan(0));
      expect(resolvedBatches).toHaveLength(1);
      expect(resolvedBatches[0]).toHaveLength(200);
    });

    it("resolves the status facet's chip to its label", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?filter.status=active`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        screen.getByRole("button", { name: "Remove filter Status: Active" }),
      ).toBeInTheDocument();
    });

    it("resolves two selected values on the same coded facet from one fetch", async () => {
      const calls = stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_MULTI_OK]);

      await renderRoute(
        `${LIST_URL}?filter.discipline=chemistry&filter.discipline=haematology`,
        SIGNED_IN,
      );
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        await screen.findByRole("button", {
          name: "Remove filter Discipline: Chemistry",
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Remove filter Discipline: Haematology" }),
      ).toBeInTheDocument();
      // The combobox and both chips share one request per property.
      expect(calledPath(calls, "/registry/properties/discipline/values")).toHaveLength(1);
    });

    it("clears every active filter at once via Clear all filters", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(
        `${LIST_URL}?filter.status=draft&filter.volume_ml=5`,
        SIGNED_IN,
      );
      await screen.findByRole("link", { name: DRAFT_TERM });

      await user.click(screen.getByRole("button", { name: "Clear all filters" }));

      await waitFor(() => {
        expect(router.state.location.href).not.toContain("filter.status");
        expect(router.state.location.href).not.toContain("filter.volume_ml");
      });
      expect(
        screen.queryByRole("button", { name: "Clear all filters" }),
      ).not.toBeInTheDocument();
    });

    // A filter the server refuses leaves the screen on a refusal message, so
    // the chip and Clear all must stay reachable: they are the way out.
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

    it("has no Clear all filters control when nothing is selected", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(
        screen.queryByRole("button", { name: "Clear all filters" }),
      ).not.toBeInTheDocument();
    });
  });

  // Only the browse route is sortable: `GET /catalogue/admin/search` stays
  // relevance-ranked.
  describe("sort", () => {
    it("defaults to Identifier (business_key) with no sort in the URL", async () => {
      const sentSorts: (string | null)[] = [];
      stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
        vary: (call) => {
          if (!call.path.endsWith("/catalogue/admin/entries")) {
            return null;
          }
          sentSorts.push(call.searchParams.get("sort"));
          return ENTRIES_OK;
        },
      });

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(screen.getByRole("combobox", { name: "Sort by" })).toHaveValue(
        "business_key",
      );
      expect(sentSorts).not.toHaveLength(0);
      expect(sentSorts.every((sort) => sort === null)).toBe(true);
    });

    it("restores a sort selection from a pasted link", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?sort=updated_at`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(screen.getByRole("combobox", { name: "Sort by" })).toHaveValue("updated_at");
    });

    it("navigates with the chosen sort, sends it to the API, and announces it", async () => {
      const sentSorts: (string | null)[] = [];
      stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
        vary: (call) => {
          if (!call.path.endsWith("/catalogue/admin/entries")) {
            return null;
          }
          sentSorts.push(call.searchParams.get("sort"));
          return ENTRIES_OK;
        },
      });
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      await user.selectOptions(
        screen.getByRole("combobox", { name: "Sort by" }),
        "status",
      );

      await waitFor(() => expect(router.state.location.href).toContain("sort=status"));
      await waitFor(() => expect(sentSorts).toContain("status"));
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "Sorted by Status. 2 results on this page.",
        ),
      );
    });

    // The note describes a browsed, re-sorted page. A search submitted before
    // that page arrives is relevance-ranked, so it must not carry the note.
    it("does not attach the sort note to a search submitted before the re-sorted page arrives", async () => {
      stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      await act(async () => {
        fireEvent.change(screen.getByRole("combobox", { name: "Sort by" }), {
          target: { value: "status" },
        });
        await router.navigate({ to: "/admin/catalogue", search: { q: "glucose" } });
      });

      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("1 result on this page."),
      );
      expect(screen.getByRole("status")).not.toHaveTextContent("Sorted by");
    });

    it("drops the after cursor once sort is changed from a later page", async () => {
      stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);
      const user = userEvent.setup();

      const { router } = await renderRoute(`${LIST_URL}?after=${DRAFT_KEY}`, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      expect(router.state.location.href).toContain(`after=${DRAFT_KEY}`);

      await user.selectOptions(
        screen.getByRole("combobox", { name: "Sort by" }),
        "preferred_term",
      );

      await waitFor(() =>
        expect(router.state.location.href).toContain("sort=preferred_term"),
      );
      expect(router.state.location.href).not.toContain(`after=${DRAFT_KEY}`);
    });

    it("disables the sort control while searching, showing Relevance rather than a stale ordering", async () => {
      stubApi([ENTRIES_OK, SEARCH_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

      await renderRoute(`${LIST_URL}?q=glucose&sort=updated_at`, SIGNED_IN);
      await screen.findByRole("link", { name: ACTIVE_TERM });

      const control = screen.getByRole("combobox", { name: "Sort by" });
      expect(control).toBeDisabled();
      expect(control).toHaveValue("relevance");
      expect(screen.getByRole("option", { name: "Relevance" })).toBeInTheDocument();
    });
  });

  describe("paging", () => {
    const PAGE_1 = { items: [DRAFT_ROW], next_cursor: DRAFT_KEY };
    const PAGE_2 = { items: [ACTIVE_ROW], next_cursor: null };

    // Keyed on `after` itself, not a call count: both pages hit the same path,
    // and `<StrictMode>` fetches the first page twice, so a counter would
    // reach "page two" before the test clicks Next.
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

    it("pushes the cursor into the URL and shows the next page's entries", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      await user.click(screen.getByRole("button", { name: "Next page" }));

      await screen.findByRole("link", { name: ACTIVE_TERM });
      expect(router.state.location.href).toContain(`after=${DRAFT_KEY}`);
      expect(screen.queryByRole("link", { name: DRAFT_TERM })).not.toBeInTheDocument();
    });

    it("says there are no more results on the last page, and disables Previous on the first", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });

      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
      expect(screen.queryByText("No more results")).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });

      expect(screen.getByText("No more results")).toBeVisible();
      expect(screen.getByRole("button", { name: "Next page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });

    it("returns to the first page from Previous, dropping the cursor from the URL", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });

      await user.click(screen.getByRole("button", { name: "Previous page" }));

      await screen.findByRole("link", { name: DRAFT_TERM });
      expect(router.state.location.href).not.toContain("after=");
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });

    it("walks back through every page visited, in reverse order", async () => {
      const THIRD_TERM = "Urea";
      stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
        vary: (call) => {
          if (call.method === "GET" && call.path.endsWith("/catalogue/admin/entries")) {
            const after = call.searchParams.get("after");
            const pages: Record<string, unknown> = {
              "": { ...PAGE_1, next_cursor: "cursor-1" },
              "cursor-1": { ...PAGE_2, next_cursor: "cursor-2" },
              "cursor-2": {
                items: [
                  entrySummary({
                    business_key: "NPTC-000500",
                    preferred_term: THIRD_TERM,
                  }),
                ],
                next_cursor: null,
              },
            };
            return {
              method: "GET",
              path: call.path,
              status: 200,
              body: pages[after ?? ""],
            };
          }
          return null;
        },
      });
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: THIRD_TERM });

      await user.click(screen.getByRole("button", { name: "Previous page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });
      expect(router.state.location.href).toContain("after=cursor-1");

      await user.click(screen.getByRole("button", { name: "Previous page" }));
      await screen.findByRole("link", { name: DRAFT_TERM });
      expect(router.state.location.href).not.toContain("after=");
    });

    it("empties the Previous history and drops the cursor once a filter is chosen from a later page", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "false",
      );

      await chooseOption(user, "Status", "Active");
      await waitFor(() =>
        expect(router.state.location.href).toContain("filter.status=active"),
      );
      // The popup stays open after a pick and makes the page behind it inert.
      await user.keyboard("{Escape}");

      await screen.findByRole("link", { name: DRAFT_TERM });
      expect(router.state.location.href).not.toContain("after=");
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });

    it("disables Previous on a page opened straight from a link", async () => {
      stubTwoPages();

      await renderRoute(`${LIST_URL}?after=${DRAFT_KEY}`, SIGNED_IN);
      await screen.findByRole("link", { name: ACTIVE_TERM });

      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });

    it("keeps Previous when a later page comes back empty", async () => {
      stubApi([PROPERTIES_OK, DISCIPLINE_VALUES_OK], {
        vary: (call) => {
          if (call.method === "GET" && call.path.endsWith("/catalogue/admin/entries")) {
            const body =
              call.searchParams.get("after") === null
                ? PAGE_1
                : { items: [], next_cursor: null };
            return { method: "GET", path: call.path, status: 200, body };
          }
          return null;
        },
      });
      const user = userEvent.setup();

      await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));

      expect(await screen.findByText("No more catalogue entries.")).toBeVisible();
      await user.click(screen.getByRole("button", { name: "Previous page" }));
      expect(await screen.findByRole("link", { name: DRAFT_TERM })).toBeVisible();
    });

    it("has no automated accessibility violations with the paging controls shown", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { container } = await renderRoute(LIST_URL, SIGNED_IN);
      await screen.findByRole("link", { name: DRAFT_TERM });
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: ACTIVE_TERM });

      await expectNoA11yViolations(container);
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

    // `waitFor`, not `findByRole`: the live region exists (empty) from first
    // render, and `useAnnounce` fills it a tick later.
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("boom"));
  });

  it("shows a stale-data warning, not a blank screen, when a refresh fails over data already shown", async () => {
    // A flag the test flips, not a call count: `<StrictMode>` fetches the
    // first page twice.
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
    await screen.findByRole("link", { name: DRAFT_TERM });

    shouldFail = true;
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: ["api", "/api/v1/catalogue/admin/entries"],
      });
    });

    // Two elements carry this text on purpose: the visible warning and the
    // live region announcing it.
    expect(
      await screen.findAllByText(
        "Catalogue entries could not be refreshed just now, so what follows may be out of date.",
      ),
    ).toHaveLength(2);
    expect(screen.getByRole("link", { name: DRAFT_TERM })).toBeInTheDocument();
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

    expect(await screen.findByText("The catalogue has no entries yet.")).toBeVisible();
  });

  it("offers Clear all filters from the empty state when filters hide every entry", async () => {
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

    await renderRoute(`${LIST_URL}?filter.status=withdrawn`, SIGNED_IN);

    expect(
      await screen.findByText("No catalogue entries match these filters."),
    ).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Clear all filters" })).not.toHaveLength(
      0,
    );
  });

  it("has no automated accessibility violations", async () => {
    stubApi([ENTRIES_OK, PROPERTIES_OK, DISCIPLINE_VALUES_OK]);

    const { container } = await renderRoute(LIST_URL, SIGNED_IN);
    await screen.findByRole("link", { name: DRAFT_TERM });

    await expectNoA11yViolations(container);
  });
});
