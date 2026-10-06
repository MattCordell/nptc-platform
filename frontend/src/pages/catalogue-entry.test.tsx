import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The public entry detail screen (FR-17, FR-18, FR-20, NFR-31), driven through
 * the real route and signed out, as an anonymous visitor uses it.
 */

const KEY = "NPTC-000247";
// Eighteen digits: past `Number.MAX_SAFE_INTEGER`, so a coerced code would
// render differently (FR-06).
const LONG_CODE = "999480561000168100";
const OLD_CODE = "000123";
const FSN = "Ferritin measurement (procedure)";

const PROVENANCE = {
  preferred_term: { designation: "au_preferred_term", semantic_tag: "not_applicable" },
};

function entry(overrides: Record<string, unknown> = {}) {
  return {
    business_key: KEY,
    preferred_term: "Ferritin",
    length: 8,
    status: "active",
    specimen_unconstrained: false,
    updated_at: "2026-09-01T12:00:00Z",
    has_open_finding: false,
    code: LONG_CODE,
    disciplines: ["Chemical pathology", "Haematology"],
    label_provenance: PROVENANCE,
    row_version: 7,
    designations: [
      {
        term: "Serum ferritin",
        use: "synonym",
        language: "en-AU",
        status: "active",
        length: 14,
        label_provenance: { designation: "synonym", semantic_tag: "not_applicable" },
      },
      {
        term: "Ferritine",
        use: "preferred",
        language: "fr",
        status: "active",
        length: 9,
        label_provenance: {
          designation: "preferred_variant",
          semantic_tag: "not_applicable",
        },
      },
    ],
    bindings: [
      binding({
        code: OLD_CODE,
        status: "retired",
        retirement_reason: "Superseded by a more specific concept",
        replaced_by_code: LONG_CODE,
      }),
      binding({ code: LONG_CODE, status: "active" }),
      binding({
        code: OLD_CODE,
        status: "retired",
        retirement_reason: "Bound in error",
        replaced_by_code: null,
      }),
    ],
    properties: [
      property({
        key: "discipline",
        label: "Discipline",
        ordinal: 1,
        value: coded("haematology", "Haematology", LOCAL_SYSTEM),
      }),
      property({
        key: "discipline",
        label: "Discipline",
        ordinal: 0,
        value: coded("chemical_pathology", "Chemical pathology", LOCAL_SYSTEM),
      }),
      property({
        key: "specimen",
        label: "Specimen",
        value: coded(LONG_CODE, "Urine", "http://snomed.info/sct"),
      }),
      property({
        key: "usage_guidance",
        label: "Usage guidance",
        value: "Fasting is not required.",
        justification: "Agreed with the working group.",
      }),
      property({
        key: "legacy_flag",
        label: "Legacy flag",
        status: "deprecated",
        value: "kept",
      }),
    ],
    ...overrides,
  };
}

const LOCAL_SYSTEM = "https://nptc.example.org/CodeSystem/discipline";

/** The shape a coded property value has on the wire. */
function coded(code: string, display: string | null, system: string) {
  return { code, system, display };
}

function binding(overrides: Record<string, unknown>) {
  return {
    system: "http://snomed.info/sct",
    code: LONG_CODE,
    fsn: FSN,
    au_preferred_term: "Ferritin level",
    edition_hint: "au",
    status: "active",
    retirement_reason: null,
    replaced_by_code: null,
    label_provenance: {},
    ...overrides,
  };
}

function property(overrides: Record<string, unknown>) {
  return {
    key: "k",
    label: "K",
    datatype: "string",
    cardinality: "single",
    status: "active",
    ordinal: 0,
    value: "v",
    justification: null,
    ...overrides,
  };
}

function historyEvent(overrides: Record<string, unknown> = {}) {
  return {
    occurred_at: "2026-09-01T12:00:00Z",
    action: "catalogue_entry.updated",
    changed_by: null,
    changed_fields: ["entry_id", "preferred_term", "row_version", "updated_at"],
    note: "Corrected the spelling.",
    release: null,
    ...overrides,
  };
}

const ENTRY_OK: Route = {
  method: "GET",
  path: `/catalogue/entries/${KEY}`,
  status: 200,
  body: entry(),
};

const HISTORY_OK: Route = {
  method: "GET",
  path: `/catalogue/entries/${KEY}/history`,
  status: 200,
  body: { items: [historyEvent()], next_cursor: null },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Wraps the stubbed `fetch` so a test can hold back the history request until
 * `release()`. Install it before rendering: the API client keeps the `fetch` it
 * was created with.
 */
function holdHistory() {
  const inner = globalThis.fetch;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal("fetch", async (request: Request) => {
    if (new URL(request.url).pathname.endsWith("/history")) {
      await gate;
    }
    return inner(request);
  });
  return { release: () => release() };
}

async function renderEntry(routes: Route[] = [ENTRY_OK, HISTORY_OK]) {
  const calls = stubApi(routes);
  const view = await renderRoute(`/catalogue/${KEY}`);
  return { calls, ...view };
}

describe("the entry heading and header", () => {
  it("shows the preferred term as the page's one h1", async () => {
    await renderEntry();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("sets the document title from the entry", async () => {
    await renderEntry();

    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    await waitFor(() => expect(document.title).toBe("Ferritin — NPTC Catalogue"));
  });

  it("requests the entry by its business key", async () => {
    const { calls } = await renderEntry();

    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    expect(
      calls.some(
        (call) => call.method === "GET" && call.path.endsWith(`/entries/${KEY}`),
      ),
    ).toBe(true);
  });

  it("shows the code as a string in mono, never altered (FR-06)", async () => {
    await renderEntry();
    const heading = await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const chip = within(heading.closest("section") as HTMLElement).getAllByText(
      LONG_CODE,
      { selector: "code" },
    )[0];
    expect(chip.textContent).toBe(LONG_CODE);
    expect(chip.className).toContain("font-mono");
  });

  it("shows the status in words, not by colour alone", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(screen.getAllByText("Active").length).toBeGreaterThan(0);
  });

  it("says so when the entry has no code", async () => {
    await renderEntry([
      { ...ENTRY_OK, body: entry({ code: null, bindings: [] }) },
      HISTORY_OK,
    ]);

    expect(await screen.findByText("No SNOMED CT code")).toBeInTheDocument();
    expect(screen.getByText("This entry has no SNOMED CT code.")).toBeInTheDocument();
  });

  it("links the breadcrumb back to the home page and the catalogue", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const trail = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(trail).getByRole("link", { name: "Home" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(within(trail).getByRole("link", { name: "Catalogue" })).toHaveAttribute(
      "href",
      "/catalogue",
    );
    expect(within(trail).getByText("Ferritin")).toHaveAttribute("aria-current", "page");
  });

  it("shows the heading and a loading note while the entry loads", async () => {
    const calls = stubApi([ENTRY_OK, HISTORY_OK]);
    const inner = globalThis.fetch;
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", async (request: Request) => {
      await gate;
      return inner(request);
    });
    await renderRoute(`/catalogue/${KEY}`);

    expect(screen.getByRole("heading", { level: 1, name: KEY })).toBeInTheDocument();
    expect(screen.getByText("Loading entry…")).toBeInTheDocument();

    await act(async () => release());
    expect(
      await screen.findByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(calls.length).toBeGreaterThan(0);
  });
});

describe("the open-finding indicator (FR-18)", () => {
  it("appears, with no detail, when a finding is open", async () => {
    await renderEntry([
      { ...ENTRY_OK, body: entry({ has_open_finding: true }) },
      HISTORY_OK,
    ]);

    const indicator = await screen.findByText(/Open finding/);
    expect(indicator.textContent).toBe("!Open finding");
    expect(screen.queryByText(/finding detail|rule|severity/i)).toBeNull();
  });

  it("is absent when no finding is open", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(screen.queryByText(/Open finding/)).toBeNull();
  });
});

describe("terms", () => {
  it("lists synonyms and other-language terms with their type and language", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const table = screen.getByRole("table", {
      name: "Synonyms and other-language terms",
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      "Serum ferritinSynonymen-AUActive",
      "FerritinePreferred term in another languagefrActive",
    ]);
  });
});

describe("SNOMED CT codes", () => {
  async function bindingRows() {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    const table = screen.getByRole("table", { name: "Code bindings" });
    return within(table).getAllByRole("row").slice(1);
  }

  it("lists the active binding before the retired ones", async () => {
    const rows = await bindingRows();

    expect(within(rows[0]).getByText("Active")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Retired")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Retired")).toBeInTheDocument();
  });

  it("shows two retired rows that share a code, without a key clash", async () => {
    const errors = vi.spyOn(console, "error");
    const rows = await bindingRows();

    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText(OLD_CODE)).toBeInTheDocument();
    expect(within(rows[2]).getByText(OLD_CODE)).toBeInTheDocument();
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("gives a retired binding its reason and its replacement code", async () => {
    const rows = await bindingRows();

    expect(
      within(rows[1]).getByText("Superseded by a more specific concept"),
    ).toBeInTheDocument();
    const replacement = within(rows[1]).getByText(LONG_CODE, { selector: "code" });
    expect(replacement.textContent).toBe(LONG_CODE);
    expect(replacement.className).toContain("font-mono");
    expect(within(rows[1]).getByText(/Replaced by/)).toBeInTheDocument();
  });

  it("shows a retired binding with no replacement as just its reason", async () => {
    const rows = await bindingRows();

    expect(within(rows[2]).getByText("Bound in error")).toBeInTheDocument();
    expect(within(rows[2]).queryByText(/Replaced by/)).toBeNull();
  });

  it("shows the fully specified name exactly as served (FR-83)", async () => {
    const rows = await bindingRows();

    expect(within(rows[0]).getByText(FSN)).toBeInTheDocument();
  });

  it("shows an unknown binding status as its raw text", async () => {
    await renderEntry([
      {
        ...ENTRY_OK,
        body: entry({ bindings: [binding({ status: "suspended" })] }),
      },
      HISTORY_OK,
    ]);

    expect(await screen.findByText("suspended")).toBeInTheDocument();
  });
});

describe("properties", () => {
  it("shows every property received, with a multi-valued one in recorded order", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const discipline = screen.getByText("Discipline", { selector: "dt" });
    const values = within(discipline.nextElementSibling as HTMLElement).getAllByRole(
      "listitem",
    );
    expect(values.map((value) => value.textContent)).toEqual([
      "Chemical pathology",
      "Haematology",
    ]);
    expect(screen.getByText("Fasting is not required.")).toBeInTheDocument();
    expect(
      screen.getByText(/Justification: Agreed with the working group\./),
    ).toBeInTheDocument();
  });

  it("shows a coded value as its term, with a SNOMED CT code in mono beside it (FR-06)", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const specimen = screen.getByText("Specimen", { selector: "dt" })
      .nextElementSibling as HTMLElement;
    expect(within(specimen).getByText("Urine")).toBeInTheDocument();
    const chip = within(specimen).getByText(LONG_CODE, { selector: "code" });
    expect(chip.textContent).toBe(LONG_CODE);
    expect(chip.className).toContain("font-mono");
  });

  it("shows a local code by its term only, never as raw JSON", async () => {
    const { container } = await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(screen.getAllByText("Chemical pathology").length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain("chemical_pathology");
    expect(container.textContent).not.toContain('{"code"');
  });

  it("marks a property whose definition is deprecated, keeping its value (FR-11)", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const term = screen.getByText(/Legacy flag/, { selector: "dt" });
    expect(within(term).getByText("Deprecated")).toBeInTheDocument();
    expect(screen.getByText("kept")).toBeInTheDocument();
  });

  it("says so when there are none", async () => {
    await renderEntry([{ ...ENTRY_OK, body: entry({ properties: [] }) }, HISTORY_OK]);

    expect(
      await screen.findByText("This entry has no recorded properties."),
    ).toBeInTheDocument();
  });

  it("renders a value that is not text without breaking", async () => {
    await renderEntry([
      {
        ...ENTRY_OK,
        body: entry({
          properties: [
            property({ key: "n", label: "Count", value: 12 }),
            property({ key: "o", label: "Shape", value: { a: 1 } }),
          ],
        }),
      },
      HISTORY_OK,
    ]);

    expect(await screen.findByText("12")).toBeInTheDocument();
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
  });
});

describe("the details sidebar", () => {
  it("shows the entry's own facts", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    const sidebar = screen.getByRole("complementary", { name: "Entry details" });
    const facts = within(sidebar).getByRole("heading", { name: "Details" }).parentElement;
    expect(facts?.textContent).toContain(KEY);
    expect(facts?.textContent).toContain("8 characters");
    expect(facts?.textContent).toContain("Chemical pathology, Haematology");
    expect(facts?.textContent).toContain("Any specimenNo");
    expect(facts?.textContent).toContain("1 September 2026");
  });

  it("never shows a row version", async () => {
    const { container } = await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(container.textContent).not.toMatch(/row.?version/i);
  });

  it("says so when no discipline is recorded", async () => {
    await renderEntry([{ ...ENTRY_OK, body: entry({ disciplines: [] }) }, HISTORY_OK]);

    expect(await screen.findByText("None recorded")).toBeInTheDocument();
  });
});

describe("recent changes", () => {
  it("shows when, what and why, and no author to an anonymous reader", async () => {
    await renderEntry();

    const heading = await screen.findByRole("heading", { name: "Recent changes" });
    const section = heading.parentElement as HTMLElement;
    expect(
      await within(section).findByText("Catalogue entry updated"),
    ).toBeInTheDocument();
    expect(within(section).getByText("1 September 2026")).toBeInTheDocument();
    expect(within(section).getByText("Fields: Preferred term")).toBeInTheDocument();
    expect(within(section).getByText("Corrected the spelling.")).toBeInTheDocument();
    expect(within(section).queryByText(/^By /)).toBeNull();
    expect(section.textContent).not.toMatch(
      /row.?version|updated_at|entry.?id|catalogue_entry/i,
    );
  });

  it("names the author when the API sends one", async () => {
    await renderEntry([
      ENTRY_OK,
      {
        ...HISTORY_OK,
        body: {
          items: [historyEvent({ changed_by: "Dr Jane Citizen", note: null })],
          next_cursor: null,
        },
      },
    ]);

    expect(await screen.findByText("By Dr Jane Citizen")).toBeInTheDocument();
  });

  it("asks for a short page of history", async () => {
    const { calls } = await renderEntry();

    await screen.findByText("Catalogue entry updated");
    const historyCall = calls.find((call) => call.path.endsWith("/history"));
    expect(historyCall?.searchParams.get("limit")).toBe("5");
  });

  it("says so when nothing is recorded", async () => {
    await renderEntry([
      ENTRY_OK,
      { ...HISTORY_OK, body: { items: [], next_cursor: null } },
    ]);

    expect(await screen.findByText("No changes are recorded.")).toBeInTheDocument();
  });

  it("leaves the rest of the page usable while history loads", async () => {
    stubApi([ENTRY_OK, HISTORY_OK]);
    const hold = holdHistory();
    await renderRoute(`/catalogue/${KEY}`);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Loading recent changes…")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Code bindings" })).toBeInTheDocument();

    await act(async () => hold.release());
    expect(await screen.findByText("Catalogue entry updated")).toBeInTheDocument();
  });

  it("leaves the rest of the page usable when history fails", async () => {
    await renderEntry([
      ENTRY_OK,
      { ...HISTORY_OK, status: 500, body: { detail: "boom" } },
    ]);

    expect(
      await screen.findByText(/The recent changes could not be loaded/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Code bindings" })).toBeInTheDocument();
  });
});

describe("an entry that cannot be shown", () => {
  const NOT_FOUND_HEADING = { level: 1, name: /couldn't find that page/i } as const;

  it("shows the not-found page for a 404", async () => {
    await renderEntry([
      { ...ENTRY_OK, status: 404, body: { detail: "Not found" } },
      HISTORY_OK,
    ]);

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
    expect(screen.queryByText("Loading entry…")).toBeNull();
  });

  // The 422 body is the framework's own, not a declared shape, so the page
  // must not read it: an array here would break any code that did.
  it("shows the not-found page for a 422, without reading its body", async () => {
    await renderEntry([
      {
        ...ENTRY_OK,
        status: 422,
        body: [{ loc: ["path", "business_key"], msg: "bad", type: "x" }],
      },
      HISTORY_OK,
    ]);

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
    expect(screen.queryByText(/bad/)).toBeNull();
  });

  it("shows the same page for a malformed key as for an unknown one", async () => {
    stubApi([
      { ...ENTRY_OK, path: "/catalogue/entries/nonsense", status: 422, body: {} },
    ]);
    await renderRoute("/catalogue/nonsense");

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
  });

  it("shows the not-found page when a cached entry stops being public", async () => {
    let state: "ok" | "gone" = "ok";
    stubApi([ENTRY_OK, HISTORY_OK], {
      vary: ({ path }) =>
        state === "gone" && path.endsWith(`/entries/${KEY}`)
          ? { method: "GET", path, status: 404, body: { detail: "Not found" } }
          : null,
    });
    const { queryClient } = await renderRoute(`/catalogue/${KEY}`);
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    state = "gone";
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["api"] });
    });

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
  });

  it("offers a retry when the entry fails to load, and recovers", async () => {
    let failing = true;
    stubApi([ENTRY_OK, HISTORY_OK], {
      vary: ({ path }) =>
        failing && path.endsWith(`/entries/${KEY}`)
          ? { method: "GET", path, status: 500, body: { detail: "boom" } }
          : null,
    });
    await renderRoute(`/catalogue/${KEY}`);

    expect(
      await screen.findByText("This entry could not be loaded. Try again in a moment."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", NOT_FOUND_HEADING)).toBeNull();

    failing = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
  });

  it("keeps the entry on screen, with a warning, when a refresh fails", async () => {
    let failing = false;
    stubApi([ENTRY_OK, HISTORY_OK], {
      vary: ({ path }) =>
        failing && path.endsWith(`/entries/${KEY}`)
          ? { method: "GET", path, status: 500, body: { detail: "boom" } }
          : null,
    });
    const { queryClient } = await renderRoute(`/catalogue/${KEY}`);
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    failing = true;
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["api"] });
    });

    expect(
      await screen.findByText(/could not be refreshed just now/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
  });
});

describe("accessibility (NFR-31)", () => {
  it("has no automated accessibility violations", async () => {
    const { container } = await renderEntry([
      { ...ENTRY_OK, body: entry({ has_open_finding: true }) },
      HISTORY_OK,
    ]);
    await screen.findByText("Catalogue entry updated");

    await expectNoA11yViolations(container);
  });

  it("has no violations while the entry loads", async () => {
    stubApi([ENTRY_OK, HISTORY_OK]);
    const inner = globalThis.fetch;
    vi.stubGlobal("fetch", async (request: Request) => {
      await new Promise(() => {});
      return inner(request);
    });
    const { container } = await renderRoute(`/catalogue/${KEY}`);

    await expectNoA11yViolations(container);
  });

  it("has no violations on the not-found page it falls back to", async () => {
    const { container } = await renderEntry([
      { ...ENTRY_OK, status: 404, body: { detail: "Not found" } },
    ]);
    await screen.findByRole("heading", { level: 1, name: /couldn't find that page/i });

    await expectNoA11yViolations(container);
  });

  // A table that scrolls at a narrow width must take focus, or a keyboard user
  // cannot scroll it. jsdom has no layout, so axe cannot report this itself:
  // the test checks the markup its `scrollable-region-focusable` rule needs.
  it.each(["Synonyms and other-language terms", "Code bindings"])(
    "makes the scrollable %s table a focusable, named region",
    async (name) => {
      await renderEntry();
      await screen.findByRole("heading", { level: 1, name: "Ferritin" });

      const region = screen.getByRole("region", { name });
      expect(region).toHaveAttribute("tabindex", "0");
      expect(region.className).toContain("overflow-x-auto");
      expect(within(region).getByRole("table")).toBeInTheDocument();
    },
  );

  it("keeps the shell's landmarks and adds none of its own", async () => {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(screen.getAllByRole("complementary")).toHaveLength(1);
  });
});
