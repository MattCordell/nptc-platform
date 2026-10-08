import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The direct code route, `/catalogue/code/$systemToken/$code` (FR-17, FR-06,
 * NFR-31), driven through the real router and signed out.
 */

const KEY = "NPTC-000247";
// Past `Number.MAX_SAFE_INTEGER`: a coerced code would render differently.
const LONG_CODE = "900000000000003001";
const LEADING_ZEROS = "000123";
const NOT_FOUND_DETAIL =
  "No published catalogue entry matches this system and code. Registered code systems: sct (http://snomed.info/sct).";

function binding(overrides: Record<string, unknown>) {
  return {
    system: "http://snomed.info/sct",
    code: LONG_CODE,
    fsn: "Ferritin measurement (procedure)",
    au_preferred_term: "Ferritin level",
    edition_hint: "au",
    status: "active",
    retirement_reason: null,
    replaced_by_code: null,
    label_provenance: {},
    ...overrides,
  };
}

function entry(bindings: unknown[]) {
  return {
    business_key: KEY,
    preferred_term: "Ferritin",
    length: 8,
    status: "active",
    updated_at: "2026-09-01T12:00:00Z",
    has_open_finding: false,
    code: LONG_CODE,
    disciplines: [],
    label_provenance: {},
    row_version: 3,
    designations: [],
    bindings,
    properties: [],
  };
}

function lookupRoute(code: string, overrides: Partial<Route> = {}): Route {
  return {
    method: "GET",
    path: `/catalogue/code/sct/${code}`,
    status: 200,
    body: entry([binding({ code })]),
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderLookup(code: string, routes: Route[]) {
  const calls = stubApi(routes);
  const view = await renderRoute(`/catalogue/code/sct/${code}`);
  return { calls, ...view };
}

async function visible(text: string | RegExp) {
  const matches = await screen.findAllByText(text);
  const node = matches.find((candidate) => candidate.closest("[role=status]") === null);
  expect(node).toBeDefined();
  return node as HTMLElement;
}

async function expectAnnounced(
  text: string | RegExp,
  role: "status" | "alert" = "status",
) {
  await waitFor(() => {
    const spoken = screen
      .getAllByRole(role)
      .some((region) =>
        typeof text === "string"
          ? region.textContent === text
          : text.test(region.textContent ?? ""),
      );
    expect(spoken).toBe(true);
  });
}

describe("a code that an entry binds", () => {
  it("shows the entry, linked, with the code and its status", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE)]);

    const result = await screen.findByRole("region", { name: "Matching entry" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Code lookup");
    expect(await screen.findByRole("link", { name: "Ferritin" })).toHaveAttribute(
      "href",
      `/catalogue/${KEY}`,
    );
    expect(result).toHaveTextContent(KEY);
    expect(result).toHaveTextContent(LONG_CODE);
    expect(result).toHaveTextContent("Active");
    expect(result).not.toHaveTextContent("retired on this entry");
  });

  it("announces the match", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE)]);

    await expectAnnounced("Found the entry Ferritin.");
  });

  it("sets the document title", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE)]);

    await screen.findByRole("region", { name: "Matching entry" });
    await waitFor(() => expect(document.title).toBe("Code lookup — NPTC Catalogue"));
  });

  // FR-06: `JSON.parse` cannot tell a quoted code from a bare number, so the
  // test reads the raw URL the client sent, and the text the page rendered.
  it.each([LEADING_ZEROS, LONG_CODE])(
    "sends the code %s as text and shows it unchanged",
    async (code) => {
      const { calls } = await renderLookup(code, [lookupRoute(code)]);

      await screen.findByRole("region", { name: "Matching entry" });
      expect(calls.length).toBeGreaterThan(0);
      for (const call of calls) {
        expect(new URL(call.url).pathname).toBe(`/api/v1/catalogue/code/sct/${code}`);
      }
      expect(screen.getAllByText(code, { selector: "code" }).length).toBeGreaterThan(0);
    },
  );

  it("says so when the code is retired, and shows why and what replaced it", async () => {
    const retired = binding({
      code: LEADING_ZEROS,
      status: "retired",
      retirement_reason: "Superseded by a more specific concept",
      replaced_by_code: LONG_CODE,
    });
    await renderLookup(LEADING_ZEROS, [
      lookupRoute(LEADING_ZEROS, { body: entry([retired, binding({})]) }),
    ]);

    const result = await screen.findByRole("region", { name: "Matching entry" });
    expect(result).toHaveTextContent("Retired");
    expect(result).toHaveTextContent("This code is retired on this entry.");
    expect(result).toHaveTextContent("Superseded by a more specific concept");
    expect(result).toHaveTextContent(`Replaced by ${LONG_CODE}`);
  });

  it("reports the active binding when the entry bound the code, retired it and bound it again", async () => {
    const earlier = binding({
      status: "retired",
      retirement_reason: "Bound in error",
    });
    await renderLookup(LONG_CODE, [
      lookupRoute(LONG_CODE, { body: entry([earlier, binding({})]) }),
    ]);

    const result = await screen.findByRole("region", { name: "Matching entry" });
    expect(result).toHaveTextContent("Active");
    expect(result).not.toHaveTextContent("Retired");
  });

  it("still shows the entry when its bindings do not list the code", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE, { body: entry([]) })]);

    const result = await screen.findByRole("region", { name: "Matching entry" });
    expect(result).toHaveTextContent("Ferritin");
    expect(result).toHaveTextContent(LONG_CODE);
    expect(result).not.toHaveTextContent("Retired");
  });

  it("links back to the lookup form", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE)]);

    expect(
      await screen.findByRole("link", { name: "Look up another code" }),
    ).toHaveAttribute("href", "/catalogue/lookup");
  });
});

describe("a code that no entry binds", () => {
  it("shows the server's sentence under a clear heading", async () => {
    await renderLookup("999", [
      lookupRoute("999", { status: 404, body: { detail: NOT_FOUND_DETAIL } }),
    ]);

    expect(
      await screen.findByRole("region", { name: "No matching entry" }),
    ).toHaveTextContent(NOT_FOUND_DETAIL);
    expect(screen.queryByRole("link", { name: "Ferritin" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("announces the miss", async () => {
    await renderLookup("999", [
      lookupRoute("999", { status: 404, body: { detail: NOT_FOUND_DETAIL } }),
    ]);

    await expectAnnounced("No entry matches this code.");
  });

  it("falls back to its own sentence when the 404 carries no usable detail", async () => {
    await renderLookup("999", [lookupRoute("999", { status: 404, body: {} })]);

    expect(
      await screen.findByRole("region", { name: "No matching entry" }),
    ).toHaveTextContent("No published catalogue entry matches this system and code.");
  });

  it("keeps the code on screen so the user can check what they typed", async () => {
    await renderLookup("999", [lookupRoute("999", { status: 404, body: {} })]);

    await screen.findByRole("region", { name: "No matching entry" });
    expect(screen.getByText("999", { selector: "code" })).toBeInTheDocument();
  });
});

describe("a lookup the server refuses or cannot answer", () => {
  it("shows a different message for a refusal, and no retry that cannot help", async () => {
    await renderLookup("abc", [
      lookupRoute("abc", { status: 422, body: { detail: [{ msg: "bad" }] } }),
    ]);

    expect(
      await screen.findByRole("region", { name: "Code not accepted" }),
    ).toHaveTextContent("The catalogue does not accept this system or code.");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("shows a failure with Try again, and shows the entry once a retry works", async () => {
    const user = userEvent.setup();
    let recovered = false;
    stubApi([], {
      vary: ({ path }) =>
        path.endsWith(`/code/sct/${LONG_CODE}`)
          ? recovered
            ? lookupRoute(LONG_CODE)
            : { method: "GET", path, status: 500, body: { detail: "boom" } }
          : null,
    });
    await renderRoute(`/catalogue/code/sct/${LONG_CODE}`);

    expect(
      await screen.findByRole("region", { name: "Lookup failed" }),
    ).toHaveTextContent("This code could not be looked up. Try again in a moment.");
    await expectAnnounced(
      "This code could not be looked up. Try again in a moment.",
      "alert",
    );

    recovered = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("region", { name: "Matching entry" }),
    ).toHaveTextContent("Ferritin");
    expect(
      screen.queryByRole("region", { name: "Lookup failed" }),
    ).not.toBeInTheDocument();
  });

  it("shows a loading line while the answer is pending", async () => {
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE, { neverSettles: true })]);

    expect(await visible("Looking up the code…")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});

describe("accessibility (NFR-31)", () => {
  it("has no automated violations on a match", async () => {
    const { container } = await renderLookup(LONG_CODE, [
      lookupRoute(LONG_CODE, {
        body: entry([
          binding({ status: "retired", retirement_reason: "Bound in error" }),
        ]),
      }),
    ]);
    await screen.findByRole("region", { name: "Matching entry" });

    await expectNoA11yViolations(container);
  });

  it("has no violations on a miss", async () => {
    const { container } = await renderLookup("999", [
      lookupRoute("999", { status: 404, body: { detail: NOT_FOUND_DETAIL } }),
    ]);
    await screen.findByRole("region", { name: "No matching entry" });

    await expectNoA11yViolations(container);
  });

  it("has no violations on a failure", async () => {
    const { container } = await renderLookup(LONG_CODE, [
      lookupRoute(LONG_CODE, { status: 500, body: { detail: "boom" } }),
    ]);
    await screen.findByRole("region", { name: "Lookup failed" });

    await expectNoA11yViolations(container);
  });

  it("has no violations while the lookup is pending", async () => {
    const { container } = await renderLookup(LONG_CODE, [
      lookupRoute(LONG_CODE, { neverSettles: true }),
    ]);
    await visible("Looking up the code…");

    await expectNoA11yViolations(container);
  });

  it("has one banner, one main and one h1, and a keyboard reaches both links", async () => {
    const user = userEvent.setup();
    await renderLookup(LONG_CODE, [lookupRoute(LONG_CODE)]);
    await screen.findByRole("region", { name: "Matching entry" });

    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

    const entryLink = screen.getByRole("link", { name: "Ferritin" });
    const againLink = screen.getByRole("link", { name: "Look up another code" });
    entryLink.focus();
    expect(entryLink).toHaveFocus();
    await user.tab();
    expect(againLink).toHaveFocus();
  });
});
