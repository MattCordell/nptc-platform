import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/**
 * The property registry detail screen (FR-08..13, FR-77, NFR-31), driven
 * through the real route.
 */

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

function definition(overrides: Record<string, unknown>) {
  return {
    key: "usage_guidance",
    label: "Usage guidance",
    datatype: "string",
    cardinality: "0..1",
    scope: "maintenance",
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
    display_order: 40,
    constraints: {},
    row_version: 1,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const SPECIMEN = definition({
  key: "specimen",
  label: "Specimen",
  datatype: "code",
  cardinality: "0..*",
  scope: "both",
  required_for_publication: true,
  binding_target: "value_set",
  value_set_uri: "http://snomed.info/sct?fhir_vs=ecl/%3C123038009",
  strength: "required",
  edition: "au",
  filterable: true,
  display_order: 30,
  constraints: { forbidden_codes: ["Any"] },
  form_control: { control: "concept_picker", params: {} },
});

function stubDefinition(key: string, body: unknown, status = 200) {
  stubApi([{ method: "GET", path: `/registry/properties/${key}`, status, body }]);
}

function termValue(term: string): HTMLElement {
  const dt = screen.getByText(term, { selector: "dt" });
  return dt.nextElementSibling as HTMLElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminPropertyDetailPage", () => {
  it("shows the whole definition of a coded property", async () => {
    stubDefinition("specimen", SPECIMEN);

    await renderRoute("/admin/properties/specimen", SIGNED_IN);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Specimen" }),
    ).toBeVisible();
    expect(termValue("Key")).toHaveTextContent("specimen");
    expect(termValue("Datatype")).toHaveTextContent("code");
    expect(termValue("Cardinality")).toHaveTextContent("Zero or more");
    expect(termValue("Scope")).toHaveTextContent("Both");
    expect(termValue("Status")).toHaveTextContent("Active");
    expect(termValue("Origin")).toHaveTextContent("System");
    expect(termValue("Required for submission")).toHaveTextContent("No");
    expect(termValue("Required for publication")).toHaveTextContent("Yes");
    expect(termValue("Used as a catalogue filter")).toHaveTextContent("Yes");
    expect(termValue("Display order")).toHaveTextContent("30");
    expect(termValue("Data-entry control")).toHaveTextContent("concept_picker");
  });

  it("shows the binding and lists constraints without interpreting them", async () => {
    stubDefinition("specimen", SPECIMEN);

    await renderRoute("/admin/properties/specimen", SIGNED_IN);

    const binding = (await screen.findByRole("heading", { name: "Terminology binding" }))
      .parentElement as HTMLElement;
    expect(within(binding).getByText("Bound to", { selector: "dt" })).toBeInTheDocument();
    expect(
      within(binding).getByText("Value set", { selector: "dt" }),
    ).toBeInTheDocument();
    // The URI is a string end to end and is shown verbatim (FR-06).
    expect(
      within(binding).getByText("http://snomed.info/sct?fhir_vs=ecl/%3C123038009"),
    ).toBeInTheDocument();
    expect(within(binding).getByText("Required")).toBeInTheDocument();
    expect(within(binding).getByText("au")).toBeInTheDocument();

    const constraints = screen.getByRole("heading", { name: "Constraints" })
      .parentElement as HTMLElement;
    expect(within(constraints).getByText("forbidden_codes")).toBeInTheDocument();
    expect(within(constraints).getByText('["Any"]')).toBeInTheDocument();
  });

  it("omits the binding and constraints sections when the property has neither", async () => {
    stubDefinition("usage_guidance", definition({}));

    await renderRoute("/admin/properties/usage_guidance", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Usage guidance" });
    expect(
      screen.queryByRole("heading", { name: "Terminology binding" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Constraints" }),
    ).not.toBeInTheDocument();
  });

  it("shows only the binding rows that are present", async () => {
    stubDefinition(
      "discipline",
      definition({
        key: "discipline",
        label: "Discipline",
        datatype: "code",
        binding_target: "local_code_system",
        local_code_system_key: "discipline",
        strength: "required",
      }),
    );

    await renderRoute("/admin/properties/discipline", SIGNED_IN);

    await screen.findByRole("heading", { name: "Terminology binding" });
    expect(screen.getByText("Local code system", { selector: "dt" })).toBeInTheDocument();
    expect(screen.queryByText("Value set", { selector: "dt" })).not.toBeInTheDocument();
    expect(screen.queryByText("Edition", { selector: "dt" })).not.toBeInTheDocument();
  });

  it("shows a deprecated property with the word Deprecated", async () => {
    stubDefinition(
      "legacy_flag",
      definition({ key: "legacy_flag", status: "deprecated" }),
    );

    await renderRoute("/admin/properties/legacy_flag", SIGNED_IN);

    await screen.findByRole("heading", { level: 1 });
    expect(within(termValue("Status")).getByText("Deprecated")).toBeVisible();
  });

  it("renders a datatype it has never heard of as plain text", async () => {
    stubDefinition(
      "dose",
      definition({
        key: "dose",
        label: "Dose",
        datatype: "quantity",
        constraints: { unit: "mg", minimum: 1 },
      }),
    );

    await renderRoute("/admin/properties/dose", SIGNED_IN);

    expect(await screen.findByRole("heading", { level: 1, name: "Dose" })).toBeVisible();
    expect(termValue("Datatype")).toHaveTextContent("quantity");
    expect(termValue("unit")).toHaveTextContent("mg");
    expect(termValue("minimum")).toHaveTextContent("1");
  });

  it("links back to the registry list, reachable by keyboard", async () => {
    stubApi([
      {
        method: "GET",
        path: "/registry/properties/specimen",
        status: 200,
        body: SPECIMEN,
      },
      {
        method: "GET",
        path: "/registry/properties",
        status: 200,
        body: { items: [SPECIMEN] },
      },
    ]);
    const user = userEvent.setup();

    const { router } = await renderRoute("/admin/properties/specimen", SIGNED_IN);
    const back = await screen.findByRole("link", {
      name: "Back to the property registry",
    });

    expect(back).toHaveAttribute("href", "/admin/properties");
    // The skip link and site navigation come first in the tab order.
    for (let presses = 0; presses < 25 && document.activeElement !== back; presses += 1) {
      await user.tab();
    }
    expect(back).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(router.state.location.pathname).toMatch(/^\/admin\/properties\/?$/),
    );
    expect(
      await screen.findByRole("heading", { level: 1, name: "Property registry" }),
    ).toBeVisible();
  });

  it("names the key and the next step when the property does not exist", async () => {
    stubDefinition(
      "missing",
      { detail: "No property definition matches the given key." },
      404,
    );

    await renderRoute("/admin/properties/missing", SIGNED_IN);

    expect(
      await screen.findByText("No property was found for missing. Check the key."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Definition" })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "No property was found for missing. Check the key.",
      ),
    );
  });

  it("shows the server's refusal when the caller lacks registry.read", async () => {
    stubDefinition(
      "specimen",
      { detail: "You do not have permission to view the property registry." },
      403,
    );

    await renderRoute("/admin/properties/specimen", SIGNED_IN);

    expect(
      await screen.findByText(
        "You do not have permission to view the property registry.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Definition" })).not.toBeInTheDocument();
  });

  it("has exactly one h1 and no automated accessibility violations", async () => {
    stubDefinition("specimen", SPECIMEN);

    const { container } = await renderRoute("/admin/properties/specimen", SIGNED_IN);
    await screen.findByRole("heading", { name: "Terminology binding" });

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);
  });
});
