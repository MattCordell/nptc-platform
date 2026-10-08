import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";

/**
 * The public catalogue browse and search screen (FR-14..18, NFR-31), driven
 * through the real route and signed out, as an anonymous visitor uses it.
 *
 * Requests are told apart by their query string, never by a call count:
 * `<StrictMode>` fetches the first page twice.
 */

const BOUND_KEY = "NPTC-000247";
const UNBOUND_KEY = "NPTC-000901";
// Eighteen digits: past `Number.MAX_SAFE_INTEGER`, so a coerced code would
// render differently (FR-06).
const LONG_CODE = "999480561000168100";

function entrySummary(overrides: Record<string, unknown>) {
  return {
    business_key: "NPTC-000000",
    preferred_term: "Placeholder",
    length: 11,
    status: "active",
    updated_at: "2026-09-01T04:30:00Z",
    has_open_finding: false,
    code: null,
    fsn: null,
    disciplines: [],
    specimens: [],
    label_provenance: {
      preferred_term: {
        designation: "au_preferred_term",
        semantic_tag: "not_applicable",
      },
      fsn: { designation: "fsn", semantic_tag: "stripped" },
      specimens: {
        designation: "au_preferred_term",
        semantic_tag: "not_applicable",
      },
    },
    ...overrides,
  };
}

const BOUND_ROW = entrySummary({
  business_key: BOUND_KEY,
  preferred_term: "Ferritin",
  code: LONG_CODE,
  fsn: "Ferritin measurement",
  disciplines: ["Chemical pathology", "Haematology"],
  specimens: ["Serum", "Plasma"],
  has_open_finding: true,
});
const UNBOUND_ROW = entrySummary({
  business_key: UNBOUND_KEY,
  preferred_term: "Full blood count",
});

const ENTRIES_OK: Route = {
  method: "GET",
  path: "/catalogue/entries",
  status: 200,
  body: { items: [BOUND_ROW, UNBOUND_ROW], next_cursor: null },
};

const DISCIPLINE_FACET = {
  key: "discipline",
  label: "Discipline",
  facetable: true,
  truncated: false,
  buckets: [
    { value: "chem", label: "Chemical pathology", count: 2 },
    { value: "haem", label: "Haematology", count: 1 },
  ],
};

function searchPage(overrides: Record<string, unknown> = {}) {
  return {
    items: [{ ...BOUND_ROW, score: 0.9 }],
    next_cursor: null,
    facets: [
      {
        key: "status",
        label: "Status",
        facetable: true,
        truncated: false,
        buckets: [{ value: "active", label: "active", count: 1 }],
      },
      DISCIPLINE_FACET,
    ],
    ...overrides,
  };
}

const SEARCH_OK: Route = {
  method: "GET",
  path: "/catalogue/search",
  status: 200,
  body: searchPage(),
};

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Wraps the stubbed `fetch` so a test can hold back the requests it picks
 * until `release()`, and look at the screen while they load. Install it
 * before rendering: the API client keeps the `fetch` it was created with.
 */
function holdableFetch() {
  const inner = globalThis.fetch;
  let matches: (url: URL) => boolean = () => false;
  let release: () => void = () => {};
  let gate = Promise.resolve();
  vi.stubGlobal("fetch", async (request: Request) => {
    if (matches(new URL(request.url))) {
      await gate;
    }
    return inner(request);
  });
  return {
    hold(picks: (url: URL) => boolean) {
      matches = picks;
      gate = new Promise<void>((resolve) => {
        release = resolve;
      });
    },
    release: () => release(),
  };
}

/** The route's validated search, where a filter is always an array; the raw
 * `location.search` holds a lone value as a bare string. */
function validatedSearch(router: { state: { matches: { search: unknown }[] } }): unknown {
  return router.state.matches.at(-1)?.search;
}

function calledPath(
  calls: ReturnType<typeof stubApi>,
  suffix: string,
): ReturnType<typeof stubApi> {
  return calls.filter((call) => call.path.endsWith(suffix));
}

describe("CatalogueSearchPage", () => {
  it("browses published entries with no query, and offers no facet controls", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK]);

    await renderRoute("/catalogue");

    expect(await screen.findByRole("link", { name: "Ferritin" })).toBeInTheDocument();
    expect(calledPath(calls, "/catalogue/entries").length).toBeGreaterThan(0);
    expect(calledPath(calls, "/catalogue/search")).toHaveLength(0);
    expect(screen.queryByRole("group", { name: "Discipline" })).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Search to filter the results by discipline and other properties.",
      ),
    ).toBeInTheDocument();
  });

  it("has one h1 and links each row to its entry by business key", async () => {
    stubApi([ENTRIES_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/catalogue");
    await screen.findByRole("link", { name: "Ferritin" });

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await user.click(screen.getByRole("link", { name: "Ferritin" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`/catalogue/${BOUND_KEY}`),
    );
  });

  it("lets the table scroll inside its own container on a narrow screen", async () => {
    stubApi([ENTRIES_OK]);

    await renderRoute("/catalogue");
    await screen.findByRole("link", { name: "Ferritin" });

    const scroller = screen.getByTestId("results-scroll");
    expect(scroller).toHaveClass("overflow-x-auto");
    expect(within(scroller).getByRole("table")).toBeInTheDocument();
  });

  it("shows the term, discipline, specimen and FSN, in that order", async () => {
    stubApi([ENTRIES_OK]);

    await renderRoute("/catalogue");
    await screen.findByRole("link", { name: "Ferritin" });

    expect(
      screen.getAllByRole("columnheader").map((header) => header.textContent),
    ).toEqual(["Requesting term", "Discipline", "Specimen", "SNOMED CT FSN"]);
  });

  it("shows the disciplines, specimens and FSN, and the finding beside the term", async () => {
    stubApi([ENTRIES_OK]);

    await renderRoute("/catalogue");

    const bound = (await screen.findByRole("link", { name: "Ferritin" })).closest("tr");
    const boundRow = within(bound as HTMLElement);
    expect(boundRow.getByText("Chemical pathology, Haematology")).toBeInTheDocument();
    expect(boundRow.getByText("Serum, Plasma")).toBeInTheDocument();
    expect(boundRow.getByText("Ferritin measurement")).toBeInTheDocument();
    const finding = boundRow.getByText("Open finding");
    expectTokenClassesOnly(finding.className);
    expect(finding.closest("th, td")).toBe(boundRow.getByRole("link").closest("th, td"));
  });

  it("shows no SNOMED CT code on a row", async () => {
    stubApi([ENTRIES_OK]);

    await renderRoute("/catalogue");
    await screen.findByRole("link", { name: "Ferritin" });

    expect(screen.queryByText(LONG_CODE)).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /code/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Validation" })).toBeNull();
  });

  it("shows placeholders, and no finding badge, on a row with nothing recorded", async () => {
    stubApi([ENTRIES_OK]);

    await renderRoute("/catalogue");

    const unbound = within(
      (await screen.findByRole("link", { name: "Full blood count" })).closest(
        "tr",
      ) as HTMLElement,
    );
    expect(unbound.getByText("No code")).toBeInTheDocument();
    expect(unbound.getAllByText("None recorded")).toHaveLength(2);
    expect(unbound.queryByText("Open finding")).toBeNull();
    expect(unbound.queryByText("None")).toBeNull();
  });

  it("searches once a query is submitted, dropping any cursor", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/catalogue?after=NPTC-000100");
    await screen.findByRole("link", { name: "Ferritin" });

    await user.type(
      screen.getByLabelText("Search term or SNOMED CT code"),
      "  ferritin ",
    );
    await user.keyboard("{Enter}");

    await waitFor(() => expect(validatedSearch(router)).toEqual({ q: "ferritin" }));
    await waitFor(() =>
      expect(
        calledPath(calls, "/catalogue/search").some(
          (call) => call.searchParams.get("q") === "ferritin",
        ),
      ).toBe(true),
    );
  });

  it("shows facets with counts while searching, leaving out the single-bucket status facet", async () => {
    stubApi([ENTRIES_OK, SEARCH_OK]);

    await renderRoute("/catalogue?q=ferritin");

    const group = await screen.findByRole("group", { name: "Discipline" });
    expect(
      within(group).getByRole("button", { name: "Chemical pathology (2)" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Status" })).not.toBeInTheDocument();
  });

  it("restores filters from a pasted URL and sends them to the API", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK]);

    await renderRoute(
      "/catalogue?q=ferritin&filter.discipline=chem&filter.discipline=haem",
    );

    const group = await screen.findByRole("group", { name: "Discipline" });
    expect(
      within(group).getByRole("button", { name: /Chemical pathology/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: "Remove filter Discipline: Haematology" }),
    ).toBeInTheDocument();
    const sent = calledPath(calls, "/catalogue/search").at(-1);
    expect(sent?.searchParams.getAll("filter.discipline")).toEqual(["chem", "haem"]);
  });

  it("drops the cursor when a filter is toggled", async () => {
    const calls = stubApi([ENTRIES_OK, SEARCH_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute(
      "/catalogue?q=ferritin&after=0.5%3Aabc%3ANPTC-000100",
    );
    const group = await screen.findByRole("group", { name: "Discipline" });

    await user.click(within(group).getByRole("button", { name: "Haematology (1)" }));

    await waitFor(() =>
      expect(validatedSearch(router)).toEqual({
        q: "ferritin",
        "filter.discipline": ["haem"],
      }),
    );
    // The new filter set must reach the server, not only the URL.
    await waitFor(() =>
      expect(
        calledPath(calls, "/catalogue/search").some(
          (call) => call.searchParams.get("filter.discipline") === "haem",
        ),
      ).toBe(true),
    );
  });

  it("applies a URL filter while browsing and shows it as a raw, removable chip", async () => {
    const calls = stubApi([ENTRIES_OK]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/catalogue?filter.discipline=chem");
    await screen.findByRole("link", { name: "Ferritin" });

    expect(
      calledPath(calls, "/catalogue/entries")
        .at(-1)
        ?.searchParams.get("filter.discipline"),
    ).toBe("chem");
    await user.click(
      screen.getByRole("button", { name: "Remove filter discipline: chem" }),
    );

    await waitFor(() => expect(validatedSearch(router)).toEqual({ q: "" }));
  });

  it("offers a many-valued facet as a dropdown that adds a filter", async () => {
    const buckets = Array.from({ length: 10 }, (_, index) => ({
      value: `v${index}`,
      label: `Value ${index}`,
      count: 1,
    }));
    stubApi([
      {
        ...SEARCH_OK,
        body: searchPage({
          facets: [{ ...DISCIPLINE_FACET, truncated: true, buckets }],
        }),
      },
    ]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/catalogue?q=ferritin");
    const select = await screen.findByLabelText("Discipline");

    expect(
      screen.getByText(
        "Discipline shows only its most common values. Narrow the search to see others.",
      ),
    ).toBeInTheDocument();
    await user.selectOptions(select, "v3");

    await waitFor(() =>
      expect(validatedSearch(router)).toEqual({
        q: "ferritin",
        "filter.discipline": ["v3"],
      }),
    );
  });

  it("shows an empty state with a way to clear the filters, and announces no results", async () => {
    stubApi([{ ...SEARCH_OK, body: searchPage({ items: [] }) }]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/catalogue?q=zzz&filter.discipline=chem");

    expect(
      await screen.findByText('No catalogue entries match "zzz" with these filters.'),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("No results."),
    );
    const table = screen.getByRole("table");
    await user.click(within(table).getByRole("button", { name: "Clear all filters" }));

    await waitFor(() => expect(validatedSearch(router)).toEqual({ q: "zzz" }));
  });

  it("announces how many results are on the page, and whether more follow", async () => {
    stubApi([
      { ...ENTRIES_OK, body: { items: [BOUND_ROW, UNBOUND_ROW], next_cursor: "x" } },
    ]);

    await renderRoute("/catalogue");

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "2 results on this page. More results are on the next page.",
      ),
    );
  });

  it("shows and announces a failed search", async () => {
    stubApi([
      {
        method: "GET",
        path: "/catalogue/search",
        status: 422,
        body: { detail: "Filter is not available: volume_ml" },
      },
    ]);

    await renderRoute("/catalogue?q=ferritin&filter.volume_ml=5");

    expect(
      await screen.findByText("Filter is not available: volume_ml"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Filter is not available: volume_ml",
      ),
    );
    // The way out of a refused filter stays on screen.
    expect(
      screen.getByRole("button", { name: "Remove filter volume_ml: 5" }),
    ).toBeInTheDocument();
  });

  it("falls back to a plain message when the failure carries no detail", async () => {
    stubApi([{ method: "GET", path: "/catalogue/entries", status: 503, body: {} }]);

    await renderRoute("/catalogue");

    expect(
      await screen.findByText(
        "The catalogue could not be loaded. Try again in a moment, or change the search.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the rows and warns when a refresh fails over data already shown", async () => {
    // A flag, not a call count: `<StrictMode>` fetches the first page twice.
    let shouldFail = false;
    stubApi([], {
      vary: (call) =>
        call.path.endsWith("/catalogue/entries")
          ? shouldFail
            ? { method: "GET", path: call.path, status: 500, body: {} }
            : ENTRIES_OK
          : null,
    });

    const { queryClient } = await renderRoute("/catalogue");
    await screen.findByRole("link", { name: "Ferritin" });

    shouldFail = true;
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: ["api", "/api/v1/catalogue/entries"],
      });
    });

    // Shown and announced: the paragraph and the live region.
    expect(
      await screen.findAllByText(
        "The catalogue could not be refreshed just now, so these results may be out of date.",
      ),
    ).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Ferritin" })).toBeInTheDocument();
  });

  describe("results from a different search are never shown as current", () => {
    const ACID_ROW = entrySummary({
      business_key: "NPTC-000310",
      preferred_term: "Acid phosphatase",
    });
    const IRON_ROW = entrySummary({
      business_key: "NPTC-000320",
      preferred_term: "Iron studies",
    });

    function stubSearches() {
      return stubApi([ENTRIES_OK], {
        vary: (call) => {
          if (!call.path.endsWith("/catalogue/search")) {
            return null;
          }
          const row = call.searchParams.get("q") === "iron" ? IRON_ROW : ACID_ROW;
          return {
            method: "GET",
            path: call.path,
            status: 200,
            body: searchPage({ items: [{ ...row, score: 0.9 }] }),
          };
        },
      });
    }

    async function submitQuery(user: ReturnType<typeof userEvent.setup>, q: string) {
      const box = screen.getByLabelText("Search term or SNOMED CT code");
      await user.clear(box);
      if (q !== "") {
        await user.type(box, q);
      }
      await user.keyboard("{Enter}");
    }

    function expectNothingFromAcid() {
      expect(
        screen.queryByRole("link", { name: "Acid phosphatase" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Discipline" })).not.toBeInTheDocument();
      expect(screen.getByText("Loading catalogue entries…")).toBeInTheDocument();
    }

    it("after a return to browse mode", async () => {
      stubSearches();
      const held = holdableFetch();
      const user = userEvent.setup();

      await renderRoute("/catalogue?q=acid");
      await screen.findByRole("link", { name: "Acid phosphatase" });
      await submitQuery(user, "");
      await screen.findByRole("link", { name: "Full blood count" });
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("2 results on this page."),
      );

      held.hold((url) => url.searchParams.get("q") === "iron");
      await submitQuery(user, "iron");

      await waitFor(expectNothingFromAcid);
      // Still the browse announcement: the acid page was not re-announced.
      expect(screen.getByRole("status")).toHaveTextContent("2 results on this page.");
      held.release();
      await screen.findByRole("link", { name: "Iron studies" });
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("1 result on this page."),
      );
    });

    it("when a new query replaces the current one", async () => {
      stubSearches();
      const held = holdableFetch();
      const user = userEvent.setup();

      await renderRoute("/catalogue?q=acid");
      await screen.findByRole("link", { name: "Acid phosphatase" });

      held.hold((url) => url.searchParams.get("q") === "iron");
      await submitQuery(user, "iron");

      await waitFor(expectNothingFromAcid);
      held.release();
      await screen.findByRole("link", { name: "Iron studies" });
    });

    // The same query with a new filter is the one case worth keeping the
    // previous page for: its facets are still the right ones, and the pill
    // the user just pressed must stay mounted to keep focus.
    it("but keeps the facets of the same query while a filter change loads", async () => {
      stubSearches();
      const held = holdableFetch();
      const user = userEvent.setup();

      await renderRoute("/catalogue?q=acid");
      const group = await screen.findByRole("group", { name: "Discipline" });

      held.hold((url) => url.searchParams.has("filter.discipline"));
      const pill = within(group).getByRole("button", { name: "Haematology (1)" });
      await user.click(pill);

      await waitFor(() => expect(pill).toHaveAttribute("aria-pressed", "true"));
      expect(pill).toHaveFocus();
      held.release();
    });
  });

  describe("empty state names the actual reason", () => {
    const EMPTY_PAGE = { items: [], next_cursor: null };

    it.each([
      ["/catalogue?after=NPTC-999999", "No more catalogue entries."],
      ["/catalogue", "The catalogue has no published entries yet."],
      ["/catalogue?filter.discipline=chem", "No catalogue entries match these filters."],
    ])("while browsing %s", async (url, text) => {
      stubApi([{ ...ENTRIES_OK, body: EMPTY_PAGE }]);

      await renderRoute(url);

      expect(await screen.findByText(text)).toBeInTheDocument();
    });

    it.each([
      ["/catalogue?q=zzz", 'No catalogue entries match "zzz".'],
      [
        "/catalogue?q=zzz&filter.discipline=chem",
        'No catalogue entries match "zzz" with these filters.',
      ],
    ])("while searching %s", async (url, text) => {
      stubApi([{ ...SEARCH_OK, body: searchPage({ items: [] }) }]);

      await renderRoute(url);

      expect(await screen.findByText(text)).toBeInTheDocument();
    });
  });

  describe("paging", () => {
    const PAGE_1 = { items: [BOUND_ROW], next_cursor: BOUND_KEY };
    const PAGE_2 = { items: [UNBOUND_ROW], next_cursor: null };

    function stubTwoPages() {
      return stubApi([], {
        vary: (call) => {
          if (call.path.endsWith("/catalogue/entries")) {
            const body = call.searchParams.get("after") === null ? PAGE_1 : PAGE_2;
            return { method: "GET", path: call.path, status: 200, body };
          }
          return null;
        },
      });
    }

    it("pages forward and back with the cursor, and says when the end is reached", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute("/catalogue");
      await screen.findByRole("link", { name: "Ferritin" });
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );

      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByRole("link", { name: "Full blood count" });
      expect(validatedSearch(router)).toEqual({ q: "", after: BOUND_KEY });
      expect(screen.getByText("No more results")).toBeVisible();

      await user.click(screen.getByRole("button", { name: "Previous page" }));
      await screen.findByRole("link", { name: "Ferritin" });
      expect(validatedSearch(router)).toEqual({ q: "" });
    });

    // The controls must stay mounted while the next page loads, or focus
    // falls to the document body and a keyboard user starts again at the top.
    it("keeps focus on the paging control across a page change", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      await renderRoute("/catalogue");
      await screen.findByRole("link", { name: "Ferritin" });

      screen.getByRole("button", { name: "Next page" }).focus();
      await user.keyboard("{Enter}");
      await screen.findByRole("link", { name: "Full blood count" });

      expect(screen.getByRole("button", { name: "Next page" })).toHaveFocus();
    });

    // While page 2 loads, the placeholder is page 1, whose `next_cursor` is
    // the `after` already in the URL. A second press must not push it again.
    it("ignores Next while the next page is still loading", async () => {
      stubTwoPages();
      const held = holdableFetch();
      const user = userEvent.setup();

      const { router } = await renderRoute("/catalogue");
      await screen.findByRole("link", { name: "Ferritin" });

      held.hold((url) => url.searchParams.get("after") === BOUND_KEY);
      const next = screen.getByRole("button", { name: "Next page" });
      await user.click(next);
      await screen.findByText("Loading catalogue entries…");
      await user.click(next);
      held.release();
      await screen.findByRole("link", { name: "Full blood count" });

      await user.click(screen.getByRole("button", { name: "Previous page" }));

      await screen.findByRole("link", { name: "Ferritin" });
      expect(validatedSearch(router)).toEqual({ q: "" });
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });

    it("is operable by keyboard alone", async () => {
      stubTwoPages();
      const user = userEvent.setup();

      const { router } = await renderRoute("/catalogue");
      await screen.findByRole("link", { name: "Ferritin" });

      screen.getByRole("button", { name: "Next page" }).focus();
      await user.keyboard("{Enter}");
      await screen.findByRole("link", { name: "Full blood count" });

      screen.getByLabelText("Search term or SNOMED CT code").focus();
      await user.keyboard("ferritin{Enter}");
      await waitFor(() => expect(validatedSearch(router)).toEqual({ q: "ferritin" }));
    });
  });

  it("has no automated accessibility violations while browsing", async () => {
    stubApi([ENTRIES_OK]);

    const { container } = await renderRoute("/catalogue?filter.discipline=chem");
    await screen.findByRole("link", { name: "Ferritin" });

    await expectNoA11yViolations(container);
  });

  it("has no automated accessibility violations while searching with facets", async () => {
    stubApi([SEARCH_OK]);

    const { container } = await renderRoute(
      "/catalogue?q=ferritin&filter.discipline=chem",
    );
    await screen.findByRole("group", { name: "Discipline" });

    await expectNoA11yViolations(container);
  });
});
