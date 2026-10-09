import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The SNOMED CT code picker and the RCPA Synonyms inside the edit form
 * (FR-04, FR-06, FR-08, FR-26, FR-36, FR-38, FR-54).
 *
 * Driven through the real admin edit route. The search stub answers every
 * query the same way, so these tests prove what the form does with an answer,
 * not how a terminology server matches: Ontoserver matches word prefixes and the
 * offline stub matches any substring.
 */

const BUSINESS_KEY = "NPTC-000902";
const EDIT_URL = `/admin/catalogue/${BUSINESS_KEY}/edit`;
const ENTRY_PATH = `/catalogue/admin/entries/${BUSINESS_KEY}`;
const AMEND_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations/amendment`;
const RETIRE_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations/retirement`;
const ADD_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations`;
const BIND_PATH = `/catalogue/entries/${BUSINESS_KEY}/bindings`;
const REINSTATE_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations/reinstatement`;
const ACK_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations/acknowledgement`;
const USAGE_PATH = `/catalogue/entries/${BUSINESS_KEY}/properties/usage_guidance`;

// The PRD's FR-83 regression fixture: a double-parenthesis FSN a careless
// semantic-tag strip would mangle.
const CODE = "391483001";
const FSN = "Microscopy (acid fast bacilli) (procedure)";
const AU_PT = "Microscopy (AFB)";
const OLD_CODE = "252275004";
const OLD_FSN = "Haematology test (procedure)";

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const DEFINITIONS = {
  items: [
    {
      key: "usage_guidance",
      label: "Usage guidance",
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
      form_control: { control: "textarea", params: {} },
    },
  ],
};

const ACTIVE_BINDING = {
  code: OLD_CODE,
  fsn: OLD_FSN,
  au_preferred_term: "Haematology test",
  status: "active",
  retirement_reason: null,
  replaced_by_code: null,
};

const ENTRY = {
  business_key: BUSINESS_KEY,
  preferred_term: "Full blood count",
  length: 16,
  status: "active",
  updated_at: "2026-09-01T04:30:00Z",
  row_version: 4,
  designations: [
    { term: "FBC", status: "active", length: 3 },
    { term: "Complete blood count", status: "active", length: 20 },
    { term: "Old note", status: "retired", length: 8 },
  ],
  bindings: [],
  properties: [],
};

const ENTRY_WITH_CODE = { ...ENTRY, bindings: [ACTIVE_BINDING] };

function entryRoute(body: unknown = ENTRY): Route {
  return { method: "GET", path: ENTRY_PATH, status: 200, body };
}

const PROPERTIES_OK: Route = {
  method: "GET",
  path: "/registry/properties",
  status: 200,
  body: DEFINITIONS,
};

const PROCEDURES_OK: Route = {
  method: "GET",
  path: "/terminology/procedures",
  status: 200,
  body: {
    items: [{ code: CODE, au_preferred_term: AU_PT, label_provenance: {} }],
    total: 1,
  },
};

function conceptRoute(overrides: { fsn?: string | null; active?: boolean | null } = {}) {
  const { fsn = FSN, active = true } = overrides;
  return {
    method: "GET",
    path: `/terminology/concepts/${CODE}`,
    status: 200,
    body: {
      system: "http://snomed.info/sct",
      code: CODE,
      fsn,
      au_preferred_term: AU_PT,
      active,
      edition: "au",
      resolved_version: "http://snomed.info/sct/32506021000036107/version/20260101",
    },
  } satisfies Route;
}

function bindOk(rowVersion: number): Route {
  return {
    method: "POST",
    path: BIND_PATH,
    status: 201,
    body: {
      binding: { ...ACTIVE_BINDING, code: CODE, fsn: FSN },
      row_version: rowVersion,
    },
  };
}

function replaceOk(rowVersion: number): Route {
  return {
    method: "POST",
    path: `${BIND_PATH}/${OLD_CODE}/replacement`,
    status: 200,
    body: { items: [], row_version: rowVersion },
  };
}

function amendOk(term: string, rowVersion: number): Route {
  return {
    method: "POST",
    path: AMEND_PATH,
    status: 200,
    body: {
      designation: { term, status: "active", length: term.length, label_provenance: {} },
      warnings: [],
      row_version: rowVersion,
    },
  };
}

function retireOk(rowVersion: number): Route {
  return {
    method: "POST",
    path: RETIRE_PATH,
    status: 200,
    body: {
      designation: { term: "FBC", status: "retired", length: 3, label_provenance: {} },
      row_version: rowVersion,
    },
  };
}

function addOk(rowVersion: number, warnings: unknown[] = []): Route {
  return {
    method: "POST",
    path: ADD_PATH,
    status: 201,
    body: { designations: [], warnings, row_version: rowVersion },
  };
}

function usageOk(rowVersion: number): Route {
  return {
    method: "PUT",
    path: USAGE_PATH,
    status: 200,
    body: { values: [], row_version: rowVersion },
  };
}

function form() {
  return within(screen.getByRole("region", { name: "Edit entry" }));
}

function summary() {
  return within(screen.getByRole("region", { name: "Some changes were not saved" }));
}

async function renderLoaded() {
  const rendered = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByRole("heading", { name: "Full blood count", level: 1 });
  await screen.findByLabelText("Usage guidance");
  return rendered;
}

type User = ReturnType<typeof userEvent.setup>;

function writes(calls: { method: string; path: string; body: unknown }[]) {
  return calls.filter((call) => call.method !== "GET");
}

function bodyOf(
  calls: { method: string; path: string; body: unknown }[],
  suffix: string,
) {
  return calls.find((call) => call.method !== "GET" && call.path.endsWith(suffix))?.body;
}

async function fillNote(user: User, note: string) {
  await user.type(form().getByLabelText("Changelog note"), note);
}

async function save(user: User) {
  await user.click(form().getByRole("button", { name: "Save" }));
}

/** Searches for a procedure and picks the one result, waiting out the search debounce. */
async function pickProcedure(user: User, label = "SNOMED CT code") {
  await user.type(form().getByLabelText(label), "micro");
  await user.click(
    await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
  );
  await form().findByText(FSN, {}, { timeout: 2000 });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the SNOMED CT code picker (FR-26)", () => {
  it("searches the scoped route and shows the chosen concept's names from the server", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const user = userEvent.setup();
    await renderLoaded();

    await pickProcedure(user);

    expect(form().getByText(AU_PT)).toBeInTheDocument();
    expect(form().getByText("Saving binds this code to the entry.")).toBeInTheDocument();
    const search = calls.find((call) => call.path.endsWith("/terminology/procedures"));
    expect(search?.searchParams.get("q")).toBe("micro");
    // The scope is the server's, so the browser sends no ECL of its own.
    expect([...(search?.searchParams.keys() ?? [])]).toEqual(["q"]);
  });

  it("offers no label to type: the FSN and AU preferred term are only ever read", async () => {
    stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const user = userEvent.setup();
    await renderLoaded();
    await pickProcedure(user);

    const textboxes = form()
      .getAllByRole("textbox")
      .map((box) => box.getAttribute("aria-label") ?? box.id);
    expect(textboxes.join(" ")).not.toMatch(/fully specified|au preferred/i);
  });

  it("says nothing matches, and offers no concept, for a code outside Procedure", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      { ...PROCEDURES_OK, body: { items: [], total: 0 } },
      conceptRoute(),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "138875005");

    expect(
      await screen.findByText(/No procedure matches/, {}, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    // `$lookup` would accept that code, so it must never be asked.
    expect(calls.some((call) => call.path.includes("/terminology/concepts/"))).toBe(
      false,
    );
  });

  it("says the terminology server is unreachable, and the rest of the form still saves (FR-54)", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "GET",
        path: "/terminology/procedures",
        status: 503,
        body: { detail: "The terminology server is not reachable right now." },
      },
      usageOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    expect(
      await form().findByText(
        /terminology server is not reachable/,
        {},
        { timeout: 2000 },
      ),
    ).toBeInTheDocument();
    expect(form().getByText(/still change the rest of this entry/)).toBeInTheDocument();

    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Guidance while the server is down");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(writes(calls).map((call) => call.path.split("/api/v1")[1])).toEqual([
      USAGE_PATH,
    ]);
  });

  it("refuses a code the terminology server cannot name, and sends nothing", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute({ fsn: null }),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );
    await form().findByText(/did not return a name/, {}, { timeout: 2000 });
    await fillNote(user, "Bind a code the server cannot name");
    await save(user);

    expect(await form().findAllByText(/did not return a name/)).not.toHaveLength(0);
    expect(writes(calls)).toHaveLength(0);
  });

  it("drops the chosen code when the editor clears it", async () => {
    stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const user = userEvent.setup();
    await renderLoaded();
    await pickProcedure(user);

    await user.click(form().getByRole("button", { name: "Clear the chosen code" }));

    expect(form().queryByText(FSN)).not.toBeInTheDocument();
    expect(form().getByRole("button", { name: "Save" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});

describe("saving a code (FR-06, FR-08)", () => {
  it("binds the chosen code with the server's names, and the code stays a string", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
      bindOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await pickProcedure(user);

    await fillNote(user, "Add the primary code");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, BIND_PATH)).toEqual({
      code: CODE,
      fsn: FSN,
      au_preferred_term: AU_PT,
      edition_hint: "au",
      reason: "Add the primary code",
      expected_row_version: 4,
    });
    const wire = calls.find((call) => call.path.endsWith(BIND_PATH))?.text;
    expect(wire).toContain(`"code":"${CODE}"`);
  });

  it("replaces the active code in one request", async () => {
    const calls = stubApi([
      entryRoute(ENTRY_WITH_CODE),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
      replaceOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    expect(form().getByText(OLD_FSN)).toBeInTheDocument();

    await pickProcedure(user, "Replacement SNOMED CT code");
    expect(
      form().getByText(`Saving retires ${OLD_CODE} and binds this code in its place.`),
    ).toBeInTheDocument();
    await fillNote(user, "Move to the current code");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, `${BIND_PATH}/${OLD_CODE}/replacement`)).toEqual({
      successor: {
        code: CODE,
        fsn: FSN,
        au_preferred_term: AU_PT,
        edition_hint: "au",
      },
      reason: "Move to the current code",
      expected_row_version: 4,
    });
    expect(calls.some((call) => call.path.endsWith(`${BIND_PATH}`))).toBe(false);
  });

  it("marks the code with the server's reason when it is refused, and still saves the rest", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
      {
        method: "POST",
        path: BIND_PATH,
        status: 409,
        body: {
          detail: "This code is already actively bound to another catalogue entry.",
        },
      },
      usageOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await pickProcedure(user);
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Bind and describe");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(
      summary().getByText(/already actively bound to another catalogue entry/),
    ).toBeVisible();
    expect(form().getByText(FSN)).toBeInTheDocument();
    // A refusal changed nothing on the server, so the next write keeps the loaded version.
    expect(bodyOf(calls, USAGE_PATH)).toMatchObject({ expected_row_version: 4 });
  });
});

describe("RCPA Synonyms (FR-04, FR-36)", () => {
  it("shows each active synonym in a text box, and no retired one", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    await renderLoaded();

    expect(form().getByLabelText("Synonym 1")).toHaveValue("FBC");
    expect(form().getByLabelText("Synonym 2")).toHaveValue("Complete blood count");
    expect(form().queryByDisplayValue("Old note")).not.toBeInTheDocument();
  });

  it("amends a synonym through the amendment route, addressed as a synonym", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, amendOk("Full count", 5)]);
    const user = userEvent.setup();
    await renderLoaded();

    const box = form().getByLabelText("Synonym 1");
    await user.clear(box);
    await user.paste("Full count");
    await fillNote(user, "Reword the synonym");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, AMEND_PATH)).toEqual({
      term: "FBC",
      new_term: "Full count",
      target: "synonym",
      expected_row_version: 4,
      reason: "Reword the synonym",
    });
  });

  it("retires a removed synonym only when Save is pressed, and Keep undoes a removal", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, retireOk(5)]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Remove FBC" }));
    expect(form().getByText(/will be removed when you save/)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);

    await user.click(form().getByRole("button", { name: "Keep FBC" }));
    expect(form().getByLabelText("Synonym 1")).toHaveValue("FBC");
    expect(form().getByRole("button", { name: "Save" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    await user.click(form().getByRole("button", { name: "Remove FBC" }));
    await fillNote(user, "FBC is no longer used");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, RETIRE_PATH)).toEqual({
      term: "FBC",
      reason: "FBC is no longer used",
      expected_row_version: 4,
    });
  });

  it("splits a pasted cell, shows what it will add, and adds the terms in one request", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, addOk(5)]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax;;Cyclir; ");
    expect(form().getByText(/Save will add 2 terms/)).toHaveTextContent(
      "“Zovirax”, “Cyclir”",
    );
    await fillNote(user, "Add two trade names");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(bodyOf(calls, ADD_PATH)).toEqual({
      terms: ["Zovirax", "Cyclir"],
      reason: "Add two trade names",
      expected_row_version: 4,
    });
    expect(form().getByLabelText("Add synonyms")).toHaveValue("");
  });

  it("refuses a batch over the limit before it reaches the server", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, addOk(5)]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste(Array.from({ length: 101 }, (_, index) => `T${index}`).join(";"));
    await fillNote(user, "Too many at once");
    await save(user);

    expect(
      await form().findAllByText(/at most 100 can be added at once/),
    ).not.toHaveLength(0);
    expect(writes(calls)).toHaveLength(0);
  });

  it("refuses a blank synonym and names the box", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.clear(form().getByLabelText("Synonym 1"));
    await fillNote(user, "Clear a synonym by mistake");
    await save(user);

    expect(
      await form().findAllByText("Enter the synonym, or remove it."),
    ).not.toHaveLength(0);
    expect(writes(calls)).toHaveLength(0);
  });

  it("marks a synonym that collides with another entry, keeps its text, and still saves the rest", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This term is already in use on another entry.",
          collisions: [
            {
              severity: "error",
              business_key: "NPTC-000900",
              preferred_term: "Iron studies",
              label_provenance: {},
            },
          ],
        },
      },
      usageOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const box = form().getByLabelText("Synonym 1");
    await user.clear(box);
    await user.paste("Iron studies");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Reword and describe");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(summary().getByText(/NPTC-000900/)).toBeInTheDocument();
    expect(form().getByLabelText("Synonym 1")).toHaveValue("Iron studies");
    expect(bodyOf(calls, USAGE_PATH)).toMatchObject({ expected_row_version: 4 });
  });

  it("shows a warning from an added synonym in the summary (FR-05)", async () => {
    stubApi([
      entryRoute(),
      PROPERTIES_OK,
      addOk(5, [
        {
          kind: "collision",
          severity: "warning",
          term: "Zovirax",
          business_key: "NPTC-000900",
          preferred_term: "Aciclovir",
        },
      ]),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await fillNote(user, "Add a trade name");
    await save(user);

    const done = await screen.findByRole("region", { name: "Changes saved" });
    expect(within(done).getByText(/“Zovirax” is also on NPTC-000900/)).toBeVisible();
  });
});

describe("one run across the form (FR-36, FR-38)", () => {
  it("sends term, code, synonyms and properties in order, each with the previous row version", async () => {
    const calls = stubApi(
      [
        entryRoute(),
        PROPERTIES_OK,
        PROCEDURES_OK,
        conceptRoute(),
        bindOk(6),
        retireOk(7),
        addOk(9),
        usageOk(10),
      ],
      {
        vary: (call, prior) =>
          call.method === "POST" && call.path.endsWith(AMEND_PATH)
            ? prior === 0
              ? amendOk("Serum ferritin", 5)
              : amendOk("Full count", 8)
            : null,
      },
    );
    const user = userEvent.setup();
    await renderLoaded();

    const term = form().getByLabelText("RCPA Preferred");
    await user.clear(term);
    await user.paste("Serum ferritin");
    await pickProcedure(user);
    const synonym = form().getByLabelText("Synonym 2");
    await user.clear(synonym);
    await user.paste("Full count");
    await user.click(form().getByRole("button", { name: "Remove FBC" }));
    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Correct everything together");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(
      writes(calls).map((call) => [
        call.method,
        call.path.split("/api/v1")[1],
        (call.body as { expected_row_version: number }).expected_row_version,
      ]),
    ).toEqual([
      ["POST", AMEND_PATH, 4],
      ["POST", BIND_PATH, 5],
      ["POST", RETIRE_PATH, 6],
      ["POST", AMEND_PATH, 7],
      ["POST", ADD_PATH, 8],
      ["PUT", USAGE_PATH, 9],
    ]);
  });

  it("stops at a stale version and leaves every unsent field on screen", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
      {
        method: "POST",
        path: BIND_PATH,
        status: 409,
        body: {
          detail: "This entry changed after you opened it.",
          business_key: BUSINESS_KEY,
          expected_row_version: 4,
          current_row_version: 9,
          conflicts: [],
          changed_by: "another.editor",
          changed_at: "2026-09-01T05:00:00Z",
        },
      },
      addOk(5),
      usageOk(5),
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await pickProcedure(user);
    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await user.type(form().getByLabelText("Usage guidance"), "Fasting sample.");
    await fillNote(user, "Everything at once");
    await save(user);

    await screen.findByRole("heading", { name: "Some changes were not saved" });
    expect(writes(calls).map((call) => call.path.split("/api/v1")[1])).toEqual([
      BIND_PATH,
    ]);
    expect(
      summary().getAllByText(/Not sent, because an earlier field failed/),
    ).toHaveLength(2);
    expect(form().getByLabelText("Add synonyms")).toHaveValue("Zovirax");
    expect(form().getByLabelText("Usage guidance")).toHaveValue("Fasting sample.");
    expect(form().getByText(FSN)).toBeInTheDocument();
  });

  it("does not send a saved synonym change again when Save is pressed before the entry reloads", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, addOk(5), usageOk(6)]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await fillNote(user, "Add a trade name");
    await save(user);
    await form().findByRole("heading", { name: "Changes saved" });

    expect(form().getByLabelText("Add synonyms")).toHaveValue("");
    expect(writes(calls).filter((call) => call.path.endsWith(ADD_PATH))).toHaveLength(1);
  });

  it("has no accessibility violations with a code chosen and the synonyms showing", async () => {
    stubApi([entryRoute(ENTRY_WITH_CODE), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const user = userEvent.setup();
    const { container } = await renderLoaded();
    await pickProcedure(user, "Replacement SNOMED CT code");
    await user.click(form().getByRole("button", { name: "Remove FBC" }));

    await waitFor(() => expect(form().getByText(FSN)).toBeInTheDocument());
    await expectNoA11yViolations(container);
  });
});

function inDialog() {
  return within(screen.getByRole("dialog"));
}

const COLLISION_WARNING = {
  kind: "collision",
  severity: "warning",
  term: "Zovirax",
  business_key: "NPTC-000900",
  preferred_term: "Aciclovir",
};

describe("reinstating a retired synonym (FR-04)", () => {
  it("lists a retired synonym with a Reinstate button, and not as a text box", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    await renderLoaded();

    expect(form().getByRole("heading", { name: "Retired synonyms" })).toBeVisible();
    expect(form().getByText("Old note")).toBeVisible();
    expect(form().getByRole("button", { name: "Reinstate Old note" })).toBeVisible();
  });

  it("posts the term with its reason and the entry's row version, then announces it", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "POST",
        path: REINSTATE_PATH,
        status: 200,
        body: {
          designation: { term: "Old note", status: "active", length: 8 },
          warnings: [],
          row_version: 5,
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Reinstate Old note" }));
    await user.type(inDialog().getByLabelText("Changelog note"), "Still in use");
    await user.click(inDialog().getByRole("button", { name: "Reinstate term" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(bodyOf(calls, REINSTATE_PATH)).toEqual({
      term: "Old note",
      reason: "Still in use",
      expected_row_version: 4,
    });
    expect(
      screen
        .getAllByRole("status")
        .some((region) => /Term reinstated/.test(region.textContent ?? "")),
    ).toBe(true);
  });

  it("refuses without a changelog note, and sends nothing", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Reinstate Old note" }));
    await user.click(inDialog().getByRole("button", { name: "Reinstate term" }));

    expect(
      inDialog().getAllByText(/A changelog note is required\./).length,
    ).toBeGreaterThan(0);
    expect(writes(calls)).toHaveLength(0);
  });

  it("shows the server's own sentence when it refuses, and keeps the dialog open", async () => {
    stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "POST",
        path: REINSTATE_PATH,
        status: 409,
        body: { detail: "An active synonym with this term already exists." },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Reinstate Old note" }));
    await user.type(inDialog().getByLabelText("Changelog note"), "Still in use");
    await user.click(inDialog().getByRole("button", { name: "Reinstate term" }));

    expect(
      await inDialog().findByText("An active synonym with this term already exists."),
    ).toBeVisible();
  });
});

describe("acknowledging a duplicate (FR-05)", () => {
  async function addZovirax(user: User) {
    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await fillNote(user, "Add a trade name");
    await save(user);
    await screen.findByRole("region", { name: "Changes saved" });
  }

  it("posts the term with its reason, then stops listing the warning", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      addOk(5, [COLLISION_WARNING]),
      {
        method: "POST",
        path: ACK_PATH,
        status: 201,
        body: { term: "Zovirax", created: true },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await addZovirax(user);

    await user.click(screen.getByRole("button", { name: "Acknowledge Zovirax" }));
    await user.type(inDialog().getByLabelText("Changelog note"), "Two entries share it");
    await user.click(inDialog().getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(bodyOf(calls, ACK_PATH)).toEqual({
      term: "Zovirax",
      reason: "Two entries share it",
    });
    expect(
      screen.queryByRole("button", { name: "Acknowledge Zovirax" }),
    ).not.toBeInTheDocument();
  });

  it("refuses without a changelog note, and sends nothing to acknowledge", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, addOk(5, [COLLISION_WARNING])]);
    const user = userEvent.setup();
    await renderLoaded();
    await addZovirax(user);

    await user.click(screen.getByRole("button", { name: "Acknowledge Zovirax" }));
    await user.click(inDialog().getByRole("button", { name: "Acknowledge" }));

    expect(
      inDialog().getAllByText(/A changelog note is required\./).length,
    ).toBeGreaterThan(0);
    expect(calls.some((call) => call.path.endsWith(ACK_PATH))).toBe(false);
  });
});

describe("retiring the code with no replacement (FR-08)", () => {
  const RETIRE_CODE_PATH = `${BIND_PATH}/${OLD_CODE}/retirement`;
  const RETIRE_BUTTON = `Retire ${OLD_CODE} without a replacement`;

  it("posts the reason and the row version, addressed by the active code", async () => {
    const calls = stubApi([
      entryRoute(ENTRY_WITH_CODE),
      PROPERTIES_OK,
      {
        method: "POST",
        path: RETIRE_CODE_PATH,
        status: 200,
        body: {
          binding: { ...ACTIVE_BINDING, status: "retired" },
          row_version: 5,
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: RETIRE_BUTTON }));
    await user.type(inDialog().getByLabelText("Changelog note"), "No longer a procedure");
    await user.click(inDialog().getByRole("button", { name: "Retire code" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(bodyOf(calls, RETIRE_CODE_PATH)).toEqual({
      reason: "No longer a procedure",
      expected_row_version: 4,
    });
  });

  it("offers no retire button when the entry has no active code", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    await renderLoaded();

    expect(
      form().queryByRole("button", { name: /without a replacement/ }),
    ).not.toBeInTheDocument();
  });

  it("refuses without a changelog note, and sends nothing", async () => {
    const calls = stubApi([entryRoute(ENTRY_WITH_CODE), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: RETIRE_BUTTON }));
    await user.click(inDialog().getByRole("button", { name: "Retire code" }));

    expect(
      inDialog().getAllByText(/A changelog note is required\./).length,
    ).toBeGreaterThan(0);
    expect(writes(calls)).toHaveLength(0);
  });

  it("uses the row version the form's own save returned while the entry is still reloading", async () => {
    const calls = stubApi([
      entryRoute(ENTRY_WITH_CODE),
      PROPERTIES_OK,
      addOk(5),
      {
        method: "POST",
        path: RETIRE_CODE_PATH,
        status: 200,
        body: { binding: { ...ACTIVE_BINDING, status: "retired" }, row_version: 6 },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(form().getByLabelText("Add synonyms"));
    await user.paste("Zovirax");
    await fillNote(user, "Add a trade name");
    await save(user);
    await screen.findByRole("region", { name: "Changes saved" });

    await user.click(form().getByRole("button", { name: RETIRE_BUTTON }));
    await user.type(inDialog().getByLabelText("Changelog note"), "No longer a procedure");
    await user.click(inDialog().getByRole("button", { name: "Retire code" }));

    await waitFor(() =>
      expect(bodyOf(calls, RETIRE_CODE_PATH)).toMatchObject({ expected_row_version: 5 }),
    );
  });

  it("has no accessibility violations with the dialog open", async () => {
    stubApi([entryRoute(ENTRY_WITH_CODE), PROPERTIES_OK]);
    const user = userEvent.setup();
    const { container } = await renderLoaded();

    await user.click(form().getByRole("button", { name: RETIRE_BUTTON }));

    await expectNoA11yViolations(container);
  });
});

describe("choosing a code by keyboard, and the same code twice", () => {
  it("chooses the first result on Enter, and does not submit the form", async () => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 });
    await user.keyboard("{Enter}");

    expect(await form().findByText(FSN, {}, { timeout: 2000 })).toBeVisible();
    expect(writes(calls)).toHaveLength(0);
  });

  it("says so when the chosen code is the one the entry already has", async () => {
    stubApi([
      entryRoute({ ...ENTRY, bindings: [{ ...ACTIVE_BINDING, code: CODE, fsn: FSN }] }),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("Replacement SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );

    expect(
      (await form().findAllByText("That code is already bound to this entry.")).length,
    ).toBeGreaterThan(0);
    expect(form().queryByText("Chosen code")).not.toBeInTheDocument();
  });

  it("limits a search to the length the server accepts", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    await renderLoaded();

    expect(form().getByLabelText("SNOMED CT code")).toHaveAttribute("maxlength", "200");
  });
});

describe("a reinstated synonym that duplicates another entry's (FR-05)", () => {
  it("lists the warning with an Acknowledge button", async () => {
    stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "POST",
        path: REINSTATE_PATH,
        status: 200,
        body: {
          designation: { term: "Old note", status: "active", length: 8 },
          warnings: [{ ...COLLISION_WARNING, term: "Old note" }],
          row_version: 5,
        },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Reinstate Old note" }));
    await user.type(inDialog().getByLabelText("Changelog note"), "Still in use");
    await user.click(inDialog().getByRole("button", { name: "Reinstate term" }));

    expect(
      await screen.findByRole("button", { name: "Acknowledge Old note" }),
    ).toBeVisible();
  });
});

describe("choosing with the arrow keys", () => {
  it("chooses the highlighted result once, and does not submit the form", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        ...PROCEDURES_OK,
        body: {
          items: [
            { code: CODE, au_preferred_term: AU_PT, label_provenance: {} },
            { code: "122192001", au_preferred_term: "Other", label_provenance: {} },
          ],
          total: 2,
        },
      },
      conceptRoute(),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 });
    await user.keyboard("{ArrowDown}{Enter}");

    expect(await form().findByText(FSN, {}, { timeout: 2000 })).toBeVisible();
    expect(
      calls.filter((call) => call.path.includes("/terminology/concepts/")),
    ).toHaveLength(1);
    expect(writes(calls)).toHaveLength(0);
  });
});

describe("what counts as a change to the synonyms", () => {
  it.each([
    ["a trailing space", "FBC "],
    ["a non-breaking space", "FBC "],
  ])("does not enable Save for %s alone, and sends nothing", async (_name, text) => {
    const calls = stubApi([entryRoute(), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();

    const box = form().getByLabelText("Synonym 1");
    await user.clear(box);
    await user.paste(text);
    await fillNote(user, "Only the spacing changed");
    await save(user);

    expect(form().getByRole("button", { name: "Save" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(writes(calls)).toHaveLength(0);
    expect(
      screen.queryByRole("region", { name: "Changes saved" }),
    ).not.toBeInTheDocument();
  });
});

describe("renaming a synonym to a term that is being removed", () => {
  it("retires first, so the rename is not refused as a duplicate", async () => {
    const calls = stubApi([
      entryRoute(),
      PROPERTIES_OK,
      retireOk(5),
      amendOk("Complete blood count", 6),
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(form().getByRole("button", { name: "Remove Complete blood count" }));
    const box = form().getByLabelText("Synonym 1");
    await user.clear(box);
    await user.paste("Complete blood count");
    await fillNote(user, "Replace the abbreviation");
    await save(user);

    await form().findByRole("heading", { name: "Changes saved" });
    expect(
      writes(calls).map((call) => [
        call.path.split("/api/v1")[1],
        (call.body as { expected_row_version: number }).expected_row_version,
      ]),
    ).toEqual([
      [RETIRE_PATH, 4],
      [AMEND_PATH, 5],
    ]);
    expect(bodyOf(calls, RETIRE_PATH)).toMatchObject({ term: "Complete blood count" });
    expect(bodyOf(calls, AMEND_PATH)).toMatchObject({
      term: "FBC",
      new_term: "Complete blood count",
      target: "synonym",
    });
  });
});

describe("a field's error stays until that field changes", () => {
  async function blankTermAndSave(user: User) {
    await user.clear(form().getByLabelText("RCPA Preferred"));
    await fillNote(user, "Clear the term by mistake");
    await save(user);
    expect(await form().findAllByText("Enter the preferred term.")).not.toHaveLength(0);
  }

  it("keeps the preferred term's error while the editor types in a synonym", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();
    await blankTermAndSave(user);

    await user.type(form().getByLabelText("Synonym 1"), "x");
    await user.type(form().getByLabelText("Add synonyms"), "Zovirax");

    expect(form().getAllByText("Enter the preferred term.")).not.toHaveLength(0);
  });

  it("clears the preferred term's error once that field is typed in", async () => {
    stubApi([entryRoute(), PROPERTIES_OK]);
    const user = userEvent.setup();
    await renderLoaded();
    await blankTermAndSave(user);

    await user.type(form().getByLabelText("RCPA Preferred"), "Full blood count");

    expect(form().queryByText("Enter the preferred term.")).not.toBeInTheDocument();
  });

  it("keeps the other errors when the code picked is the one already bound", async () => {
    stubApi([
      entryRoute({ ...ENTRY, bindings: [{ ...ACTIVE_BINDING, code: CODE, fsn: FSN }] }),
      PROPERTIES_OK,
      PROCEDURES_OK,
      conceptRoute(),
    ]);
    const user = userEvent.setup();
    await renderLoaded();
    await blankTermAndSave(user);

    await user.type(form().getByLabelText("Replacement SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );

    expect(
      (await form().findAllByText("That code is already bound to this entry.")).length,
    ).toBeGreaterThan(0);
    expect(form().getAllByText("Enter the preferred term.")).not.toHaveLength(0);
  });

  it("keeps another synonym's refusal while a different row is edited", async () => {
    stubApi([
      entryRoute(),
      PROPERTIES_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: { detail: "This entry already holds that synonym." },
      },
    ]);
    const user = userEvent.setup();
    await renderLoaded();

    const first = form().getByLabelText("Synonym 1");
    await user.clear(first);
    await user.paste("Full count");
    await fillNote(user, "Reword the first synonym");
    await save(user);
    await screen.findByRole("heading", { name: "Some changes were not saved" });

    await user.type(form().getByLabelText("Synonym 2"), "x");

    expect(
      form().getAllByText("This entry already holds that synonym.").length,
    ).toBeGreaterThan(0);
  });
});

describe("a code error that describes the lookup", () => {
  /** Holds the concept lookup until `release` is called, so a code stays "checking". */
  function holdConceptLookup() {
    const send = fetch;
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", async (request: Request) => {
      if (new URL(request.url).pathname.endsWith(`/terminology/concepts/${CODE}`)) {
        await gate;
      }
      return send(request);
    });
    return release;
  }

  it("goes once the lookup finishes, with no second Save", async () => {
    stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK, conceptRoute()]);
    const release = holdConceptLookup();
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );
    await form().findByText(/Checking .* against the terminology server/);
    await fillNote(user, "Bind while the code is still checking");
    await save(user);
    expect(
      (await form().findAllByText("Wait for the code to finish checking before saving."))
        .length,
    ).toBeGreaterThan(0);

    release();

    expect(await form().findByText(FSN, {}, { timeout: 2000 })).toBeVisible();
    await waitFor(() =>
      expect(
        form().queryByText("Wait for the code to finish checking before saving."),
      ).not.toBeInTheDocument(),
    );
  });

  it("goes when a retry of the same code succeeds", async () => {
    stubApi([entryRoute(), PROPERTIES_OK, PROCEDURES_OK], {
      vary: (call, prior) =>
        call.path.endsWith(`/terminology/concepts/${CODE}`)
          ? prior === 0
            ? {
                method: "GET",
                path: call.path,
                status: 503,
                body: { detail: "The terminology server is not reachable right now." },
              }
            : conceptRoute()
          : null,
    });
    const user = userEvent.setup();
    await renderLoaded();

    await user.type(form().getByLabelText("SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );
    await form().findByText(/not reachable right now/, {}, { timeout: 2000 });
    await fillNote(user, "Bind a code the server could not check");
    await save(user);
    expect(
      (await form().findAllByText(/not reachable right now/)).length,
    ).toBeGreaterThan(1);

    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));

    expect(await form().findByText(FSN, {}, { timeout: 2000 })).toBeVisible();
    await waitFor(() =>
      expect(form().queryByText(/not reachable right now/)).not.toBeInTheDocument(),
    );
  });
});
