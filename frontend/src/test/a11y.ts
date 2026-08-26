import axe from "axe-core";
import { expect } from "vitest";

/**
 * Runs axe-core against a rendered subtree and fails the test with the
 * violation ids, impact, and affected node targets if any are found - one
 * assertion per component test (issue #148), not a spot check.
 *
 * `color-contrast` is disabled: axe evaluates it against computed layout,
 * which jsdom does not render, so the rule would either report nothing
 * useful or false-fail on every element. Contrast is instead carried by the
 * `--color-*` tokens in `src/styles/app.css` and confirmed in the P5 manual
 * keyboard/screen-reader pass (NFR-31) - see docs/architecture/components.md.
 */
export async function expectNoA11yViolations(container: Element): Promise<void> {
  const results = await axe.run(container, {
    rules: {
      "color-contrast": { enabled: false },
    },
  });

  if (results.violations.length > 0) {
    const summary = results.violations
      .map((violation) => {
        const targets = violation.nodes.map((node) => node.target.join(" ")).join(", ");
        return `- [${violation.impact ?? "unknown"}] ${violation.id}: ${violation.help} (${targets})`;
      })
      .join("\n");
    expect.fail(`axe-core found ${results.violations.length} violation(s):\n${summary}`);
  }
}
