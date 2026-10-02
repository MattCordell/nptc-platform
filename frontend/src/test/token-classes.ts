import { expect } from "vitest";

const PALETTE_CLASS =
  /\b(?:bg|text|border|ring|outline|fill|stroke)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black)\b/;

/**
 * Asserts a class string takes its colour, border and elevation from design
 * tokens only: no hex value, no Tailwind palette class, no `shadow-*`. A
 * class-string check, because jsdom cannot resolve `var(--*)` to a computed
 * style (see status-badge.test.tsx).
 */
export function expectTokenClassesOnly(className: string): void {
  expect(className).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  expect(className).not.toMatch(PALETTE_CLASS);
  expect(className).not.toMatch(/\bshadow/);
}
