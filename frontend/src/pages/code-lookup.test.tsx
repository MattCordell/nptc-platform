import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The lookup form, `/catalogue/lookup` (FR-17, FR-06, NFR-31), driven through
 * the real router and signed out.
 */

const KEY = "NPTC-000247";
const LEADING_ZEROS = "000123";
const SYSTEM_URI = "http://snomed.info/sct";
const NOT_FOUND_DETAIL =
  "No published catalogue entry matches this system and code. Registered code systems: sct (http://snomed.info/sct).";

function entry(code: string) {
  return {
    business_key: KEY,
    preferred_term: "Ferritin",
    length: 8,
    status: "active",
    updated_at: "2026-09-01T12:00:00Z",
    has_open_finding: false,
    code,
    disciplines: [],
    label_provenance: {},
    row_version: 3,
    designations: [],
    bindings: [
      {
        system: SYSTEM_URI,
        code,
        fsn: "Ferritin measurement (procedure)",
        au_preferred_term: null,
        edition_hint: "au",
        status: "active",
        retirement_reason: null,
        replaced_by_code: null,
        label_provenance: {},
      },
    ],
    properties: [],
  };
}

const BY_TOKEN: Route = {
  method: "GET",
  path: `/catalogue/code/sct/${LEADING_ZEROS}`,
  status: 200,
  body: entry(LEADING_ZEROS),
};

const BY_URI: Route = {
  method: "GET",
  path: "/catalogue/lookup",
  status: 200,
  body: entry(LEADING_ZEROS),
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function lookupUrl(system: string, code: string) {
  return `/catalogue/lookup?system=${encodeURIComponent(system)}&code=${encodeURIComponent(code)}`;
}

describe("the form", () => {
  it("has one h1, a labelled system select and a labelled code field", async () => {
    stubApi([]);
    await renderRoute("/catalogue/lookup");

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Code lookup");
    expect(screen.getByRole("combobox", { name: "Code system" })).toHaveValue("sct");
    expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue("");
    expect(screen.getByRole("form", { name: "Look up a code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Look up" })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Code lookup — NPTC Catalogue"));
  });

  it("offers SNOMED CT as the only system", async () => {
    stubApi([]);
    await renderRoute("/catalogue/lookup");

    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["SNOMED CT"]);
  });

  it("makes no request until the user submits", async () => {
    const calls = stubApi([]);
    await renderRoute("/catalogue/lookup");

    expect(calls).toHaveLength(0);
  });
});

describe("submitting a code", () => {
  // FR-06: the form must not turn "000123" into 123 on the way to the path.
  it("goes to the code route with the leading zeros kept", async () => {
    const user = userEvent.setup();
    const calls = stubApi([BY_TOKEN]);
    const { router } = await renderRoute("/catalogue/lookup");

    await user.type(screen.getByRole("textbox", { name: "Code" }), LEADING_ZEROS);
    await user.click(screen.getByRole("button", { name: "Look up" }));

    expect(
      await screen.findByRole("region", { name: "Matching entry" }),
    ).toHaveTextContent("Ferritin");
    expect(router.state.location.pathname).toBe(`/catalogue/code/sct/${LEADING_ZEROS}`);
    for (const call of calls) {
      expect(new URL(call.url).pathname).toBe(
        `/api/v1/catalogue/code/sct/${LEADING_ZEROS}`,
      );
    }
  });

  it("trims spaces around the code", async () => {
    const user = userEvent.setup();
    stubApi([BY_TOKEN]);
    const { router } = await renderRoute("/catalogue/lookup");

    await user.type(screen.getByRole("textbox", { name: "Code" }), `  ${LEADING_ZEROS} `);
    await user.click(screen.getByRole("button", { name: "Look up" }));

    await screen.findByRole("region", { name: "Matching entry" });
    expect(router.state.location.pathname).toBe(`/catalogue/code/sct/${LEADING_ZEROS}`);
  });

  it("can be completed with the keyboard alone", async () => {
    const user = userEvent.setup();
    stubApi([BY_TOKEN]);
    const { router } = await renderRoute("/catalogue/lookup");

    const select = screen.getByRole("combobox", { name: "Code system" });
    const input = screen.getByRole("textbox", { name: "Code" });
    select.focus();
    await user.tab();
    expect(input).toHaveFocus();
    await user.keyboard(LEADING_ZEROS);
    await user.keyboard("{Enter}");

    await screen.findByRole("region", { name: "Matching entry" });
    expect(router.state.location.pathname).toBe(`/catalogue/code/sct/${LEADING_ZEROS}`);
  });
});

describe("submitting nothing", () => {
  it("lists the problem in a summary, names it on the field, and sends no request", async () => {
    const user = userEvent.setup();
    const calls = stubApi([]);
    const { router } = await renderRoute("/catalogue/lookup");

    await user.click(screen.getByRole("button", { name: "Look up" }));

    const summary = await screen.findByRole("heading", {
      level: 2,
      name: "Enter a code to look up",
    });
    expect(summary.parentElement).toHaveFocus();
    const input = screen.getByRole("textbox", { name: "Code" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Enter a code\./);
    expect(calls).toHaveLength(0);
    expect(router.state.location.pathname).toBe("/catalogue/lookup");
  });

  it("treats a code of spaces as blank", async () => {
    const user = userEvent.setup();
    const calls = stubApi([]);
    await renderRoute("/catalogue/lookup");

    await user.type(screen.getByRole("textbox", { name: "Code" }), "   ");
    await user.click(screen.getByRole("button", { name: "Look up" }));

    await screen.findByRole("heading", { level: 2, name: "Enter a code to look up" });
    expect(calls).toHaveLength(0);
  });

  it("moves focus to the code field from the summary link", async () => {
    const user = userEvent.setup();
    stubApi([]);
    await renderRoute("/catalogue/lookup");

    await user.click(screen.getByRole("button", { name: "Look up" }));
    await user.click(await screen.findByRole("link", { name: "Enter a code." }));

    expect(screen.getByRole("textbox", { name: "Code" })).toHaveFocus();
  });
});

describe("an address that carries a system URI and a code", () => {
  it("looks the code up under the form, with the leading zeros kept", async () => {
    const calls = stubApi([BY_URI]);
    const { router } = await renderRoute(lookupUrl(SYSTEM_URI, LEADING_ZEROS));

    expect(
      await screen.findByRole("region", { name: "Matching entry" }),
    ).toHaveTextContent("Ferritin");
    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.searchParams.get("code")).toBe(LEADING_ZEROS);
      expect(call.searchParams.get("system")).toBe(SYSTEM_URI);
      expect(call.url).toContain(`code=${LEADING_ZEROS}`);
    }
    expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue(LEADING_ZEROS);
    expect(router.state.location.pathname).toBe("/catalogue/lookup");
  });

  it("follows a move to another address on the same route", async () => {
    stubApi([BY_URI]);
    const { router } = await renderRoute(lookupUrl(SYSTEM_URI, LEADING_ZEROS));
    await screen.findByRole("region", { name: "Matching entry" });
    expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue(LEADING_ZEROS);

    await act(async () => {
      await router.navigate({
        to: "/catalogue/lookup",
        search: { system: SYSTEM_URI, code: "0042" },
      });
    });

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue("0042"),
    );
  });

  it("says no entry matches when the server answers 404", async () => {
    stubApi([{ ...BY_URI, status: 404, body: { detail: NOT_FOUND_DETAIL } }]);
    await renderRoute(lookupUrl("http://example.org/unknown", "42"));

    expect(
      await screen.findByRole("region", { name: "No matching entry" }),
    ).toHaveTextContent(NOT_FOUND_DETAIL);
  });

  it("makes no request when the address has a code but no system", async () => {
    const calls = stubApi([BY_URI]);
    await renderRoute(`/catalogue/lookup?code=${LEADING_ZEROS}`);

    expect(screen.getByRole("textbox", { name: "Code" })).toHaveValue(LEADING_ZEROS);
    expect(calls).toHaveLength(0);
    expect(
      screen.queryByRole("region", { name: /matching entry/i }),
    ).not.toBeInTheDocument();
  });

  it("makes no request when the address has a system but no code", async () => {
    const calls = stubApi([BY_URI]);
    await renderRoute(`/catalogue/lookup?system=${encodeURIComponent(SYSTEM_URI)}`);

    expect(calls).toHaveLength(0);
  });
});

describe("accessibility (NFR-31)", () => {
  it("has no automated violations on the empty form", async () => {
    stubApi([]);
    const { container } = await renderRoute("/catalogue/lookup");

    await expectNoA11yViolations(container);
  });

  it("has no violations with the error summary showing", async () => {
    const user = userEvent.setup();
    stubApi([]);
    const { container } = await renderRoute("/catalogue/lookup");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { level: 2, name: "Enter a code to look up" });

    await expectNoA11yViolations(container);
  });

  it("has no violations with a result under the form", async () => {
    stubApi([BY_URI]);
    const { container } = await renderRoute(lookupUrl(SYSTEM_URI, LEADING_ZEROS));
    await screen.findByRole("region", { name: "Matching entry" });

    await expectNoA11yViolations(container);
  });

  it("has one banner, one main and one h1 with a result showing", async () => {
    stubApi([BY_URI]);
    await renderRoute(lookupUrl(SYSTEM_URI, LEADING_ZEROS));
    await screen.findByRole("region", { name: "Matching entry" });

    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
