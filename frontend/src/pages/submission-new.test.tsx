import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The new-test submission page (FR-23 to FR-27, FR-43, FR-54, NFR-45), driven
 * through the real route. The property keys are invented: the form must show
 * whatever the registry's submission scope lists, never a key it knows.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

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
    roles: ["provisional"],
    permissions: ["submission.create"],
    mfa_satisfied: false,
  },
};

function definition(overrides: Record<string, unknown>) {
  return {
    key: "assay_method",
    label: "Assay method",
    datatype: "alpha",
    cardinality: "1..1",
    scope: "submission",
    required_for_submission: true,
    required_for_publication: false,
    binding_target: null,
    value_set_uri: null,
    strength: null,
    edition: null,
    local_code_system_key: null,
    filterable: false,
    origin: "admin",
    status: "active",
    display_order: 10,
    constraints: {},
    row_version: 1,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const DEFINITIONS: Route = {
  method: "GET",
  path: "/registry/properties",
  status: 200,
  body: {
    items: [
      definition({}),
      definition({
        key: "clinical_use",
        label: "Clinical use",
        cardinality: "0..*",
        required_for_submission: false,
        display_order: 20,
        form_control: { control: "textarea", params: {} },
      }),
    ],
  },
};

const NO_MATCHES: Route = {
  method: "POST",
  path: "/submissions/duplicate-check",
  status: 200,
  body: { matches: [] },
};

const CREATED: Route = {
  method: "POST",
  path: "/submissions",
  status: 201,
  body: { id: "created-submission-1", preferred_term: "Serum ferritin" },
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

const CATALOGUE_MATCH = {
  source: "catalogue_entry",
  matched_on: "preferred_term",
  key: "NPTC-000123",
  preferred_term: "Serum ferritin level",
  term: "Serum ferritin level",
  similarity: 0.82,
  label_provenance: {},
};

const OPEN_MATCH = {
  source: "submission",
  matched_on: "code",
  key: "open-submission-1",
  preferred_term: "Ferritin",
  term: "Ferritin",
  similarity: null,
  label_provenance: {},
};

function refusal(status: number, body: unknown, headers?: Record<string, string>): Route {
  return { method: "POST", path: "/submissions", status, body, headers };
}

type User = ReturnType<typeof userEvent.setup>;

async function renderPage() {
  const result = await renderRoute("/submissions/new", SIGNED_IN);
  await screen.findByLabelText("Test name (required)");
  return result;
}

async function fillRequired(user: User) {
  await user.type(screen.getByLabelText("Test name (required)"), "Serum ferritin");
  await user.type(screen.getByLabelText("Assay method (required)"), "Immunoassay");
  await user.type(screen.getByLabelText("Reference link (required)"), REFERENCE);
}

async function submit(user: User) {
  await user.click(screen.getByRole("button", { name: "Submit test" }));
}

function writes(calls: ReturnType<typeof stubApi>, suffix: string) {
  return calls.filter((call) => call.method === "POST" && call.path.endsWith(suffix));
}

function bodyOf(calls: ReturnType<typeof stubApi>, suffix: string) {
  return writes(calls, suffix)[0]?.body as Record<string, unknown>;
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

describe("SubmissionNewPage fields", () => {
  it("shows the registry's submission properties and no computed field", async () => {
    const calls = stubApi([ME, DEFINITIONS]);

    await renderPage();

    expect(await screen.findByLabelText("Assay method (required)")).toBeVisible();
    expect(screen.getByLabelText("Clinical use 1")).toBeVisible();
    expect(screen.getByLabelText("Clinical use 1").tagName).toBe("TEXTAREA");
    expect(screen.queryByLabelText(/length/i)).not.toBeInTheDocument();
    const request = calls.find((call) => call.path.endsWith("/registry/properties"));
    expect(request?.searchParams.get("scope")).toBe("submission");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("fills the organisation from the profile and shows the submitter, read only", async () => {
    stubApi([ME, DEFINITIONS]);

    await renderPage();

    await waitFor(() =>
      expect(screen.getByLabelText("Organisation")).toHaveValue("Acme Pathology"),
    );
    expect(screen.getByText("Submitted by").nextElementSibling).toHaveTextContent(
      "Jo Citizen",
    );
    expect(screen.queryByLabelText("Submitted by")).not.toBeInTheDocument();
  });

  it("leaves the organisation out of the request until the user edits it", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);
    await screen.findByText("Your test was submitted");

    expect(bodyOf(calls, "/submissions")).not.toHaveProperty("organisation");
  });

  it("sends an edited organisation, and a cleared one as blank", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);
    await waitFor(() =>
      expect(screen.getByLabelText("Organisation")).toHaveValue("Acme Pathology"),
    );

    await user.clear(screen.getByLabelText("Organisation"));
    await submit(user);
    await screen.findByText("Your test was submitted");

    expect(bodyOf(calls, "/submissions").organisation).toBe("");
  });

  it("splits a pasted list of names into one row each", async () => {
    stubApi([ME, DEFINITIONS]);
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByLabelText("Other name 1"));
    await user.paste("Ferritin; Serum Fe store; Ferritin level");

    expect(screen.getByLabelText("Other name 1")).toHaveValue("Ferritin");
    expect(screen.getByLabelText("Other name 2")).toHaveValue("Serum Fe store");
    expect(screen.getByLabelText("Other name 3")).toHaveValue("Ferritin level");
  });

  it("adds and removes other names and drops blank ones from the request", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await user.type(screen.getByLabelText("Other name 1"), "Ferritin");
    await user.click(screen.getByRole("button", { name: "Add another name" }));
    await user.click(screen.getByRole("button", { name: "Add another name" }));
    await user.type(screen.getByLabelText("Other name 3"), "Fe store");
    await user.click(screen.getByRole("button", { name: "Remove other name 1" }));
    await submit(user);
    await screen.findByText("Your test was submitted");

    expect(bodyOf(calls, "/submissions").synonyms).toEqual(["Fe store"]);
  });
});

describe("SubmissionNewPage validation", () => {
  it("refuses a form with no name, no required property and no reference, and sends nothing", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await screen.findByLabelText("Assay method (required)");

    await submit(user);

    const summary = await screen.findByRole("heading", { name: "There is a problem" });
    const list = within(summary.parentElement as HTMLElement);
    expect(list.getByText("Enter the test name.")).toBeVisible();
    expect(list.getByText("Enter a value for Assay method.")).toBeVisible();
    expect(
      list.getByText("Enter a link to a web page that supports this test."),
    ).toBeVisible();
    const name = screen.getByLabelText("Test name (required)");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(describedBy(name)).toContain("Enter the test name.");
    expect(writes(calls, "/duplicate-check")).toHaveLength(0);
    expect(writes(calls, "/submissions")).toHaveLength(0);
  });

  it("names a name that is over the server's limit before sending", async () => {
    const calls = stubApi([ME, DEFINITIONS]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await user.click(screen.getByLabelText("Test name (required)"));
    await user.paste("x".repeat(501));
    await submit(user);

    expect(
      (await screen.findAllByText("The test name is over 500 characters. Shorten it."))
        .length,
    ).toBeGreaterThan(0);
    expect(writes(calls, "/submissions")).toHaveLength(0);
  });
});

describe("SubmissionNewPage code", () => {
  it("sends the picked code as a string the server named", async () => {
    const calls = stubApi([ME, DEFINITIONS, PROCEDURES, CONCEPT, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await pickCode(user);
    expect(screen.getByText(AU_PT)).toBeVisible();
    await submit(user);
    await screen.findByText("Your test was submitted");

    expect(bodyOf(calls, "/submissions").snomed_code).toBe(CODE);
    expect(writes(calls, "/submissions")[0]?.text).toContain(`"snomed_code":"${CODE}"`);
    expect(bodyOf(calls, "/duplicate-check").snomed_code).toBe(CODE);
  });

  it("says the terminology server is down and still submits without a code", async () => {
    const calls = stubApi([
      ME,
      DEFINITIONS,
      PROCEDURES,
      {
        ...CONCEPT,
        status: 503,
        body: { detail: "The terminology server could not be reached." },
      },
      NO_MATCHES,
      CREATED,
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);
    await user.type(screen.getByLabelText("SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );

    expect(
      await screen.findByText(/The terminology server could not be reached, so the code/),
    ).toBeVisible();
    await submit(user);
    expect(
      await screen.findAllByText(/Clear the code to send the form/),
    ).not.toHaveLength(0);
    expect(writes(calls, "/submissions")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Clear the code" }));
    await submit(user);
    await screen.findByText("Your test was submitted");

    expect(bodyOf(calls, "/submissions")).not.toHaveProperty("snomed_code");
  });

  it("marks the code field when the server cannot check the code on submit", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      PROCEDURES,
      CONCEPT,
      NO_MATCHES,
      refusal(503, {
        detail: "The terminology server could not be reached.",
        field: "snomed_code",
      }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);
    await pickCode(user);

    await submit(user);

    await waitFor(() =>
      expect(describedBy(screen.getByLabelText("SNOMED CT code"))).toContain(
        "could not be reached",
      ),
    );
    expect(screen.getByLabelText("Test name (required)")).toHaveValue("Serum ferritin");
  });
});

describe("SubmissionNewPage duplicate step", () => {
  it("checks first and, with no match, submits once without a confirmation", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    expect(await screen.findByText("Your test was submitted")).toBeVisible();
    expect(writes(calls, "/duplicate-check")).toHaveLength(1);
    expect(writes(calls, "/submissions")).toHaveLength(1);
    expect(bodyOf(calls, "/submissions").confirm_not_duplicate).toBe(false);
    expect(bodyOf(calls, "/submissions").property_values).toEqual({
      assay_method: [{ value: "Immunoassay", justification: null }],
    });
  });

  it("lists the matches with links and sends nothing until the user confirms", async () => {
    const calls = stubApi([
      ME,
      DEFINITIONS,
      { ...NO_MATCHES, body: { matches: [CATALOGUE_MATCH, OPEN_MATCH] } },
      CREATED,
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const heading = await screen.findByRole("heading", {
      name: "This test may already exist",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    const link = screen.getByRole("link", { name: /NPTC-000123/ });
    expect(link).toHaveAttribute("href", "/catalogue/NPTC-000123");
    expect(screen.getByText(/Submission already open/)).toBeVisible();
    expect(screen.getByText(/82% alike/)).toBeVisible();
    expect(writes(calls, "/submissions")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Submit this test" }));
    expect(
      (await screen.findAllByText("Tick the box to say your test is different.")).length,
    ).toBeGreaterThan(0);
    expect(writes(calls, "/submissions")).toHaveLength(0);

    await user.click(screen.getByLabelText(/My test is different from all of them/));
    await user.click(screen.getByRole("button", { name: "Submit this test" }));

    await screen.findByText("Your test was submitted");
    expect(bodyOf(calls, "/submissions").confirm_not_duplicate).toBe(true);
  });

  it("keeps what the user typed when they go back to change the entry", async () => {
    stubApi([ME, DEFINITIONS, { ...NO_MATCHES, body: { matches: [CATALOGUE_MATCH] } }]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);
    await submit(user);
    await screen.findByRole("heading", { name: "This test may already exist" });

    await user.click(screen.getByRole("button", { name: "Change my entry" }));

    expect(screen.getByLabelText("Test name (required)")).toBeVisible();
    expect(screen.getByLabelText("Test name (required)")).toHaveValue("Serum ferritin");
    expect(screen.getByLabelText("Reference link (required)")).toHaveValue(REFERENCE);
  });

  it("opens the same step when a match appears between the check and the send", async () => {
    const calls = stubApi([ME, DEFINITIONS, NO_MATCHES], {
      vary: ({ method, path }, prior) =>
        method === "POST" && path.endsWith("/submissions") && prior === 0
          ? refusal(409, {
              detail: "This submission may duplicate an entry.",
              matches: [CATALOGUE_MATCH],
            })
          : method === "POST" && path.endsWith("/submissions")
            ? CREATED
            : null,
    });
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    expect(
      await screen.findByRole("heading", { name: "This test may already exist" }),
    ).toBeVisible();
    await user.click(screen.getByLabelText(/My test is different/));
    await user.click(screen.getByRole("button", { name: "Submit this test" }));
    await screen.findByText("Your test was submitted");
    expect(writes(calls, "/submissions")).toHaveLength(2);
    expect(
      (writes(calls, "/submissions")[1]?.body as Record<string, unknown>)
        .confirm_not_duplicate,
    ).toBe(true);
  });
});

describe("SubmissionNewPage refusals", () => {
  it("marks the reference link field with the reason the server gave", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      NO_MATCHES,
      refusal(422, {
        detail: "The reference link answered with status 404, so it was not accepted.",
        field: "reference_url",
      }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const field = screen.getByLabelText("Reference link (required)");
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(describedBy(field)).toContain("status 404");
    expect(screen.getByLabelText("Test name (required)")).not.toHaveAttribute(
      "aria-invalid",
    );
    expect(field).toHaveValue(REFERENCE);

    await user.type(field, "x");
    expect(field).not.toHaveAttribute("aria-invalid");
  });

  it("marks the name field when the server cannot clean the name", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      {
        ...NO_MATCHES,
        status: 422,
        body: { detail: "This term could not be saved.", field: "preferred_term" },
      },
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const name = screen.getByLabelText("Test name (required)");
    await waitFor(() => expect(name).toHaveAttribute("aria-invalid", "true"));
    expect(describedBy(name)).toContain("could not be saved");
  });

  it("marks the property the server names, and no other", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      NO_MATCHES,
      refusal(422, {
        detail: "One or more of the values you entered could not be saved.",
        issues: [
          {
            property_key: "assay_method",
            label: "Assay method",
            code: "invalid",
            message: "Assay method is not valid.",
            ordinal: 0,
          },
        ],
      }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const field = screen.getByLabelText("Assay method (required)");
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(describedBy(field)).toContain("Assay method is not valid.");
  });

  it("tells the user when an hourly quota lifts, from Retry-After", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      NO_MATCHES,
      refusal(
        429,
        { detail: "Quota used.", limit: "hourly", maximum: 20 },
        { "Retry-After": "1800" },
      ),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    expect(
      await screen.findByText(
        /limit of 20 submissions in one hour\. You can submit again in about 30 minutes\./,
      ),
    ).toBeVisible();
  });

  it("says plainly that a lifetime limit does not lift with time", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      NO_MATCHES,
      refusal(429, { detail: "Quota used.", limit: "lifetime", maximum: 5 }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const message = await screen.findByText(/all 5 submissions your account can make/);
    expect(message).toHaveTextContent("Waiting will not lift this limit.");
    expect(message).not.toHaveTextContent(/submit again in/);
  });

  it("shows the server's refusal to a user without permission to submit", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      {
        ...NO_MATCHES,
        status: 403,
        body: { detail: "You do not have permission to do this." },
      },
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    expect(
      await screen.findByText("You do not have permission to do this."),
    ).toBeVisible();
  });

  it("keeps the typed form when the terms must be accepted first", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      NO_MATCHES,
      refusal(403, {
        detail: "You need to accept the current terms of use before you can do this.",
        code: "terms_acceptance_required",
      }),
    ]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    expect(await screen.findByText(/Your answers are kept/)).toBeVisible();
    expect(screen.getByLabelText("Test name (required)")).toHaveValue("Serum ferritin");
    expect(screen.getByLabelText("Assay method (required)")).toHaveValue("Immunoassay");
  });
});

describe("SubmissionNewPage success", () => {
  it("confirms the submission and can start another with a clean form", async () => {
    stubApi([ME, DEFINITIONS, NO_MATCHES, CREATED]);
    const user = userEvent.setup();
    await renderPage();
    await fillRequired(user);

    await submit(user);

    const heading = await screen.findByRole("heading", {
      name: "Your test was submitted",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText(/Serum ferritin/)).toBeVisible();
    const confirmation = within(
      screen.getByRole("region", { name: "Your test was submitted" }),
    );
    expect(confirmation.getAllByRole("link")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Submit another test" }));

    expect(screen.getByLabelText("Test name (required)")).toHaveValue("");
    expect(screen.getByLabelText("Reference link (required)")).toHaveValue("");
  });
});

describe("SubmissionNewPage accessibility", () => {
  it("has no axe violations, with an error summary showing", async () => {
    stubApi([ME, DEFINITIONS]);
    const user = userEvent.setup();
    const { container } = await renderPage();
    await screen.findByLabelText("Assay method (required)");
    await expectNoA11yViolations(container);

    await submit(user);
    await screen.findByRole("heading", { name: "There is a problem" });

    await expectNoA11yViolations(container);
  });

  it("has no axe violations on the duplicate step", async () => {
    stubApi([
      ME,
      DEFINITIONS,
      { ...NO_MATCHES, body: { matches: [CATALOGUE_MATCH, OPEN_MATCH] } },
    ]);
    const user = userEvent.setup();
    const { container } = await renderPage();
    await fillRequired(user);

    await submit(user);
    await screen.findByRole("heading", { name: "This test may already exist" });

    await expectNoA11yViolations(container);
  });
});
