import { expect } from "vitest";

const COLOUR_UTILITIES =
  "bg|text|border(?:-[xytrblse])?|ring|ring-offset|outline|fill|stroke|divide|from|via|to|placeholder|accent|caret|decoration";

const PALETTE_CLASS = new RegExp(
  `\\b(?:${COLOUR_UTILITIES})-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black)\\b`,
);

const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/;

/**
 * Asserts a class string takes its colour, border and elevation from design
 * tokens only: no hex or colour-function value, no Tailwind palette class, no
 * `shadow-*`. A class-string check, because jsdom cannot resolve `var(--*)` to
 * a computed style (see status-badge.test.tsx).
 */
export function expectTokenClassesOnly(className: string): void {
  expect(className).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  expect(className).not.toMatch(COLOUR_FUNCTION);
  expect(className).not.toMatch(PALETTE_CLASS);
  expect(className).not.toMatch(/\bshadow/);
}
