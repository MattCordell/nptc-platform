import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

/**
 * Runs axe-core in the real browser with `color-contrast` enabled and fails
 * on any violation (NFR-31). The jsdom helper in `src/test/a11y.ts` has to
 * disable that rule because jsdom computes no layout; this is the run that
 * closes the gap, so it keeps axe's default rule set and disables nothing.
 */
export async function expectNoA11yViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .options({ rules: { "color-contrast": { enabled: true } } })
    .analyze();

  if (results.violations.length > 0) {
    const summary = results.violations
      .map((violation) => {
        const targets = violation.nodes.map((node) => node.target.join(" ")).join(", ");
        return `- [${violation.impact ?? "unknown"}] ${violation.id}: ${violation.help} (${targets})`;
      })
      .join("\n");
    throw new Error(
      `axe-core found ${results.violations.length} violation(s):\n${summary}`,
    );
  }
}
