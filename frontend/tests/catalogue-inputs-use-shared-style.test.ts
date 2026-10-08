// A Vitest source-scanning guard, not app code - see
// `fr-83-no-semantic-tag-stripping.test.ts`'s header comment for why it lives
// outside `src`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * Every text-like `<input>` under `src/catalogue` carries
 * `className={INPUT_CLASSES}` (docs/architecture/components.md), so the edit
 * screens keep the radius, border and text size of the landing page's search
 * box. A render test can only cover the controls it mounts; this walks the
 * source, so an input added or edited later cannot drop the class unnoticed.
 *
 * Checkboxes and radios are excluded: `Checkbox` and `RadioGroup` own their
 * look. An input whose `type` is not a plain string literal is treated as
 * text-like, so a computed type cannot be used to skip the check.
 */

const CATALOGUE_ROOT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../src/catalogue",
);

const NOT_TEXT_LIKE_TYPES = new Set(["checkbox", "radio"]);

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      files.push(...collectSourceFiles(full));
    } else if (/\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

/** The text-like `<input>` elements in `source` that lack `className={INPUT_CLASSES}`, by line. */
function inputsWithoutSharedStyle(source: string): number[] {
  const sourceFile = ts.createSourceFile(
    "scanned.tsx",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const lines: number[] = [];

  function checkAttributes(node: ts.Node, tagName: string, attributes: ts.JsxAttributes) {
    if (tagName !== "input") {
      return;
    }
    const named = (name: string) =>
      attributes.properties.find(
        (prop): prop is ts.JsxAttribute =>
          ts.isJsxAttribute(prop) && prop.name.getText(sourceFile) === name,
      );
    const type = named("type")?.initializer;
    if (type && ts.isStringLiteral(type) && NOT_TEXT_LIKE_TYPES.has(type.text)) {
      return;
    }
    const className = named("className")?.initializer;
    const usesSharedStyle =
      className !== undefined &&
      ts.isJsxExpression(className) &&
      className.expression !== undefined &&
      ts.isIdentifier(className.expression) &&
      className.expression.text === "INPUT_CLASSES";
    if (!usesSharedStyle) {
      lines.push(sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1);
    }
  }

  function visit(node: ts.Node) {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      checkAttributes(node, node.tagName.getText(sourceFile), node.attributes);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return lines;
}

describe("catalogue inputs use the shared text-input style", () => {
  it("flags a bare text input, so this guard can actually fail", () => {
    expect(inputsWithoutSharedStyle(`<input type="text" value="" />`)).toEqual([1]);
  });

  it("flags an input with some other className", () => {
    expect(inputsWithoutSharedStyle(`<input className="w-full" type="text" />`)).toEqual([
      1,
    ]);
  });

  it("flags an input whose type is not a plain literal", () => {
    expect(inputsWithoutSharedStyle(`<input type={kind} />`)).toEqual([1]);
  });

  it("accepts an input that uses INPUT_CLASSES", () => {
    expect(
      inputsWithoutSharedStyle(`<input className={INPUT_CLASSES} type="number" />`),
    ).toEqual([]);
  });

  it("does not flag a checkbox or a radio", () => {
    expect(
      inputsWithoutSharedStyle(`<><input type="checkbox" /><input type="radio" /></>`),
    ).toEqual([]);
  });

  it("does not flag a comment or string that mentions an input", () => {
    expect(
      inputsWithoutSharedStyle(
        `// <input type="text" />\nconst note = '<input type="text" />';`,
      ),
    ).toEqual([]);
  });

  const files = collectSourceFiles(CATALOGUE_ROOT);

  it("found .tsx files to walk", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files)("%s", (file) => {
    const lines = inputsWithoutSharedStyle(readFileSync(file, "utf-8"));
    expect(
      lines,
      `${relative(CATALOGUE_ROOT, file)} has a text-like <input> without className={INPUT_CLASSES} at line(s) ${lines.join(", ")}`,
    ).toEqual([]);
  });
});
