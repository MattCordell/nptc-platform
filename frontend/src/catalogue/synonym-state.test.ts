import { describe, expect, it } from "vitest";

import {
  activeSynonyms,
  addTextError,
  blankRows,
  hasSynonymChanges,
  initialSynonymRows,
  mergeSynonymRows,
  synonymChanges,
} from "./synonym-state.ts";
import type { SynonymRow } from "./synonym-state.ts";

/**
 * The synonym rows behind the edit form (FR-04, FR-36, FR-38), with no React
 * in the way.
 */

function counter() {
  let next = 0;
  return () => `row-${++next}`;
}

function row(original: string, overrides: Partial<SynonymRow> = {}): SynonymRow {
  return { id: `id-${original}`, original, term: original, removed: false, ...overrides };
}

describe("activeSynonyms", () => {
  it("lists the active synonyms and leaves out a retired one", () => {
    expect(
      activeSynonyms([
        { term: "FBC", status: "active", length: 3 },
        { term: "Old", status: "retired", length: 3 },
      ] as never),
    ).toEqual(["FBC"]);
  });
});

describe("mergeSynonymRows", () => {
  it("shows a synonym another editor added, and drops one they retired, in the entry's order", () => {
    const rows = [row("FBC"), row("Old")];

    const merged = mergeSynonymRows(rows, ["FBC", "Complete blood count"], counter());

    expect(merged.map((item) => item.term)).toEqual(["FBC", "Complete blood count"]);
  });

  it("keeps the row object, and so its identity, for an untouched synonym", () => {
    const rows = [row("FBC")];

    expect(mergeSynonymRows(rows, ["FBC"], counter())[0]).toBe(rows[0]);
  });

  it("keeps the editor's edit and their removal while the stored term is still there", () => {
    const rows = [row("FBC", { term: "Full count" }), row("CBC", { removed: true })];

    const merged = mergeSynonymRows(rows, ["FBC", "CBC"], counter());

    expect(merged.map((item) => [item.original, item.term, item.removed])).toEqual([
      ["FBC", "Full count", false],
      ["CBC", "CBC", true],
    ]);
  });

  it("drops an edit whose stored term is gone, and shows what the entry holds instead", () => {
    const rows = [row("FBC", { term: "Full count" })];

    const merged = mergeSynonymRows(rows, ["Full count"], counter());

    expect(merged.map((item) => [item.original, item.term])).toEqual([
      ["Full count", "Full count"],
    ]);
  });
});

describe("synonymChanges", () => {
  it("amends an edited row, retires a removed one, and adds the pasted terms", () => {
    const rows = initialSynonymRows(["FBC", "CBC", "Same"], counter());
    rows[0] = { ...rows[0], term: "Full count" };
    rows[1] = { ...rows[1], removed: true };

    const found = synonymChanges(rows, "Zovirax;;Cyclir");

    expect(found.amendments.map((item) => item.change)).toMatchObject([
      { kind: "synonym_amend", currentTerm: "FBC", newTerm: "Full count" },
    ]);
    expect(found.retirements.map((item) => item.change)).toMatchObject([
      { kind: "synonym_retire", term: "CBC" },
    ]);
    expect(found.addition).toMatchObject({
      kind: "synonyms_add",
      terms: ["Zovirax", "Cyclir"],
    });
  });

  it("does not count an edit that cleans to the stored term", () => {
    const rows = [row("FBC", { term: "FBC " })];

    const found = synonymChanges(rows, "");

    expect(found.amendments).toEqual([]);
  });

  it("sends nothing for a cell of only delimiters", () => {
    expect(synonymChanges([], ";; ;").addition).toBeNull();
  });
});

describe("what the editor must fix first", () => {
  it("names a blank synonym that is not being removed", () => {
    const rows = [row("FBC", { term: " " }), row("CBC", { term: "", removed: true })];

    expect(blankRows(rows).map((item) => item.original)).toEqual(["FBC"]);
  });

  it("refuses a batch over the limit", () => {
    const cell = Array.from({ length: 101 }, (_, index) => `T${index}`).join(";");

    expect(addTextError(cell)).toMatch(/at most 100/);
    expect(addTextError("One;Two")).toBeNull();
  });

  it("reports a change when a row is edited, removed or a term is typed", () => {
    expect(hasSynonymChanges([row("FBC")], "")).toBe(false);
    expect(hasSynonymChanges([row("FBC", { removed: true })], "")).toBe(true);
    expect(hasSynonymChanges([row("FBC", { term: "x" })], "")).toBe(true);
    expect(hasSynonymChanges([row("FBC")], "New")).toBe(true);
  });

  it("agrees with synonymChanges: spacing alone is no change, a blank row still is one", () => {
    expect(hasSynonymChanges([row("FBC", { term: "FBC " })], "")).toBe(false);
    expect(hasSynonymChanges([row("FBC", { term: " FBC " })], "")).toBe(false);
    expect(hasSynonymChanges([row("FBC", { term: " " })], "")).toBe(true);
    expect(hasSynonymChanges([], ";; ;")).toBe(false);
  });
});
