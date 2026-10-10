import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * Proposing an amendment to a catalogue entry (FR-35, FR-26, FR-43, NFR-20,
 * NFR-45), driven through the real route at `/submissions/new?entry=<key>`.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const KEY = "NPTC-000006";
// Eighteen digits: past `Number.MAX_SAFE_INTEGER`, so a coerced code would
// render differently (FR-06).
const ENTRY_CODE = "999480561000168100";
const CODE = "391483001";
const FSN = "Microscopy (acid fast bacilli) (procedure)";
const AU_PT = "Microscopy (AFB)";
const REFERENCE = "https://example.org/ferritin";

const ME: Route = {
  method: "GET",
  path: "/auth/me",
  status: 200,
  body: {
    authenticated: true,
    user: {
      username: "jo",
      display_name: "Jo Citizen",
      organisation: "Acme Pathology",
      status: "active",
    },
    roles: ["contributor"],
    permissions: ["amendment.propose"],
    mfa_satisfied: false,
  },
};

function entry(overrides: Record<string, unknown> = {}) {
  return {
    business_key: KEY,
    preferred_term: "Ferritin",
    length: 8,
    status: "active",
    updated_at: "2026-09-01T12:00:00Z",
    has_open_finding: false,
    code: ENTRY_CODE,
    disciplines: [],
    label_provenance: {},
    row_version: 7,
    designations: [
      {
        term: "Serum ferritin",
        status: "active",
        length: 14,
        label_provenance: {},
      },
      {
        term: "Retired name",
        status: "retired",
        length: 12,
        label_provenance: {},
      },
    ],
    bindings: [],
    properties: [],
    snomed_synonyms: null,
    ...overrides,
  };
}

const ENTRY_OK: Route = {
  method: "GET",
  path: `/catalogue/entries/${KEY}`,
  status: 200,
  body: entry(),
};

const CREATED: Route = {
  method: "POST",
  path: "/submissions/amendments",
  status: 201,
  body: { id: "created-amendment-1", preferred_term: "Ferritin" },
};

const PROCEDURES: Route = {
  method: "GET",
  path: "/terminology/procedures",
  status: 200,
  body: {
    items: [{ code: CODE, au_preferred_term: AU_PT, label_provenance: {} }],
    total: 1,
  },
};

const CONCEPT: Route = {
  method: "GET",
  path: `/terminology/concepts/${CODE}`,
  status: 200,
  body: {
    system: "http://snomed.info/sct",
    code: CODE,
    fsn: FSN,
    au_preferred_term: AU_PT,
    active: true,
    edition: "au",
    resolved_version: "http://snomed.info/sct/32506021000036107/version/20260101",
  },
};

function refusal(status: number, body: unknown, headers?: Record<string, string>): Route {
  return { method: "POST", path: "/submissions/amendments", status, body, headers };
}

type User = ReturnType<typeof userEvent.setup>;

async function renderPage(path = `/submissions/new?entry=${KEY}`) {
  const result = await renderRoute(path, SIGNED_IN);
  await screen.findByLabelText("Other name 1");
  return result;
}

async function submit(user: User) {
  await user.click(screen.getByRole("button", { name: "Propose change" }));
}

function writes(calls: ReturnType<typeof stubApi>) {
  return calls.filter(
    (call) => call.method === "POST" && call.path.endsWith("/submissions/amendments"),
  );
}

function bodyOf(calls: ReturnType<typeof stubApi>) {
  return writes(calls)[0]?.body as Record<string, unknown>;
}

function describedBy(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}

async function pickCode(user: User) {
  await user.type(screen.getByLabelText("SNOMED CT code"), "micro");
  await user.click(
    await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
  );
  await screen.findByText(FSN, {}, { timeout: 2000 });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the amendment form (FR-35)", () => {
  it("shows the entry's name, other names and code, none of them editable", async () => {
    const calls = stubApi([ME, ENTRY_OK]);

    await renderPage();

    const now = within(
      (await screen.findByRole("heading", { name: "The entry now" }))
        .parentElement as HTMLElement,
    );
    expect(now.getByRole("link", { name: "Ferritin" })).toHaveAttribute(
      "href",
      `/catalogue/${KEY}`,
    );
    expect(now.getByText("Serum ferritin")).toBeVisible();
    expect(now.queryByText("Retired name")).not.toBeInTheDocument();
    expect(now.getByText(ENTRY_CODE, { selector: "code" })).toBeVisible();
    expect(screen.queryByLabelText(/^Test name/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 1, name: "Propose a change" }),
    ).toBeVisible();
    expect(
      calls.some(
        (call) => call.method === "GET" && call.path.endsWith(`/entries/${KEY}`),
      ),
    ).toBe(true);
  });

  it("offers only names, a code, a reference link, notes and the organisation", async () => {
    const calls = stubApi([ME, ENTRY_OK]);

    await renderPage();

    expect(screen.getByLabelText("Other name 1")).toBeVisible();
    expect(screen.getByLabelText("SNOMED CT code")).toBeVisible();
    expect(screen.getByLabelText("Reference link")).toBeVisible();
    expect(describedBy(screen.getByLabelText("Reference link"))).toContain(
      "supplier's test directory",
    );
    expect(describedBy(screen.getByLabelText("Reference link"))).not.toContain(
      "published method",
    );
    expect(screen.getByLabelText("Notes")).toBeVisible();
    expect(screen.getByLabelText("Organisation")).toBeVisible();
    expect(screen.queryByText("Properties")).not.toBeInTheDocument();
    expect(calls.some((call) => call.path.endsWith("/registry/properties"))).toBe(false);
    expect(document.title).toBe("Propose a change — NPTC Catalogue");
  });

  it("fills the organisation from the profile and shows who proposes", async () => {
    stubApi([ME, ENTRY_OK]);

    await renderPage();

    await waitFor(() =>
      expect(screen.getByLabelText("Organisation")).toHaveValue("Acme Pathology"),
    );
    expect(screen.getByText("Proposed by").nextElementSibling).toHaveTextContent(
      "Jo Citizen",
    );
  });

  it("leaves the page the new-test form when there is no entry", async () => {
    stubApi([
      ME,
      { method: "GET", path: "/registry/properties", status: 200, body: { items: [] } },
    ]);

    await renderRoute("/submissions/new", SIGNED_IN);

    expect(await screen.findByLabelText("Test name (required)")).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Propose a change" }),
    ).not.toBeInTheDocument();
  });
});

describe("sending an amendment (FR-35)", () => {
  it("sends one new name with the entry's key and nothing that is empty", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await submit(user);

    const heading = await screen.findByRole("heading", {
      name: "Your change was proposed",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(writes(calls)).toHaveLength(1);
    expect(bodyOf(calls)).toEqual({
      entry_business_key: KEY,
      synonyms: ["Ferritin level"],
    });
    expect(bodyOf(calls)).not.toHaveProperty("reference_url");
    expect(bodyOf(calls)).not.toHaveProperty("snomed_code");
    const done = within(screen.getByRole("region", { name: "Your change was proposed" }));
    expect(done.getByRole("link", { name: "Back to the entry" })).toHaveAttribute(
      "href",
      `/catalogue/${KEY}`,
    );
    expect(done.getByRole("link", { name: "Back to the catalogue" })).toHaveAttribute(
      "href",
      "/catalogue",
    );
  });

  it("splits a pasted list into one name each", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByLabelText("Other name 1"));
    await user.paste("Ferritin; Serum Fe store");
    await submit(user);
    await screen.findByText("Your change was proposed");

    expect(bodyOf(calls).synonyms).toEqual(["Ferritin", "Serum Fe store"]);
  });

  it("sends the picked code as a string the terminology server named (FR-06, FR-26)", async () => {
    const calls = stubApi([ME, ENTRY_OK, PROCEDURES, CONCEPT, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await pickCode(user);
    await submit(user);
    await screen.findByText("Your change was proposed");

    expect(bodyOf(calls)).toEqual({ entry_business_key: KEY, snomed_code: CODE });
    expect(writes(calls)[0]?.text).toContain(`"snomed_code":"${CODE}"`);
  });

  it("sends a reference link, notes and a changed organisation when given", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await waitFor(() =>
      expect(screen.getByLabelText("Organisation")).toHaveValue("Acme Pathology"),
    );

    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await user.type(screen.getByLabelText("Reference link"), REFERENCE);
    await user.type(screen.getByLabelText("Notes"), "Seen in the RCPA manual.");
    await user.clear(screen.getByLabelText("Organisation"));
    await user.type(screen.getByLabelText("Organisation"), "Example Lab");
    await submit(user);
    await screen.findByText("Your change was proposed");

    expect(bodyOf(calls)).toEqual({
      entry_business_key: KEY,
      synonyms: ["Ferritin level"],
      reference_url: REFERENCE,
      notes: "Seen in the RCPA manual.",
      organisation: "Example Lab",
    });
  });

  it("leaves a blank reference link and an untouched organisation out of the request", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await user.type(screen.getByLabelText("Reference link"), "   ");
    await submit(user);
    await screen.findByText("Your change was proposed");

    expect(bodyOf(calls)).not.toHaveProperty("reference_url");
    expect(bodyOf(calls)).not.toHaveProperty("organisation");
    expect(bodyOf(calls)).not.toHaveProperty("notes");
  });
});

describe("what holds an amendment back (FR-35)", () => {
  it("says what to add when nothing is filled in, and sends nothing", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await submit(user);

    const summary = await screen.findByRole("heading", { name: "There is a problem" });
    expect(
      within(summary.parentElement as HTMLElement).getByText(
        "Add a new other name or a SNOMED CT code to propose a change.",
      ),
    ).toBeVisible();
    expect(describedBy(screen.getByRole("group", { name: "New other names" }))).toContain(
      "Add a new other name",
    );
    expect(writes(calls)).toHaveLength(0);
  });

  it("treats blank names as nothing", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await user.type(screen.getByLabelText("Other name 1"), "   ");
    await submit(user);

    expect(
      await screen.findByRole("heading", { name: "There is a problem" }),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(0);
  });

  it("holds the form back while the terminology server cannot name the code", async () => {
    const calls = stubApi([
      ME,
      ENTRY_OK,
      PROCEDURES,
      {
        ...CONCEPT,
        status: 503,
        body: { detail: "The terminology server could not be reached." },
      },
      CREATED,
    ]);
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByLabelText("SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );
    await screen.findByText(/The terminology server could not be reached, so the code/);

    await submit(user);

    expect(
      await screen.findAllByText(/Clear the code to send the form/),
    ).not.toHaveLength(0);
    expect(writes(calls)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Clear the code" }));
    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await submit(user);
    await screen.findByText("Your change was proposed");
    expect(bodyOf(calls)).not.toHaveProperty("snomed_code");
  });

  it("names a name over the server's limit before sending", async () => {
    const calls = stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByLabelText("Other name 1"));
    await user.paste("x".repeat(501));
    await submit(user);

    expect(
      (await screen.findAllByText("An other name is over 500 characters. Shorten it."))
        .length,
    ).toBeGreaterThan(0);
    expect(writes(calls)).toHaveLength(0);
  });
});

describe("what the server refuses (FR-35, FR-43, NFR-20, NFR-45)", () => {
  async function propose(routes: Route[]) {
    const calls = stubApi([ME, ENTRY_OK, ...routes]);
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await submit(user);
    return { calls, user };
  }

  it("shows the server's sentence to a user without permission, with no check of its own", async () => {
    const { calls } = await propose([
      refusal(403, { detail: "You do not have permission to do this." }),
    ]);

    expect(
      await screen.findByText("You do not have permission to do this."),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(1);
    expect(screen.queryByText("Your change was proposed")).not.toBeInTheDocument();
  });

  it("says the entry can no longer be amended, then gives the server's reason", async () => {
    await propose([
      refusal(409, { detail: "This entry is deprecated, so it cannot be amended." }),
    ]);

    const message = await screen.findByText(/This entry can no longer be amended\./);
    expect(message).toHaveTextContent(
      "This entry can no longer be amended. This entry is deprecated, so it cannot be amended.",
    );
    expect(screen.getByLabelText("Other name 1")).toHaveValue("Ferritin level");
  });

  it("shows the sentence for an entry that no longer exists", async () => {
    await propose([refusal(404, { detail: "No entry has that key." })]);

    expect(await screen.findByText("No entry has that key.")).toBeVisible();
  });

  it("shows the sentence when the entry already holds every name and code", async () => {
    await propose([
      refusal(422, {
        detail: "There is nothing to propose: the entry already has these names.",
      }),
    ]);

    expect(
      await screen.findByText(
        "There is nothing to propose: the entry already has these names.",
      ),
    ).toBeVisible();
  });

  it("marks the field the server names, and clears the mark when it is edited", async () => {
    const { user } = await propose([
      refusal(422, { detail: "This term could not be saved.", field: "synonyms" }),
    ]);

    const group = screen.getByRole("group", { name: "New other names" });
    await waitFor(() => expect(describedBy(group)).toContain("could not be saved"));
    expect(screen.getByLabelText("Other name 1")).toHaveValue("Ferritin level");

    await user.type(screen.getByLabelText("Other name 1"), "x");
    expect(describedBy(group)).not.toContain("could not be saved");
  });

  it("marks the reference link when the server cannot reach it", async () => {
    await propose([
      refusal(422, {
        detail: "The reference link answered with status 404, so it was not accepted.",
        field: "reference_url",
      }),
    ]);

    const field = screen.getByLabelText("Reference link");
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(describedBy(field)).toContain("status 404");
  });

  it("marks the code when the server's answer about it is unusable", async () => {
    stubApi([
      ME,
      ENTRY_OK,
      PROCEDURES,
      CONCEPT,
      refusal(502, {
        detail: "The terminology server's response could not be used.",
        field: "snomed_code",
      }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await pickCode(user);

    await submit(user);

    const field = screen.getByLabelText("SNOMED CT code");
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(describedBy(field)).toContain("response could not be used");
  });

  it("tells the user when an hourly quota lifts, from Retry-After", async () => {
    await propose([
      refusal(
        429,
        { detail: "Quota used.", limit: "hourly", maximum: 20 },
        { "Retry-After": "1800" },
      ),
    ]);

    expect(
      await screen.findByText(
        /limit of 20 submissions in one hour\. You can submit again in about 30 minutes\./,
      ),
    ).toBeVisible();
  });

  it("keeps the typed form when the terms must be accepted first", async () => {
    await propose([
      refusal(403, {
        detail: "You need to accept the current terms of use before you can do this.",
        code: "terms_acceptance_required",
      }),
    ]);

    expect(await screen.findByText(/Your answers are kept/)).toBeVisible();
    expect(screen.getByLabelText("Other name 1")).toHaveValue("Ferritin level");
  });

  it("shows a plain sentence when the request cannot be sent at all", async () => {
    await propose([refusal(500, "not json")]);

    expect(
      await screen.findByText(/The change could not be proposed\. Check your connection/),
    ).toBeVisible();
  });
});

describe("an entry that is not active (FR-35)", () => {
  it.each([
    ["deprecated", "Deprecated"],
    ["withdrawn", "Withdrawn"],
  ])(
    "says a %s entry can no longer be amended, in place of the form",
    async (status, label) => {
      const calls = stubApi([ME, { ...ENTRY_OK, body: entry({ status }) }, CREATED]);

      await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

      const message = await screen.findByText(/This entry can no longer be amended\./);
      expect(message).toHaveTextContent(`is ${label.toLowerCase()}`);
      expect(screen.queryByLabelText("Other name 1")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Propose change" }),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to the entry" })).toHaveAttribute(
        "href",
        `/catalogue/${KEY}`,
      );
      expect(writes(calls)).toHaveLength(0);
    },
  );

  it("has no axe violations on the notice", async () => {
    stubApi([ME, { ...ENTRY_OK, body: entry({ status: "deprecated" }) }]);

    const { container } = await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);
    await screen.findByText(/This entry can no longer be amended\./);

    await expectNoA11yViolations(container);
  });
});

describe("the page title (FR-35)", () => {
  it("names the amendment form while the entry is still loading", async () => {
    stubApi([ME, { ...ENTRY_OK, neverSettles: true }]);

    await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

    expect(await screen.findByText("Loading entry…")).toBeVisible();
    await waitFor(() => expect(document.title).toBe("Propose a change — NPTC Catalogue"));
  });

  it("names the amendment form when the entry fails to load", async () => {
    stubApi([ME, { ...ENTRY_OK, status: 500, body: { detail: "boom" } }]);

    await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

    await screen.findByRole("button", { name: "Try again" });
    expect(document.title).toBe("Propose a change — NPTC Catalogue");
  });

  it("keeps the new-test title when there is no entry", async () => {
    stubApi([
      ME,
      { method: "GET", path: "/registry/properties", status: 200, body: { items: [] } },
    ]);

    await renderRoute("/submissions/new", SIGNED_IN);

    await screen.findByLabelText("Test name (required)");
    expect(document.title).toBe("Submit a new test — NPTC Catalogue");
  });
});

describe("loading the entry (FR-35)", () => {
  it("shows the not-found page for a key that names no entry", async () => {
    stubApi([ME, { ...ENTRY_OK, status: 404, body: { detail: "Not found" } }]);

    await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

    expect(
      await screen.findByRole("heading", { name: "We couldn't find that page" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Other name 1")).not.toBeInTheDocument();
  });

  it("shows the not-found page for a key that is not well formed", async () => {
    stubApi([ME, { ...ENTRY_OK, status: 422, body: { detail: [] } }]);

    await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

    expect(
      await screen.findByRole("heading", { name: "We couldn't find that page" }),
    ).toBeInTheDocument();
  });

  it("offers a retry when the entry fails to load, and recovers", async () => {
    let failing = true;
    stubApi([ME, ENTRY_OK], {
      vary: ({ path }) =>
        failing && path.endsWith(`/entries/${KEY}`)
          ? { method: "GET", path, status: 500, body: { detail: "boom" } }
          : null,
    });
    await renderRoute(`/submissions/new?entry=${KEY}`, SIGNED_IN);

    expect(
      (
        await screen.findAllByText(
          "This entry could not be loaded. Try again in a moment.",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Other name 1")).not.toBeInTheDocument();

    failing = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Other name 1")).toBeVisible();
  });
});

describe("the amendment form and accessibility (FR-35)", () => {
  it("has no axe violations on the form, or with its errors showing", async () => {
    stubApi([ME, ENTRY_OK]);
    const user = userEvent.setup();
    const { container } = await renderPage();
    await expectNoA11yViolations(container);

    await submit(user);
    await screen.findByRole("heading", { name: "There is a problem" });

    await expectNoA11yViolations(container);
  });

  it("has no axe violations on the confirmation", async () => {
    stubApi([ME, ENTRY_OK, CREATED]);
    const user = userEvent.setup();
    const { container } = await renderPage();
    await user.type(screen.getByLabelText("Other name 1"), "Ferritin level");
    await submit(user);
    await screen.findByRole("heading", { name: "Your change was proposed" });

    await expectNoA11yViolations(container);
  });
});
