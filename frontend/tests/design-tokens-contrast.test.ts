// Lives outside `src` and in `tsconfig.node.json`'s program instead - see
// `fr-83-no-semantic-tag-stripping.test.ts`'s own note on why: a Vitest
// source-scanning guard is not app code, and keeping it outside `src`
// keeps Node's ambient globals off the app's own typecheck.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Verifies the actual `--color-*` hex values declared in app.css, not a
 * rendered component (PR #327 review): jsdom cannot compute a colour from a
 * CSS custom property via `getComputedStyle`, so a component test asserting
 * `className` contains `text-[var(--color-status-draft-text)]` can only ever
 * prove that class string is present - it says nothing about whether that
 * token's value is contrast-safe. This file is the one place that claim is
 * actually checked, following NFR-31 now that `frontend/src/test/a11y.ts`
 * disables axe's `color-contrast` rule under jsdom and issue #211's manual
 * pass is cancelled - see docs/architecture/components.md.
 */

const APP_CSS_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "src",
  "styles",
  "app.css",
);
const appCss = readFileSync(APP_CSS_PATH, "utf-8");

function tokenValue(name: string): string {
  const match = appCss.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) {
    throw new Error(`Token --${name} not found in app.css`);
  }
  return match[1];
}

function srgbToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

/** WCAG 2.x contrast ratio (1 to 21) between two colours. */
function contrastRatio(hexA: string, hexB: string): number {
  const lighter = Math.max(relativeLuminance(hexA), relativeLuminance(hexB));
  const darker = Math.min(relativeLuminance(hexA), relativeLuminance(hexB));
  return (lighter + 0.05) / (darker + 0.05);
}

// WCAG AA for normal-weight text under 18.66px (StatusBadge and
// --color-text-tertiary are both drafted for 12-13px use).
const AA_NORMAL_TEXT = 4.5;

describe("design tokens meet WCAG AA contrast", () => {
  it.each(["draft", "active", "deprecated", "neutral"] as const)(
    "the %s status pair reaches 4.5:1",
    (tone) => {
      const text = tokenValue(`color-status-${tone}-text`);
      const bg = tokenValue(`color-status-${tone}-bg`);
      expect(contrastRatio(text, bg)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    },
  );

  it("--color-text-tertiary reaches 4.5:1 on --color-surface", () => {
    const text = tokenValue("color-text-tertiary");
    const surface = tokenValue("color-surface");
    expect(contrastRatio(text, surface)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });
});
