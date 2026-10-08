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
    updated_at: "2026-09-01T12:00:00Z",
    has_open_finding: false,
    code: LONG_CODE,
    disciplines: ["Chemical pathology", "Haematology"],
    label_provenance: PROVENANCE,
    row_version: 7,
    designations: [
      {
        term: "Serum ferritin",
        status: "active",
        length: 14,
        label_provenance: { designation: "synonym", semantic_tag: "not_applicable" },
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
    snomed_synonyms: snomedSynonyms("available", [
      "Ferritin level, serum",
      "Ferritin concentration",
    ]),
    ...overrides,
  };
}

/** `EntryDetail.snomed_synonyms` as the API serves it. */
function snomedSynonyms(status: "available" | "unavailable", terms: string[] = []) {
  return {
    status,
    terms,
    label_provenance: { designation: "synonym", semantic_tag: "not_applicable" },
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
    changed_fields: ["preferred_term", "status"],
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

/**
 * The sighted-reader copy of a message, as opposed to its live-region copy.
 * A live region is a `role="status"` element mounted empty and filled later, so
 * a screen reader announces the change (`LiveRegion`).
 */
async function visibleNote(text: string | RegExp) {
  const matches = await screen.findAllByText(text);
  const visible = matches.find((node) => node.closest("[role=status]") === null);
  expect(visible).toBeDefined();
  return visible as HTMLElement;
}

async function expectAnnounced(text: string | RegExp) {
  await waitFor(() => {
    const spoken = screen
      .getAllByRole("status")
      .some((region) =>
        typeof text === "string"
          ? region.textContent === text
          : text.test(region.textContent ?? ""),
      );
    expect(spoken).toBe(true);
  });
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
  async function termRows(overrides: Record<string, unknown> = {}) {
    await renderEntry([{ ...ENTRY_OK, body: entry(overrides) }, HISTORY_OK]);
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    const table = screen.getByRole("table", { name: "Terms by type" });
    return within(table).getAllByRole("row").slice(1);
  }

  it("lists the RCPA terms, then the active binding's names, in one table (FR-04)", async () => {
    const rows = await termRows();

    expect(rows.map((row) => row.textContent)).toEqual([
      "FerritinRCPA Preferred",
      "Serum ferritinRCPA Synonym",
      `${FSN}SNOMED CT FSN`,
      "Ferritin levelSNOMED CT Preferred",
      "Ferritin level, serumSNOMED CT Synonym",
      "Ferritin concentrationSNOMED CT Synonym",
    ]);
    expect(screen.queryByText(/synonyms could not be loaded/)).not.toBeInTheDocument();
  });

  it("says the SNOMED CT synonyms could not be loaded, and keeps every stored row (FR-54)", async () => {
    const rows = await termRows({ snomed_synonyms: snomedSynonyms("unavailable") });

    expect(rows.map((row) => row.textContent)).toEqual([
      "FerritinRCPA Preferred",
      "Serum ferritinRCPA Synonym",
      `${FSN}SNOMED CT FSN`,
      "Ferritin levelSNOMED CT Preferred",
    ]);
    expect(
      screen.getByText("SNOMED CT synonyms could not be loaded. Try again later."),
    ).toBeInTheDocument();
  });

  it("shows no synonym rows and no notice when the concept has no synonyms", async () => {
    const rows = await termRows({ snomed_synonyms: snomedSynonyms("available") });

    expect(rows).toHaveLength(4);
    expect(screen.queryByText(/synonyms could not be loaded/)).not.toBeInTheDocument();
  });

  it("has a Term and a Type column, and no Language or Status column", async () => {
    await termRows();

    const table = screen.getByRole("table", { name: "Terms by type" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Term", "Type"]);
  });

  it("shows the FSN exactly as served, semantic tag included (FR-82, FR-83)", async () => {
    const rows = await termRows();

    expect(within(rows[2]).getByText(FSN)).toBeInTheDocument();
  });

  it("leaves out the SNOMED CT Preferred row when there is no AU preferred term", async () => {
    const rows = await termRows({
      bindings: [binding({ status: "active", au_preferred_term: null })],
      snomed_synonyms: snomedSynonyms("available"),
    });

    expect(rows.map((row) => row.textContent)).toEqual([
      "FerritinRCPA Preferred",
      "Serum ferritinRCPA Synonym",
      `${FSN}SNOMED CT FSN`,
    ]);
  });

  it("shows the RCPA rows alone when no binding is active", async () => {
    const rows = await termRows({
      bindings: [binding({ status: "retired", retirement_reason: "Bound in error" })],
      snomed_synonyms: null,
    });

    expect(rows.map((row) => row.textContent)).toEqual([
      "FerritinRCPA Preferred",
      "Serum ferritinRCPA Synonym",
    ]);
  });

  it("shows only the preferred term when there is nothing else", async () => {
    const rows = await termRows({
      designations: [],
      bindings: [],
      code: null,
      snomed_synonyms: null,
    });

    expect(rows.map((row) => row.textContent)).toEqual(["FerritinRCPA Preferred"]);
  });
});

describe("retired SNOMED CT codes", () => {
  async function retiredTable() {
    await renderEntry();
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    return screen.getByRole("table", { name: "Retired code bindings" });
  }

  async function retiredRows() {
    return within(await retiredTable())
      .getAllByRole("row")
      .slice(1);
  }

  it("lists only the retired bindings", async () => {
    const table = await retiredTable();
    const rows = within(table).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText(OLD_CODE)).toBeInTheDocument();
    expect(within(rows[1]).getByText(OLD_CODE)).toBeInTheDocument();
    expect(within(table).queryByText("Ferritin level")).toBeNull();
  });

  it("shows two retired rows that share a code, without a key clash", async () => {
    const errors = vi.spyOn(console, "error");
    const rows = await retiredRows();

    expect(rows).toHaveLength(2);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("gives a retired binding its reason and its replacement code (FR-08)", async () => {
    const rows = await retiredRows();

    expect(
      within(rows[0]).getByText("Superseded by a more specific concept"),
    ).toBeInTheDocument();
    const replacement = within(rows[0]).getByText(LONG_CODE, { selector: "code" });
    expect(replacement.textContent).toBe(LONG_CODE);
    expect(replacement.className).toContain("font-mono");
    expect(within(rows[0]).getByText(/Replaced by/)).toBeInTheDocument();
  });

  it("shows a retired binding with no replacement as just its reason", async () => {
    const rows = await retiredRows();

    expect(within(rows[1]).getByText("Bound in error")).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/Replaced by/)).toBeNull();
  });

  it("is absent when every binding is active", async () => {
    await renderEntry([
      { ...ENTRY_OK, body: entry({ bindings: [binding({ status: "active" })] }) },
      HISTORY_OK,
    ]);
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });

    expect(screen.queryByRole("table", { name: "Retired code bindings" })).toBeNull();
    expect(screen.queryByText("Retired SNOMED CT codes")).toBeNull();
  });
});

describe("the details sidebar", () => {
  async function details(overrides: Record<string, unknown> = {}) {
    await renderEntry([{ ...ENTRY_OK, body: entry(overrides) }, HISTORY_OK]);
    await screen.findByRole("heading", { level: 1, name: "Ferritin" });
    const sidebar = screen.getByRole("complementary", { name: "Entry details" });
    return within(sidebar).getByRole("heading", { name: "Details" })
      .parentElement as HTMLElement;
  }

  function labels(facts: HTMLElement) {
    return Array.from(facts.querySelectorAll("dt")).map((term) => term.textContent);
  }

  function valueOf(facts: HTMLElement, label: string) {
    return within(facts).getByText(label, { selector: "dt" })
      .nextElementSibling as HTMLElement;
  }

  it("shows the entry's own facts, and no term length (FR-85)", async () => {
    const facts = await details();

    expect(facts.textContent).toContain(KEY);
    expect(facts.textContent).toContain("Chemical pathology, Haematology");
    expect(facts.textContent).toContain("1 September 2026");
    expect(facts.textContent).not.toMatch(/term length|characters/i);
  });

  it("lists identifier, status, disciplines, the properties, then last updated", async () => {
    const facts = await details();

    expect(labels(facts)).toEqual([
      "Identifier",
      "Status",
      "Disciplines",
      "Specimen",
      "Usage guidance",
      "Legacy flagDeprecated",
      "Last updated",
    ]);
  });

  it("shows the discipline once, as the Disciplines row", async () => {
    const facts = await details();

    expect(within(facts).queryByText("Discipline", { selector: "dt" })).toBeNull();
    expect(within(facts).getAllByText(/Chemical pathology/)).toHaveLength(1);
  });

  it("shows a specimen as its trimmed term, without a code chip (FR-04)", async () => {
    const facts = await details({
      properties: [
        property({
          key: "specimen",
          label: "Specimen",
          value: coded(LONG_CODE, "Serum specimen", "http://snomed.info/sct"),
        }),
      ],
    });

    const specimen = valueOf(facts, "Specimen");
    expect(specimen.textContent).toBe("Serum");
    expect(specimen.querySelector("code")).toBeNull();
  });

  it("keeps a bare Specimen term as it is", async () => {
    const facts = await details({
      properties: [
        property({
          key: "specimen",
          label: "Specimen",
          value: coded(LONG_CODE, "Specimen", "http://snomed.info/sct"),
        }),
      ],
    });

    expect(valueOf(facts, "Specimen").textContent).toBe("Specimen");
  });

  it("lists specimens in stored order and once each after trimming", async () => {
    const facts = await details({
      properties: [
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 2,
          value: coded("3", "Plasma", "http://snomed.info/sct"),
        }),
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 0,
          value: coded("1", "Serum", "http://snomed.info/sct"),
        }),
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 1,
          value: coded("2", "Serum specimen", "http://snomed.info/sct"),
        }),
      ],
    });

    const items = within(valueOf(facts, "Specimen")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Serum", "Plasma"]);
  });

  it("keeps a repeated specimen that carries a different justification", async () => {
    const facts = await details({
      properties: [
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 0,
          value: coded("1", "Serum", "http://snomed.info/sct"),
          justification: "Preferred sample",
        }),
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 1,
          value: coded("2", "Serum specimen", "http://snomed.info/sct"),
          justification: "Also accepted when haemolysed",
        }),
        property({
          key: "specimen",
          label: "Specimen",
          ordinal: 2,
          value: coded("3", "Serum specimen", "http://snomed.info/sct"),
          justification: "Also accepted when haemolysed",
        }),
      ],
    });

    const items = within(valueOf(facts, "Specimen")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "SerumJustification: Preferred sample",
      "SerumJustification: Also accepted when haemolysed",
    ]);
  });

  it("shows a specimen with no recorded term as its code, in plain text (FR-06)", async () => {
    const facts = await details({
      properties: [
        property({
          key: "specimen",
          label: "Specimen",
          value: coded(OLD_CODE, null, "http://snomed.info/sct"),
        }),
      ],
    });

    const specimen = valueOf(facts, "Specimen");
    expect(specimen.textContent).toBe(OLD_CODE);
    expect(specimen.querySelector("code")).toBeNull();
  });

  it("shows a coded value that is not a specimen as its term with a mono code (FR-06)", async () => {
    const facts = await details({
      properties: [
        property({
          key: "subgroup",
          label: "Subgroup",
          value: coded(LONG_CODE, "Iron studies", "http://snomed.info/sct"),
        }),
      ],
    });

    const subgroup = valueOf(facts, "Subgroup");
    expect(within(subgroup).getByText("Iron studies")).toBeInTheDocument();
    const chip = within(subgroup).getByText(LONG_CODE, { selector: "code" });
    expect(chip.textContent).toBe(LONG_CODE);
    expect(chip.className).toContain("font-mono");
  });

  it("shows a local code by its term only, never as raw JSON", async () => {
    const facts = await details({
      properties: [
        property({
          key: "setting",
          label: "Setting",
          value: coded("outpatient", "Outpatient", LOCAL_SYSTEM),
        }),
      ],
    });

    expect(valueOf(facts, "Setting").textContent).toBe("Outpatient");
    expect(facts.textContent).not.toContain('{"code"');
  });

  it("shows usage guidance with its justification", async () => {
    const facts = await details();

    expect(within(facts).getByText("Fasting is not required.")).toBeInTheDocument();
    expect(
      within(facts).getByText(/Justification: Agreed with the working group\./),
    ).toBeInTheDocument();
  });

  it("lets a long usage guidance value wrap inside the card", async () => {
    const long = `${"Collect before any iron supplement is given. ".repeat(12)}Done.`;
    const facts = await details({
      properties: [
        property({ key: "usage_guidance", label: "Usage guidance", value: long }),
      ],
    });

    const value = within(facts).getByText(long);
    expect(value.className).toContain("[overflow-wrap:anywhere]");
    expect((value.closest("dd") as HTMLElement).className).toContain("min-w-0");
  });

  it("shows a multi-valued property in recorded order", async () => {
    const facts = await details({
      properties: [
        property({ key: "tags", label: "Tags", ordinal: 1, value: "second" }),
        property({ key: "tags", label: "Tags", ordinal: 0, value: "first" }),
      ],
    });

    const items = within(valueOf(facts, "Tags")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["first", "second"]);
  });

  it("marks a property whose definition is deprecated, keeping its value (FR-11)", async () => {
    const facts = await details();

    const term = within(facts).getByText(/Legacy flag/, { selector: "dt" });
    expect(within(term).getByText("Deprecated")).toBeInTheDocument();
    expect(within(facts).getByText("kept")).toBeInTheDocument();
  });

  it("renders a value that is not text without breaking", async () => {
    const facts = await details({
      properties: [
        property({ key: "n", label: "Count", value: 12 }),
        property({ key: "o", label: "Shape", value: { a: 1 } }),
      ],
    });

    expect(within(facts).getByText("12")).toBeInTheDocument();
    expect(within(facts).getByText('{"a":1}')).toBeInTheDocument();
  });

  it("has no Properties card, and no property row when there are none", async () => {
    const facts = await details({ properties: [] });

    expect(screen.queryByRole("heading", { name: "Properties" })).toBeNull();
    expect(labels(facts)).toEqual([
      "Identifier",
      "Status",
      "Disciplines",
      "Last updated",
    ]);
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
    expect(
      within(section).getByText("Fields: Requesting term, Status"),
    ).toBeInTheDocument();
    expect(within(section).getByText("Corrected the spelling.")).toBeInTheDocument();
    expect(within(section).queryByText(/^By /)).toBeNull();
    expect(section.textContent).not.toMatch(
      /row.?version|updated_at|entry.?id|catalogue_entry/i,
    );
  });

  it("names no internal key for a binding replacement event", async () => {
    await renderEntry([
      ENTRY_OK,
      {
        ...HISTORY_OK,
        body: {
          items: [
            historyEvent({
              action: "code_binding.replacement_linked",
              changed_fields: ["replaced_by_binding_id", "status", "retirement_reason"],
            }),
          ],
          next_cursor: null,
        },
      },
    ]);

    expect(
      await screen.findByText("Fields: Status, Retirement reason"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/binding id/i)).toBeNull();
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

  it("links to the full history, as a target big enough to hit (FR-19, NFR-31)", async () => {
    await renderEntry();

    const link = await screen.findByRole("link", { name: "View full history" });
    expect(link).toHaveAttribute("href", `/catalogue/${KEY}/history`);
    expect(link.className).toContain("min-h-6");
    expect(
      within(
        screen.getByRole("heading", { name: "Recent changes" })
          .parentElement as HTMLElement,
      ).getByRole("link", { name: "View full history" }),
    ).toBe(link);
  });

  it("opens the full history page from that link", async () => {
    stubApi([ENTRY_OK, HISTORY_OK]);
    const { router } = await renderRoute(`/catalogue/${KEY}`);

    await userEvent.click(await screen.findByRole("link", { name: "View full history" }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: `Change history for ${KEY}`,
      }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/catalogue/${KEY}/history`);
  });

  it("offers no link when there is no history to open", async () => {
    await renderEntry([
      ENTRY_OK,
      { ...HISTORY_OK, body: { items: [], next_cursor: null } },
    ]);
    await screen.findByText("No changes are recorded.");

    expect(screen.queryByRole("link", { name: "View full history" })).toBeNull();
  });

  it("offers no link when the recent changes fail to load", async () => {
    await renderEntry([
      ENTRY_OK,
      { ...HISTORY_OK, status: 500, body: { detail: "boom" } },
    ]);
    await visibleNote(/The recent changes could not be loaded/);

    expect(screen.queryByRole("link", { name: "View full history" })).toBeNull();
  });

  it("leaves the rest of the page usable while history loads", async () => {
    stubApi([ENTRY_OK, HISTORY_OK]);
    const hold = holdHistory();
    await renderRoute(`/catalogue/${KEY}`);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Loading recent changes…")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Terms by type" })).toBeInTheDocument();

    await act(async () => hold.release());
    expect(await screen.findByText("Catalogue entry updated")).toBeInTheDocument();
  });

  it("leaves the rest of the page usable when history fails", async () => {
    await renderEntry([
      ENTRY_OK,
      { ...HISTORY_OK, status: 500, body: { detail: "boom" } },
    ]);

    expect(
      await visibleNote(/The recent changes could not be loaded/),
    ).toBeInTheDocument();
    await expectAnnounced(/The recent changes could not be loaded/);
    expect(
      screen.getByRole("heading", { level: 1, name: "Ferritin" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Terms by type" })).toBeInTheDocument();
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
      await visibleNote("This entry could not be loaded. Try again in a moment."),
    ).toBeInTheDocument();
    await expectAnnounced("This entry could not be loaded. Try again in a moment.");
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

    expect(await visibleNote(/could not be refreshed just now/)).toBeInTheDocument();
    await expectAnnounced(/could not be refreshed just now/);
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
  it.each(["Terms by type", "Retired code bindings"])(
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
