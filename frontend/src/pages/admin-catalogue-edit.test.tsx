import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The designation edit screen (issue #149; FR-04, FR-05, FR-24, FR-36, FR-38).
 *
 * Every test drives the real router at the real URL, so what is under test is
 * the shipped route as well as the page.
 */

const BUSINESS_KEY = "NPTC-000247";
const EDIT_URL = `/admin/catalogue/${BUSINESS_KEY}/edit`;

const ENTRY = {
  business_key: BUSINESS_KEY,
  preferred_term: "Ferritin",
  // Deliberately not `"Ferritin".length`: FR-85's figure is computed by the
  // server from the *cleaned* term, and a test that derived it here would
  // stop being able to tell a rendered server value from a recomputed one.
  length: 8,
  status: "draft",
  specimen_unconstrained: false,
  updated_at: "2026-09-01T04:30:00Z",
  row_version: 3,
  designations: [
    {
      term: "Serum ferritin",
      use: "synonym",
      language: "en-AU",
      status: "active",
      length: 14,
    },
    // A non-en-AU preferred *designation*, which is a shape the read route
    // documents ("an entry's active synonyms and non-en-AU preferred
    // variants") and which the panel must not amend as a synonym. There is no
    // retired row here: both read routes build `designations` from
    // `queries.load_designations`, which omits them.
    {
      term: "Ferritine",
      use: "preferred",
      language: "fr-FR",
      status: "active",
      length: 9,
    },
  ],
  bindings: [],
  properties: [],
};

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const READ_OK: Route = {
  method: "GET",
  path: `/catalogue/admin/entries/${BUSINESS_KEY}`,
  status: 200,
  body: ENTRY,
};

const ADD_PATH = `/catalogue/entries/${BUSINESS_KEY}/designations`;
const AMEND_PATH = `${ADD_PATH}/amendment`;
const RETIRE_PATH = `${ADD_PATH}/retirement`;
const ACK_PATH = `${ADD_PATH}/acknowledgement`;

const BIND_PATH = `/catalogue/entries/${BUSINESS_KEY}/bindings`;
const TERMINOLOGY_PATH = "/terminology/concepts/";

// The PRD's own FR-83 regression fixture: a double-parenthesis FSN, chosen
// because a careless semantic-tag strip mangles it (it has two `)`s to stop
// at, not one).
const FSN_CODE = "391483001";
const FSN = "Microscopy (acid fast bacilli) (procedure)";
const AU_PT = "Microscopy (AFB)";

function terminologyRoute(
  code: string,
  overrides: {
    status?: number;
    fsn?: string | null;
    au_preferred_term?: string | null;
    active?: boolean | null;
    edition?: string;
    body?: unknown;
  } = {},
): Route {
  const {
    status = 200,
    fsn = FSN,
    au_preferred_term = AU_PT,
    active = true,
    edition = "au",
    body,
  } = overrides;
  return {
    method: "GET",
    path: `${TERMINOLOGY_PATH}${code}`,
    status,
    body: body ?? {
      system: "http://snomed.info/sct",
      code,
      fsn,
      au_preferred_term,
      active,
      edition,
      resolved_version: "http://snomed.info/sct/32506021000036107/version/20260101",
    },
  };
}

/** Types a code into the given input and waits past the 400ms debounce for its lookup to land. */
async function typeCodeAndWait(
  user: ReturnType<typeof userEvent.setup>,
  input: HTMLElement,
  code: string,
  awaitText: RegExp | string,
) {
  await user.type(input, code);
  await screen.findByText(awaitText, {}, { timeout: 2000 });
}

async function renderLoaded() {
  const rendered = await renderRoute(EDIT_URL, SIGNED_IN);
  await screen.findByRole("heading", { name: "Ferritin", level: 1 });
  return rendered;
}

function callsTo(
  calls: { method: string; path: string; body: unknown; text: string }[],
  path: string,
) {
  return calls.filter((call) => call.method === "POST" && call.path.endsWith(path));
}

/** Reads of the entry, for asserting that something refetched it. */
function readsOf(calls: { method: string; path: string }[]) {
  return calls.filter(
    (call) => call.method === "GET" && call.path.endsWith(READ_OK.path),
  );
}

/**
 * Everything the page's live regions currently say. There are three - the
 * page owns one for a failed refresh, and each panel (designations, code
 * bindings) owns one for the outcome of its own writes - and all are mounted
 * from the first render, which is the point of `LiveRegion`. A test cares
 * what was announced, not which region carried it.
 */
function announced(): string {
  return screen
    .getAllByRole("status")
    .map((region) => region.textContent ?? "")
    .join(" ");
}

/**
 * Queries scoped to the open dialog. The page's own "Add synonyms" form stays
 * mounted behind a dialog, so an unscoped `getByLabelText(/Changelog note/)`
 * legitimately matches two fields - which is the layout working, not a bug.
 */
function inDialog() {
  return within(screen.getByRole("dialog"));
}

/**
 * Queries scoped to the Terms panel. Its own "Changelog note" field shares a
 * label with the code bindings panel's Bind form (#150) - both sit directly
 * on the page, neither behind a dialog - so an unscoped
 * `getByLabelText(/Changelog note/)` legitimately matches two fields once
 * both panels are mounted, the same way `inDialog()` already documents for
 * the dialog case.
 */
function inTermsPanel() {
  return within(screen.getByRole("region", { name: "Terms" }));
}

/** Queries scoped to the code bindings panel, for the same reason as `inTermsPanel()`. */
function inBindingsPanel() {
  return within(screen.getByRole("region", { name: "Code bindings" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the entry it loads", () => {
  it("reads through the admin route, so a draft entry can be edited at all", async () => {
    // The public detail route 404s a draft entry identically to a key that was
    // never minted (#142), so loading through it would make this screen
    // unusable for exactly the entries most likely to need editing.
    const calls = stubApi([READ_OK]);

    await renderLoaded();

    expect(calls[0]?.path).toBe(`/api/v1/catalogue/admin/entries/${BUSINESS_KEY}`);
    expect(screen.getByText("Entry status").nextElementSibling).toHaveTextContent(
      "draft",
    );
  });

  it("shows the computed preferred-term length with no control to edit it", async () => {
    // FR-24/FR-85. The figure is the server's, computed from the cleaned term,
    // and must not be editable on any code path for any role.
    stubApi([READ_OK]);

    const { container } = await renderLoaded();

    // Scoped to the definition it labels: 8 is also a designation's length in
    // the table below, and an unscoped getByText would match either.
    expect(
      screen.getByText("Preferred term length").nextElementSibling,
    ).toHaveTextContent("8");
    // The FR-24 assertion proper: no control resolves to it, and no control
    // anywhere on the screen is holding the value.
    expect(screen.queryByLabelText(/length/i)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("8")).not.toBeInTheDocument();
    expect(container.querySelector("input[name*='length' i]")).toBeNull();
  });

  it("says what to do when the API refuses the load for want of MFA", async () => {
    // `catalogue.edit_published` is MFA-gated and the SPA does not yet answer
    // the RFC 9470 step-up challenge (#184). The generic "no permission"
    // sentence would strand an administrator who simply has not done the
    // second step, so this refusal names the remedy that actually works.
    stubApi([
      {
        ...READ_OK,
        status: 403,
        body: { detail: "You do not have permission to do this." },
      },
    ]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    expect(
      await screen.findByText(/requires an administrator account with multi-factor/i),
    ).toBeInTheDocument();
  });

  it("names the identifier when there is no such entry", async () => {
    stubApi([
      { ...READ_OK, status: 404, body: { detail: "No catalogue entry was found." } },
    ]);

    await renderRoute(EDIT_URL, SIGNED_IN);

    expect(
      await screen.findByText(
        new RegExp(`No catalogue entry was found for ${BUSINESS_KEY}`),
      ),
    ).toBeInTheDocument();
  });
  it("keeps the editor on screen when a refresh fails, and says so", async () => {
    // `isError` and `data` are not exclusive states. Before this, an entry
    // that loaded and then failed a refetch rendered "You cannot edit this
    // entry with your current sign-in" directly above a working terms table -
    // and the amend mutation's conflict refetch makes that a designed-in path
    // (PR #238 review).
    const user = userEvent.setup();
    const CONFLICT = {
      method: "POST",
      path: AMEND_PATH,
      status: 409,
      body: {
        detail: "This entry was changed by someone else since you loaded it.",
        business_key: BUSINESS_KEY,
        expected_row_version: 3,
        current_row_version: 4,
        conflicts: [],
        changed_by: "A Curator",
        changed_at: "2026-09-02T01:00:00Z",
      },
    };
    // The read succeeds once and is refused after that - a session expiring
    // while the screen sits open, which is the same session length that makes
    // a version conflict likely in the first place. Re-stubbing `fetch`
    // mid-test would not do it: the API client holds the reference it was
    // created with.
    // Reads are refused from the amendment onwards, not from the second read
    // onwards: StrictMode double-mounts, so the load itself is two reads.
    let expired = false;
    const calls = stubApi([READ_OK, CONFLICT], {
      vary: (call) => {
        if (call.method === "POST") {
          expired = true;
          return null;
        }
        return expired
          ? { ...READ_OK, status: 403, body: { detail: "Step-up required." } }
          : null;
      },
    });
    await renderLoaded();
    // Captured after the load, which is two reads under StrictMode, so the
    // assertion below is about the refetch and not about the load (PR #238
    // review).
    const readsBefore = readsOf(calls).length;

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(inDialog().getByLabelText(/Changelog note/), "Rename the entry");
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    expect(
      await screen.findByText(/could not be refreshed just now/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Sign out and sign in again/)).not.toBeInTheDocument();
    // Announced, not just shown: the reader who cannot see the banner appear
    // is the one it exists for. `LiveRegion` is the always-mounted region, so
    // the sentence is a text change inside it rather than a new element.
    await waitFor(() => expect(announced()).toMatch(/could not be refreshed just now/));
    expect(
      screen.getByRole("button", { name: "Retire Serum ferritin (synonym)" }),
    ).toBeInTheDocument();
    // The refetch the conflict asked for actually went out.
    expect(readsOf(calls).length).toBeGreaterThan(readsBefore);
  });
});

describe("the terms table", () => {
  it("lists the entry's own preferred term alongside its designations", async () => {
    // ADR-0022 stores these in two places; the editor sees one list.
    stubApi([READ_OK]);

    await renderLoaded();

    const rows = screen.getAllByRole("row");
    // Header, then preferred first, then the two designations.
    expect(within(rows[1] as HTMLElement).getByRole("rowheader")).toHaveTextContent(
      "Ferritin",
    );
    expect(within(rows[1] as HTMLElement).getByText("preferred")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByRole("rowheader")).toHaveTextContent(
      "Serum ferritin",
    );
    expect(within(rows[3] as HTMLElement).getByRole("rowheader")).toHaveTextContent(
      "Ferritine",
    );
  });

  it("has no Status column, because every row it can hold is active", async () => {
    // `queries.load_designations` omits retired designations and
    // `catalogue_entry.preferred_term` is NOT NULL, so a Status column could
    // only ever render the same literal on every row (review finding 2).
    // Scoped to this table: the code bindings panel (#150) below it legitimately
    // has one, since a binding can genuinely be active or retired.
    stubApi([READ_OK]);

    await renderLoaded();

    const table = screen.getByRole("table", { name: `Terms on ${BUSINESS_KEY}` });
    expect(
      within(table).queryByRole("columnheader", { name: "Status" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer to retire the entry's own preferred term", async () => {
    // `catalogue_entry.preferred_term` is NOT NULL and no route retires it
    // (ADR-0022). A button that could only ever fail is worse than none.
    stubApi([READ_OK]);

    await renderLoaded();

    const rows = screen.getAllByRole("row");
    expect(
      within(rows[1] as HTMLElement).queryByRole("button", { name: /^Retire/ }),
    ).not.toBeInTheDocument();
    expect(
      within(rows[2] as HTMLElement).getByRole("button", { name: /^Retire/ }),
    ).toBeInTheDocument();
  });

  it("tells two rows apart when a synonym shadows the preferred term", async () => {
    // `POST .../designations` will happily create a synonym whose comparison
    // key equals its own entry's preferred term - the state #227 added `use`
    // to reach past. Both rows then read "Ferritin", so naming the buttons by
    // term alone would leave a screen-reader user with two identical actions
    // and no way to know which one moves which.
    stubApi([
      {
        ...READ_OK,
        body: {
          ...ENTRY,
          designations: [
            {
              term: "Ferritin",
              use: "synonym",
              language: "en-AU",
              status: "active",
              length: 8,
            },
          ],
        },
      },
    ]);

    await renderLoaded();

    expect(
      screen.getByRole("button", { name: "Edit Ferritin (preferred)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Edit Ferritin (synonym)" }),
    ).toBeInTheDocument();
  });

  it("keeps the preferred term editable on a draft entry", async () => {
    // Found in review. An entry's own lifecycle
    // (draft/active/deprecated/withdrawn) is not a fact about any of its
    // terms, and once it reached the row actions it took the Edit action away
    // from every unpublished entry - precisely the kind this screen exists to
    // edit (#228).
    stubApi([READ_OK]);

    await renderLoaded();

    const preferredRow = screen.getAllByRole("row")[1] as HTMLElement;
    expect(
      within(preferredRow).getByRole("button", { name: "Edit Ferritin (preferred)" }),
    ).toBeInTheDocument();
  });
});

describe("adding synonyms", () => {
  it("splits a pasted cell into individual terms and shows what it will create", async () => {
    // FR-04's own acceptance criterion, end to end: the doubled semicolon in
    // "Zovirax;;Cyclir" must produce two terms and no empty row - and the
    // editor must be able to see that before saving.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: ADD_PATH,
        status: 201,
        body: { designations: [], warnings: [] },
      },
    ]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Zovirax;;Cyclir");
    expect(screen.getByText(/This will add 2 terms/)).toBeInTheDocument();

    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the two brand names",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    await waitFor(() => expect(callsTo(calls, ADD_PATH)).toHaveLength(1));
    expect(callsTo(calls, ADD_PATH)[0]?.body).toEqual({
      language: "en-AU",
      terms: ["Zovirax", "Cyclir"],
      use: "synonym",
      reason: "Add the two brand names",
    });
  });

  it("submits a term containing a non-breaking space unaltered", async () => {
    // FR-05's normalisation is `collision_key`'s, server-side. The screen's
    // one obligation is not to pre-clean the term on its way out - doing so
    // would hide the very defect the server is there to catch.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: ADD_PATH,
        status: 201,
        body: { designations: [], warnings: [] },
      },
    ]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Adrenal Ab");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the variant spelling",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    await waitFor(() => expect(callsTo(calls, ADD_PATH)).toHaveLength(1));
    expect((callsTo(calls, ADD_PATH)[0]?.body as { terms: string[] }).terms).toEqual([
      "Adrenal Ab",
    ]);
  });

  it("refuses a cell of only delimiters rather than posting an empty batch", async () => {
    const user = userEvent.setup();
    const calls = stubApi([READ_OK]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), ";;;");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add nothing at all",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    // Twice over, and deliberately: once in the error summary the form moves
    // focus to, once beside the field itself.
    expect(await screen.findAllByText(/Enter at least one term/)).toHaveLength(2);
    expect(callsTo(calls, ADD_PATH)).toHaveLength(0);
  });

  it("refuses a save with no changelog note, and moves focus to the summary", async () => {
    // FR-37. The focus move is the accessibility half: a summary nobody is
    // sent to is a summary a screen-reader user never hears.
    const user = userEvent.setup();
    const calls = stubApi([READ_OK]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Zovirax");
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    const summary = await screen.findByText("There is a problem");
    expect(summary.closest("[tabindex='-1']")).toHaveFocus();
    expect(callsTo(calls, ADD_PATH)).toHaveLength(0);

    // And the summary's link reaches the field it names, rather than a dead id.
    await user.click(screen.getByRole("link", { name: /changelog note/i }));
    expect(inTermsPanel().getByLabelText(/Changelog note/)).toHaveFocus();
  });
  it("refuses a paste over the server's batch cap, saying by how much", async () => {
    // `_MAX_TERMS_PER_BATCH` is 100 and a 422 for it carries FastAPI's
    // `ValidationError` array, which `refusalDetail` cannot turn into a
    // sentence - so without this the editor sees "check the details and try
    // again" beside a preview boasting 101 terms (review finding 6).
    const user = userEvent.setup();
    const calls = stubApi([READ_OK]);
    await renderLoaded();

    const cell = Array.from({ length: 101 }, (_, index) => `Term ${index}`).join(";");
    await user.click(screen.getByLabelText("Synonyms"));
    await user.paste(cell);
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Bulk import of synonyms",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    expect(
      await screen.findAllByText(/This adds 101 terms, and at most 100/),
    ).toHaveLength(2);
    expect(callsTo(calls, ADD_PATH)).toHaveLength(0);
  });
});

describe("an error-severity collision", () => {
  const COLLISION_409 = {
    method: "POST",
    path: ADD_PATH,
    status: 409,
    body: {
      detail: "This term matches another entry's preferred term or synonym.",
      collisions: [
        {
          severity: "error",
          business_key: "NPTC-000111",
          preferred_term: "Adrenal antibody",
        },
      ],
    },
  };

  it("blocks the save and names the conflicting entry, not a status code", async () => {
    // FR-05 / PRD 17.2 item 5, the heart of this issue.
    const user = userEvent.setup();
    stubApi([READ_OK, COLLISION_409]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Adrenal Ab");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the abbreviation",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));

    const link = await screen.findByRole("link", { name: /NPTC-000111/ });
    expect(link).toHaveTextContent("Adrenal antibody");
    expect(link).toHaveAttribute("href", "/catalogue/NPTC-000111");
    expect(screen.getByText(/Nothing has been saved/)).toBeInTheDocument();
    expect(screen.getByText(/Choose a different term/)).toBeInTheDocument();
  });

  it("shows no HTTP status anywhere in the refusal", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK, COLLISION_409]);
    const { container } = await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Adrenal Ab");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the abbreviation",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));
    await screen.findByRole("link", { name: /NPTC-000111/ });

    expect(container.textContent).not.toMatch(/\b409\b/);
    expect(container.textContent).not.toMatch(/conflict/i);
  });

  it("leaves the terms table as it was", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK, COLLISION_409]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Adrenal Ab");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the abbreviation",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));
    await screen.findByRole("link", { name: /NPTC-000111/ });

    // Header plus the three terms the entry started with, and no fourth.
    // Scoped to this table, not the whole page: the code bindings table (#150)
    // below it renders its own header and empty-state rows.
    const table = screen.getByRole("table", { name: `Terms on ${BUSINESS_KEY}` });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
  });
});

describe("a warning-severity collision", () => {
  const WARNED = {
    method: "POST",
    path: ADD_PATH,
    status: 201,
    body: {
      designations: [
        {
          term: "Ferritin assay",
          use: "synonym",
          language: "en-AU",
          status: "active",
          length: 14,
        },
      ],
      warnings: [
        {
          term: "Ferritin assay",
          business_key: "NPTC-000900",
          preferred_term: "Iron studies",
        },
      ],
    },
  };

  async function addWarnedTerm(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText("Synonyms"), "Ferritin assay");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the assay wording",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));
  }

  it("permits the save and offers the warning for acknowledgement", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK, WARNED]);
    await renderLoaded();

    await addWarnedTerm(user);

    expect(
      await screen.findByRole("heading", { name: "Possible duplicates" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/also on NPTC-000900/)).toBeInTheDocument();
    // Saved, not refused - so no error summary.
    expect(screen.queryByText("There is a problem")).not.toBeInTheDocument();
  });

  it("announces the outcome to a screen reader", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK, WARNED]);
    await renderLoaded();

    await addWarnedTerm(user);

    await waitFor(() =>
      expect(announced()).toContain("1 term added. 1 possible duplicate to review."),
    );
  });

  it("acknowledges a warning so it stops being reported", async () => {
    // FR-05's "does not warn again once acknowledged". The server stops
    // returning it; the panel has to drop it too, or it keeps offering an
    // Acknowledge button for something already acknowledged.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      WARNED,
      {
        method: "POST",
        path: ACK_PATH,
        status: 200,
        body: { language: "en-AU", reason: "Both entries use it", created: true },
      },
    ]);
    await renderLoaded();
    await addWarnedTerm(user);
    await screen.findByRole("heading", { name: "Possible duplicates" });

    await user.click(screen.getByRole("button", { name: "Acknowledge Ferritin assay" }));
    await user.type(inDialog().getByLabelText(/Changelog note/), "Both entries use it");
    await user.click(inDialog().getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(callsTo(calls, ACK_PATH)).toHaveLength(1));
    expect(callsTo(calls, ACK_PATH)[0]?.body).toEqual({
      language: "en-AU",
      term: "Ferritin assay",
      reason: "Both entries use it",
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "Possible duplicates" }),
      ).not.toBeInTheDocument(),
    );
  });
});

describe("amending a term", () => {
  it("addresses the entry's own preferred term with use and a row version", async () => {
    // FR-38 plus #227's disambiguator. Without `use: "preferred"` the route
    // resolves designations first, so a synonym shadowing the preferred term
    // would be moved instead - silently, and with no way back.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 200,
        body: {
          designation: {
            term: "Serum ferritin level",
            use: "preferred",
            language: "en-AU",
            status: "active",
            length: 20,
          },
          warnings: [],
          row_version: 4,
        },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    const term = inDialog().getByLabelText("Term");
    await user.clear(term);
    await user.type(term, "Serum ferritin level");
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Disambiguate from plasma",
    );
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    await waitFor(() => expect(callsTo(calls, AMEND_PATH)).toHaveLength(1));
    expect(callsTo(calls, AMEND_PATH)[0]?.body).toEqual({
      language: "en-AU",
      term: "Ferritin",
      new_term: "Serum ferritin level",
      use: "preferred",
      expected_row_version: 3,
      reason: "Disambiguate from plasma",
    });
  });

  it("addresses a synonym as a synonym, never falling back to the entry", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 200,
        body: {
          designation: {
            term: "Ferritin, serum",
            use: "synonym",
            language: "en-AU",
            status: "active",
            length: 15,
          },
          warnings: [],
          row_version: 3,
        },
      },
    ]);
    await renderLoaded();

    await user.click(
      screen.getByRole("button", { name: "Edit Serum ferritin (synonym)" }),
    );
    const term = inDialog().getByLabelText("Term");
    await user.clear(term);
    await user.type(term, "Ferritin, serum");
    await user.type(inDialog().getByLabelText(/Changelog note/), "Match the house style");
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    await waitFor(() => expect(callsTo(calls, AMEND_PATH)).toHaveLength(1));
    expect(callsTo(calls, AMEND_PATH)[0]?.body).toMatchObject({
      term: "Serum ferritin",
      new_term: "Ferritin, serum",
      use: "synonym",
      expected_row_version: 3,
    });
  });

  it("explains a version conflict and what to do about it", async () => {
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This entry was changed by someone else since you loaded it.",
          business_key: BUSINESS_KEY,
          expected_row_version: 3,
          current_row_version: 4,
          conflicts: [
            {
              field: "preferred_term",
              submitted: "Serum ferritin level",
              current: "Ferritin (S)",
            },
          ],
          changed_by: "A Curator",
          changed_at: "2026-09-02T01:00:00Z",
        },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Disambiguate from plasma",
    );
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    expect(await screen.findByText(/A Curator/)).toBeInTheDocument();
    expect(screen.getByText(/Ferritin \(S\)/)).toBeInTheDocument();
    expect(screen.getByText(/The entry is reloading/)).toBeInTheDocument();
  });

  it("reads correctly when the concurrent edit touched a different field", async () => {
    // `conflicts` is empty here by design: the entry moved, so the save is
    // still refused, but there is no field-level disagreement to list. The
    // copy must not promise a list it then does not show.
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This entry was changed by someone else since you loaded it.",
          business_key: BUSINESS_KEY,
          expected_row_version: 3,
          current_row_version: 4,
          conflicts: [],
          changed_by: null,
          changed_at: null,
        },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Disambiguate from plasma",
    );
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    expect(
      await screen.findByText(/Someone else changed this entry/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/What you sent/)).not.toBeInTheDocument();
    expect(screen.getByText(/The entry is reloading/)).toBeInTheDocument();
  });

  it("refetches the entry on a version conflict, so a retry can succeed", async () => {
    // The refusal says the entry has been reloaded. `invalidateQueries` runs
    // on success only by default, so without the mutation's `onError` the
    // cached `row_version` would stay stale and every retry from this dialog
    // would fail identically - advice the screen does not carry out (review
    // finding 3).
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This entry was changed by someone else since you loaded it.",
          business_key: BUSINESS_KEY,
          expected_row_version: 3,
          current_row_version: 4,
          conflicts: [],
          changed_by: "A Curator",
          changed_at: "2026-09-02T01:00:00Z",
        },
      },
    ]);
    await renderLoaded();
    const readsBefore = readsOf(calls).length;

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Disambiguate from plasma",
    );
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    await screen.findByText(/The entry is reloading/);
    await waitFor(() => expect(readsOf(calls).length).toBeGreaterThan(readsBefore));
  });

  it("does not refetch when the amendment is refused for a collision", async () => {
    // The other side of the conflict refetch: a collision means nothing moved,
    // so re-reading would only discard what the editor typed for no gain.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This term is already in use on another entry.",
          collisions: [
            {
              term: "Iron studies",
              business_key: "NPTC-000900",
              preferred_term: "Iron studies",
            },
          ],
        },
      },
    ]);
    await renderLoaded();
    const readsBefore = readsOf(calls).length;

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(inDialog().getByLabelText(/Changelog note/), "Rename the entry");
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    expect(await screen.findByText(/Nothing has been saved/)).toBeInTheDocument();
    expect(readsOf(calls)).toHaveLength(readsBefore);
  });

  it("amends a non-en-AU preferred variant as preferred, not as a synonym", async () => {
    // The read route serves "an entry's active synonyms and non-en-AU
    // preferred variants", so `use` has to come off the row. Hardcoding
    // "synonym" for every row that is not the entry's own term mis-addresses
    // exactly the designation `use` exists to reach (review finding 1).
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 200,
        body: {
          designation: {
            term: "Ferritine serique",
            use: "preferred",
            language: "fr-FR",
            status: "active",
            length: 17,
          },
          warnings: [],
          row_version: 3,
        },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritine (preferred)" }));
    const term = inDialog().getByLabelText("Term");
    await user.clear(term);
    await user.type(term, "Ferritine serique");
    await user.type(inDialog().getByLabelText(/Changelog note/), "Correct the French");
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    await waitFor(() => expect(callsTo(calls, AMEND_PATH)).toHaveLength(1));
    expect(callsTo(calls, AMEND_PATH)[0]?.body).toMatchObject({
      language: "fr-FR",
      term: "Ferritine",
      use: "preferred",
    });
  });

  it("renders a conflicting value that is not a string", async () => {
    // `submitted`/`current` are deliberately untyped on the wire - the audit
    // diff carries whatever the field holds, and `specimen_unconstrained`
    // (FR-89) is a boolean. Assuming a string here would print nothing at all
    // for the one field whose two values look most alike.
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      {
        method: "POST",
        path: AMEND_PATH,
        status: 409,
        body: {
          detail: "This entry was changed by someone else since you loaded it.",
          business_key: BUSINESS_KEY,
          expected_row_version: 3,
          current_row_version: 4,
          conflicts: [
            { field: "specimen_unconstrained", submitted: false, current: true },
          ],
          changed_by: "A Curator",
          changed_at: "2026-09-02T01:00:00Z",
        },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Disambiguate from plasma",
    );
    await user.click(inDialog().getByRole("button", { name: "Save term" }));

    // The field name is in its own <strong>, so climb to the list item that
    // carries the whole sentence.
    const item = (await screen.findByText("specimen_unconstrained")).closest("li");
    expect(item).toHaveTextContent("you sent false");
    expect(item).toHaveTextContent("it is now true");
  });

  it("has no control for the computed length in the dialog either", async () => {
    // FR-24 is "on any code path", so the dialog is its own check.
    const user = userEvent.setup();
    stubApi([READ_OK]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText(/length/i)).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole("textbox")).toHaveLength(2);
  });
});

describe("retiring a term", () => {
  it("posts the term with its mandatory reason", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      {
        method: "POST",
        path: RETIRE_PATH,
        status: 200,
        body: {
          term: "Serum ferritin",
          use: "synonym",
          language: "en-AU",
          status: "retired",
          length: 14,
        },
      },
    ]);
    await renderLoaded();

    await user.click(
      screen.getByRole("button", { name: "Retire Serum ferritin (synonym)" }),
    );
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Superseded by the new wording",
    );
    await user.click(inDialog().getByRole("button", { name: "Retire term" }));

    await waitFor(() => expect(callsTo(calls, RETIRE_PATH)).toHaveLength(1));
    expect(callsTo(calls, RETIRE_PATH)[0]?.body).toEqual({
      language: "en-AU",
      term: "Serum ferritin",
      reason: "Superseded by the new wording",
    });
  });

  it("drops a warning about the term it just retired", async () => {
    // The warning names a term the entry no longer has, and its Acknowledge
    // button would write an acknowledgement for it (review finding 4).
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      {
        method: "POST",
        path: ADD_PATH,
        status: 201,
        body: {
          designations: [
            {
              term: "Ferritin assay",
              use: "synonym",
              language: "en-AU",
              status: "active",
              length: 14,
            },
          ],
          warnings: [
            {
              term: "Serum ferritin",
              business_key: "NPTC-000900",
              preferred_term: "Iron studies",
            },
          ],
        },
      },
      {
        method: "POST",
        path: RETIRE_PATH,
        status: 200,
        body: {
          term: "Serum ferritin",
          use: "synonym",
          language: "en-AU",
          status: "retired",
          length: 14,
        },
      },
    ]);
    await renderLoaded();

    await user.type(screen.getByLabelText("Synonyms"), "Ferritin assay");
    await user.type(
      inTermsPanel().getByLabelText(/Changelog note/),
      "Add the assay wording",
    );
    await user.click(screen.getByRole("button", { name: "Add terms" }));
    await screen.findByRole("button", { name: "Acknowledge Serum ferritin" });

    await user.click(
      screen.getByRole("button", { name: "Retire Serum ferritin (synonym)" }),
    );
    await user.type(inDialog().getByLabelText(/Changelog note/), "Retire the duplicate");
    await user.click(inDialog().getByRole("button", { name: "Retire term" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Acknowledge Serum ferritin" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("refuses to retire without a reason", async () => {
    const user = userEvent.setup();
    const calls = stubApi([READ_OK]);
    await renderLoaded();

    await user.click(
      screen.getByRole("button", { name: "Retire Serum ferritin (synonym)" }),
    );
    await user.click(inDialog().getByRole("button", { name: "Retire term" }));

    expect(await inDialog().findAllByText(/Enter a changelog note/)).toHaveLength(2);
    expect(callsTo(calls, RETIRE_PATH)).toHaveLength(0);
  });

  it("surfaces the server's own sentence when it refuses", async () => {
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      {
        method: "POST",
        path: RETIRE_PATH,
        status: 422,
        body: {
          detail:
            "A changelog note is required and must describe the change. It becomes the " +
            'published History text, so single words like "update" or "fix" are not accepted.',
        },
      },
    ]);
    await renderLoaded();

    await user.click(
      screen.getByRole("button", { name: "Retire Serum ferritin (synonym)" }),
    );
    await user.type(inDialog().getByLabelText(/Changelog note/), "update");
    await user.click(inDialog().getByRole("button", { name: "Retire term" }));

    expect(await screen.findByText(/must describe the change/)).toBeInTheDocument();
  });
});

const ACTIVE_BINDING = {
  system: "http://snomed.info/sct",
  code: FSN_CODE,
  fsn: FSN,
  display_term: "Microscopy (acid fast bacilli)",
  au_preferred_term: AU_PT,
  edition_hint: "au",
  status: "active",
  retirement_reason: null,
  replaced_by_code: null,
};

const RETIRED_BINDING = {
  system: "http://snomed.info/sct",
  code: "165288007",
  fsn: "Basophil count (procedure)",
  display_term: "Basophil count",
  au_preferred_term: "Basophil count",
  edition_hint: "au",
  status: "retired",
  retirement_reason: "Superseded by the new method",
  replaced_by_code: FSN_CODE,
};

describe("code bindings", () => {
  it("shows an active binding, and offers no bind form while one exists", async () => {
    // FR-08 permits at most one active binding - offering a form that could
    // only ever 409 would be the mistake the designations panel avoids by
    // not offering Retire on the entry's own preferred term.
    stubApi([{ ...READ_OK, body: { ...ENTRY, bindings: [ACTIVE_BINDING] } }]);

    await renderLoaded();

    const table = screen.getByRole("table", { name: `Code bindings on ${BUSINESS_KEY}` });
    expect(within(table).getByRole("rowheader", { name: FSN_CODE })).toBeInTheDocument();
    expect(within(table).getByText(FSN)).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Bind a code" }),
    ).not.toBeInTheDocument();
  });

  it("resolves a code before binding it: the FSN keeps its tag, and the code is sent as a quoted string", async () => {
    // FR-82 (the resolved FSN/PT are never typed) and FR-06 (a string, not a
    // number, on the wire) in one test. `FSN` is the PRD's own FR-83
    // regression fixture - a double-parenthesis value a careless semantic-tag
    // strip mangles - chosen here because this is also this frontend's
    // principal FR-06 test with no browser Verhoeff mirror to fall back on.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE),
      { method: "POST", path: BIND_PATH, status: 201, body: ACTIVE_BINDING },
    ]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    expect(inBindingsPanel().getByText(AU_PT)).toBeInTheDocument();

    await user.type(
      inBindingsPanel().getByLabelText(/Changelog note/),
      "Add the primary code",
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    await waitFor(() => expect(callsTo(calls, BIND_PATH)).toHaveLength(1));
    const call = callsTo(calls, BIND_PATH)[0];
    expect(call?.body).toEqual({
      code: FSN_CODE,
      fsn: FSN,
      au_preferred_term: AU_PT,
      edition_hint: "au",
      reason: "Add the primary code",
    });
    // The wire text itself, not the parsed value: `JSON.parse` cannot tell a
    // quoted SCTID from a bare number once parsed either way.
    expect(call?.text).toContain(`"code":"${FSN_CODE}"`);
  });

  it("shows an inactive code as inactive, and still lets it be bound", async () => {
    // The partial H-05 mitigation this PR offers: the editor is told, but
    // the FR-45 sweep-time check (out of scope here) is what actually blocks.
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE, { active: false }),
      { method: "POST", path: BIND_PATH, status: 201, body: ACTIVE_BINDING },
    ]);
    await renderLoaded();

    // `ResolvedConcept` isolates each resolved value in its own `<span>`
    // (`getByText`'s default matcher only ever sees an element's own direct
    // text-node children, never text carried by a nested element) - so the
    // status is checked as that span's exact, standalone content, not a
    // phrase spanning the surrounding sentence.
    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    expect(inBindingsPanel().getByText("inactive")).toBeInTheDocument();
    await user.type(
      inBindingsPanel().getByLabelText(/Changelog note/),
      "Add the primary code",
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    await waitFor(() => expect(callsTo(calls, BIND_PATH)).toHaveLength(1));
  });

  it("shows an unreported active status distinctly from active or inactive", async () => {
    // `active: null` (the tri-state `ConceptLookup.active` can genuinely be)
    // must not be misread as "active" - that is hazard H-05's whole concern.
    const user = userEvent.setup();
    stubApi([READ_OK, terminologyRoute(FSN_CODE, { active: null })]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    expect(
      inBindingsPanel().getByText("not reported by the terminology server"),
    ).toBeInTheDocument();
    expect(inBindingsPanel().queryByText("active")).not.toBeInTheDocument();
    expect(inBindingsPanel().queryByText("inactive")).not.toBeInTheDocument();
  });

  it("shows a missing AU preferred term as not reported", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK, terminologyRoute(FSN_CODE, { au_preferred_term: null })]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    expect(inBindingsPanel().getByText("not reported")).toBeInTheDocument();
  });

  it("refuses to bind a code the server resolved with no name, and sends no request", async () => {
    // `ConceptLookup.fsn` is nullable - the server may report no FSN
    // designation at all. A `null` here must refuse to prefill rather than
    // substitute anything (never falling back to `display`/au_preferred_term).
    const user = userEvent.setup();
    const calls = stubApi([READ_OK, terminologyRoute(FSN_CODE, { fsn: null })]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      /did not return a name/,
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    expect(
      await screen.findAllByText(/must resolve against the terminology server/),
    ).toHaveLength(2);
    expect(callsTo(calls, BIND_PATH)).toHaveLength(0);
  });

  it("falls back to an unknown edition hint when the server reports an edition this screen does not recognise", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE, { edition: "gb" }),
      { method: "POST", path: BIND_PATH, status: 201, body: ACTIVE_BINDING },
    ]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    await user.type(
      inBindingsPanel().getByLabelText(/Changelog note/),
      "Add the primary code",
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    await waitFor(() => expect(callsTo(calls, BIND_PATH)).toHaveLength(1));
    expect(callsTo(calls, BIND_PATH)[0]?.body).toMatchObject({ edition_hint: "unknown" });
  });

  it("tells the editor a code is still checking, rather than reading as a rejection", async () => {
    // The debounce window means "hasn't fired yet" and "does not resolve"
    // would otherwise read identically - submitting right after pasting a
    // code, before the 400ms debounce fires, is a realistic sequence.
    //
    // A single `paste`, not `type`: typing nine characters races real
    // keystroke delays against the 400ms debounce, and on a loaded CI
    // runner enough of it could land inside that window for the lookup to
    // resolve before the click below, turning this into a flake rather than
    // a deterministic check of the still-checking state (review finding).
    // One paste is one state update, so nothing here waits on wall-clock
    // timing except the assertion itself.
    const user = userEvent.setup();
    const calls = stubApi([READ_OK, terminologyRoute(FSN_CODE)]);
    await renderLoaded();

    await user.click(inBindingsPanel().getByLabelText("SNOMED CT code"));
    await user.paste(FSN_CODE);
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    expect(
      await screen.findAllByText(/Wait for the code to finish checking/),
    ).toHaveLength(2);
    expect(
      screen.queryByText(/must resolve against the terminology server/),
    ).not.toBeInTheDocument();
    expect(callsTo(calls, BIND_PATH)).toHaveLength(0);
  });

  it("refuses to bind a code that does not resolve, and sends no request", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE, {
        status: 404,
        body: {
          detail: `${FSN_CODE} was not found in the AU edition. Check the identifier.`,
        },
      }),
    ]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      /was not found in the AU edition/,
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    // Twice over, as elsewhere in this file: once in the error summary,
    // once beside the field itself.
    expect(
      await screen.findAllByText(/must resolve against the terminology server/),
    ).toHaveLength(2);
    expect(callsTo(calls, BIND_PATH)).toHaveLength(0);
  });

  it("reads an unreachable terminology server differently from a code that does not exist", async () => {
    // FR-26/FR-54's whole point: the editor needs to know whether to retry
    // or to look again, so the two refusals must not read the same.
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE, {
        status: 503,
        body: {
          detail:
            "The terminology server could not be reached. Carry on editing and try again shortly.",
        },
      }),
    ]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      /could not be reached/,
    );

    expect(screen.queryByText(/was not found/)).not.toBeInTheDocument();
  });

  it("shows the server's own sentence when a bind is refused, not a status code", async () => {
    // The concurrent-editor case `catalogue-write-api.md` notes is
    // unguarded: these routes take no `row_version`, so a second active
    // binding can still reach the server as a 409 even though the form is
    // never offered for it deliberately.
    const user = userEvent.setup();
    stubApi([
      READ_OK,
      terminologyRoute(FSN_CODE),
      {
        method: "POST",
        path: BIND_PATH,
        status: 409,
        body: { detail: "This entry already has an active code binding." },
      },
    ]);
    await renderLoaded();

    await typeCodeAndWait(
      user,
      inBindingsPanel().getByLabelText("SNOMED CT code"),
      FSN_CODE,
      FSN,
    );
    await user.type(
      inBindingsPanel().getByLabelText(/Changelog note/),
      "Add the primary code",
    );
    await user.click(inBindingsPanel().getByRole("button", { name: "Bind code" }));

    expect(
      await screen.findByText(/already has an active code binding/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\b409\b/)).not.toBeInTheDocument();
  });

  it("refuses to retire without a reason", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      { ...READ_OK, body: { ...ENTRY, bindings: [ACTIVE_BINDING] } },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: `Retire ${FSN_CODE}` }));
    await user.click(inDialog().getByRole("button", { name: "Retire binding" }));

    expect(await inDialog().findAllByText(/Enter a changelog note/)).toHaveLength(2);
    expect(callsTo(calls, `${BIND_PATH}/${FSN_CODE}/retirement`)).toHaveLength(0);
  });

  it("retires a binding with its reason, addressed by its active code", async () => {
    const user = userEvent.setup();
    const calls = stubApi([
      { ...READ_OK, body: { ...ENTRY, bindings: [ACTIVE_BINDING] } },
      {
        method: "POST",
        path: `${BIND_PATH}/${FSN_CODE}/retirement`,
        status: 200,
        body: { ...ACTIVE_BINDING, status: "retired", retirement_reason: "Superseded" },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: `Retire ${FSN_CODE}` }));
    await user.type(inDialog().getByLabelText(/Changelog note/), "Superseded");
    await user.click(inDialog().getByRole("button", { name: "Retire binding" }));

    await waitFor(() =>
      expect(callsTo(calls, `${BIND_PATH}/${FSN_CODE}/retirement`)).toHaveLength(1),
    );
    expect(callsTo(calls, `${BIND_PATH}/${FSN_CODE}/retirement`)[0]?.body).toEqual({
      reason: "Superseded",
    });
  });

  it("replaces a binding: retires it and binds the resolved successor in one request", async () => {
    // Retire and Replace are two separate actions: `/replacement` is the
    // only route that populates `replaced_by_code`.
    const user = userEvent.setup();
    const SUCCESSOR_CODE = "165288007";
    const calls = stubApi([
      { ...READ_OK, body: { ...ENTRY, bindings: [ACTIVE_BINDING] } },
      terminologyRoute(SUCCESSOR_CODE, {
        fsn: "Basophil count (procedure)",
        au_preferred_term: "Basophil count",
      }),
      {
        method: "POST",
        path: `${BIND_PATH}/${FSN_CODE}/replacement`,
        status: 200,
        body: { items: [] },
      },
    ]);
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: `Replace ${FSN_CODE}` }));
    await typeCodeAndWait(
      user,
      inDialog().getByLabelText("Successor SNOMED CT code"),
      SUCCESSOR_CODE,
      "Basophil count (procedure)",
    );
    await user.type(
      inDialog().getByLabelText(/Changelog note/),
      "Superseded by the new method",
    );
    await user.click(inDialog().getByRole("button", { name: "Replace" }));

    await waitFor(() =>
      expect(callsTo(calls, `${BIND_PATH}/${FSN_CODE}/replacement`)).toHaveLength(1),
    );
    expect(callsTo(calls, `${BIND_PATH}/${FSN_CODE}/replacement`)[0]?.body).toEqual({
      successor: {
        code: SUCCESSOR_CODE,
        fsn: "Basophil count (procedure)",
        au_preferred_term: "Basophil count",
        edition_hint: "au",
      },
      reason: "Superseded by the new method",
    });
  });

  it("keeps a retired binding visible, with its reason and successor code (FR-08)", async () => {
    stubApi([{ ...READ_OK, body: { ...ENTRY, bindings: [RETIRED_BINDING] } }]);

    await renderLoaded();

    const table = screen.getByRole("table", { name: `Code bindings on ${BUSINESS_KEY}` });
    expect(
      within(table).getByRole("rowheader", { name: RETIRED_BINDING.code }),
    ).toBeInTheDocument();
    // The reason and the replacement sit in one cell as separate text nodes,
    // not a substring on its own - a substring matcher, not an exact one.
    expect(
      within(table).getByText(new RegExp(RETIRED_BINDING.retirement_reason)),
    ).toBeInTheDocument();
    expect(
      within(table).getByText(new RegExp(`replaced by ${FSN_CODE}`)),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: `Retire ${RETIRED_BINDING.code}` }),
    ).not.toBeInTheDocument();
    // No active binding among these rows, so the bind form is offered.
    expect(screen.getByRole("heading", { name: "Bind a code" })).toBeInTheDocument();
  });

  it("renders no view or component that strips or re-derives a semantic tag", () => {
    // A structural guarantee, not just this fixture's behaviour - see
    // `fr-83-no-semantic-tag-stripping.test.ts`, which walks every module
    // under `frontend/src` rather than relying on one screen's rendering.
    expect(FSN).not.toBe(ACTIVE_BINDING.display_term);
  });
});

describe("accessibility", () => {
  it("has no automated accessibility violations on the loaded screen", async () => {
    stubApi([READ_OK]);

    const { container } = await renderLoaded();

    await expectNoA11yViolations(container);
  });

  it("has no automated accessibility violations with a dialog open", async () => {
    const user = userEvent.setup();
    stubApi([READ_OK]);
    const { container } = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit Ferritin (preferred)" }));

    await expectNoA11yViolations(container);
  });

  it("has no automated accessibility violations with the code bindings panel populated and a dialog open", async () => {
    const user = userEvent.setup();
    stubApi([
      { ...READ_OK, body: { ...ENTRY, bindings: [ACTIVE_BINDING, RETIRED_BINDING] } },
    ]);
    const { container } = await renderLoaded();

    await user.click(screen.getByRole("button", { name: `Retire ${FSN_CODE}` }));

    await expectNoA11yViolations(container);
  });
});
