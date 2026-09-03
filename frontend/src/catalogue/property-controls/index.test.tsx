import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../../auth/session.ts";
import { expectNoA11yViolations } from "../../test/a11y.ts";
import { CONTROLS, RepeatableValues } from "./index.ts";
import type { ControlKind, PropertyValueSlot } from "./index.ts";

const AUTH: AuthContextValue = {
  status: "signed-in",
  getAccessToken: () => Promise.resolve("test-token"),
  signIn: () => Promise.resolve(),
  signOut: () => Promise.resolve(),
  register: () => Promise.resolve(),
  restore: () => Promise.resolve(),
  completeCallback: () => Promise.resolve(null),
};

function withProviders(children: ReactNode) {
  const queryClient = createQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

/**
 * The control registry (ADR-0013 SS3, issue #151) and the cardinality
 * wrapper around it.
 *
 * `CONTROLS` itself is proven exhaustive by TypeScript (a `ControlKind`
 * missing a row is a `tsc -b` error), so these tests exercise behaviour: each
 * control renders something, reports a value through `onChange`, and the
 * wrapper adds/removes slots correctly for single vs. multi cardinality.
 */

function stubFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(["text", "textarea", "number", "uri"] as ControlKind[])(
  "CONTROLS.%s",
  (kind) => {
    it("renders a labelled control and reports a change", async () => {
      stubFetch(200, { items: [], total: 0 });
      const user = userEvent.setup();
      const Control = CONTROLS[kind];
      const onChange = vi.fn();

      const { container } = render(
        withProviders(
          <Control
            id="prop-0"
            label="Usage guidance"
            propertyKey="usage_guidance"
            params={{}}
            value={null}
            onChange={onChange}
          />,
        ),
      );

      const control = screen.getByLabelText("Usage guidance");
      await user.type(control, kind === "number" ? "3" : "x");
      expect(onChange).toHaveBeenCalled();
      await expectNoA11yViolations(container);
    });
  },
);

describe("CONTROLS.concept_picker", () => {
  it("offers the currently held code even when it is outside the fetched list", async () => {
    stubFetch(200, { items: [{ code: "119361006", display: "Plasma specimen" }], total: 1 });
    const Control = CONTROLS.concept_picker;

    render(
      withProviders(
        <Control
          id="specimen-0"
          label="Specimen"
          propertyKey="specimen"
          params={{ valueSetUri: "http://snomed.info/sct?fhir_vs=ecl/%3C123038009" }}
          value="122554006"
          onChange={vi.fn()}
        />,
      ),
    );

    const select = (await screen.findByLabelText("Specimen")) as HTMLSelectElement;
    expect(
      Array.from(select.options).some((option) => option.value === "122554006"),
    ).toBe(true);
  });

  it("reports the chosen code through onChange", async () => {
    stubFetch(200, { items: [{ code: "119361006", display: "Plasma specimen" }], total: 1 });
    const user = userEvent.setup();
    const onChange = vi.fn();
    const Control = CONTROLS.concept_picker;

    render(
      withProviders(
        <Control
          id="specimen-0"
          label="Specimen"
          propertyKey="specimen"
          params={{}}
          value={null}
          onChange={onChange}
        />,
      ),
    );

    const select = await screen.findByLabelText("Specimen");
    await screen.findByRole("option", { name: /119361006/ });
    await user.selectOptions(select, "119361006");
    expect(onChange).toHaveBeenCalledWith("119361006");
  });
});

function ControlledRepeatable({
  cardinality,
  initial = [],
}: {
  cardinality: "0..1" | "1..1" | "0..*" | "1..*";
  initial?: PropertyValueSlot[];
}) {
  const [slots, setSlots] = useState<PropertyValueSlot[]>(initial);
  return (
    <RepeatableValues
      propertyKey="discipline"
      label="Discipline"
      cardinality={cardinality}
      control={CONTROLS.text}
      params={{}}
      slots={slots}
      onChange={setSlots}
      errors={[]}
    />
  );
}

describe("RepeatableValues", () => {
  it("shows one empty control for a single-valued property with no recorded value", () => {
    render(<ControlledRepeatable cardinality="0..1" />);

    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add another/i })).not.toBeInTheDocument();
  });

  it("offers Add/Remove for a multi-valued property, and never for a single-valued one", async () => {
    const user = userEvent.setup();
    render(
      <ControlledRepeatable
        cardinality="0..*"
        initial={[{ value: "HAEM", justification: null }]}
      />,
    );

    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: /add another/i }));
    expect(screen.getAllByRole("textbox")).toHaveLength(2);

    await user.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
  });

  it("labels each slot of a multi-valued property with its own ordinal", () => {
    render(
      <ControlledRepeatable
        cardinality="1..*"
        initial={[
          { value: "119361006", justification: null },
          { value: "122554006", justification: null },
        ]}
      />,
    );

    expect(screen.getByLabelText("Discipline 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Discipline 2")).toBeInTheDocument();
  });
});
