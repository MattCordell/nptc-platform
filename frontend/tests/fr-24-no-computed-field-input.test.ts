// See `fr-83-no-semantic-tag-stripping.test.ts`'s own header comment for why
// this file lives outside `src` (a Vitest source-scanning guard, not app
// code - `tsconfig.node.json`'s program, not `tsconfig.app.json`'s).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * FR-24: a computed field is never an editable input, anywhere this
 * frontend renders one. `test_length_submitted_on_an_amendment_is_ignored_
 * not_stored` (backend/tests/test_api_catalogue_designations.py) proves the
 * *server* ignores a caller-supplied `length` - this is the frontend half:
 * no form control ever offers one as something to type into in the first
 * place.
 *
 * **Keyed on form controls, not on banning the identifier outright** (issue
 * #61's plan, unlike `fr-83-no-semantic-tag-stripping.test.ts`'s approach for
 * `display_term`). `display_term` has exactly one legitimate home
 * (`api/schema.ts`, allow-listed there); `length` does not - it is
 * overloaded across the frontend (`terms.length`, `columns.length`,
 * `page.items.length`, array/string `.length` throughout) and cannot be
 * banned as a bare identifier without flagging code that has nothing to do
 * with this screen's computed field. The one place this screen actually
 * *renders* the computed length is a plain `<dd>{entry.data.length}</dd>`
 * (`admin-catalogue-edit.tsx`) - a property read, not a form control - and
 * this guard must not flag it.
 *
 * So the walk looks specifically for a JSX form control - `input`,
 * `textarea`, `select`, or this codebase's own `Field` wrapper
 * (`components/field.tsx`, which carries the control's `id` as its own JSX
 * attribute rather than on the native element - see `designations-panel.tsx`'s
 * `<Field id="add-terms" .../>` for the pattern) - whose `id` or `name`
 * attribute names a computed field. An AST walk, not a text search: a panel
 * docstring is free to *name* these fields in prose (several already do,
 * explaining why there is no control for them), and `ts.forEachChild` does
 * not descend into comment trivia, so mentioning one there cannot trip this.
 */

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../src");

//: The computed fields FR-24 covers for this screen set (issue #61's plan):
//: `length` (FR-85, the preferred term's published character count),
//: `fsn`/`au_preferred_term` (bound from the terminology server, FR-06/FR-82
//: - a code binding's own two read-only fields, not editor input), `display_
//: term` (FR-83's server-stripped value, already banned outright by the
//: FR-83 guard - named here too since it is also never a form control's
//: `id`/`name`), and `row_version`/`version`/`history` (concurrency and
//: audit bookkeeping, never something an editor types).
const COMPUTED_FIELD_NAMES = new Set([
  "length",
  "fsn",
  "au_preferred_term",
  "display_term",
  "row_version",
  "version",
  "history",
]);

//: Tag names this guard treats as a form control. Lower-case native
//: elements plus this codebase's own `Field` wrapper - see the module
//: docstring for why `Field` has to be included alongside the natives it
//: wraps.
const FORM_CONTROL_TAGS = new Set(["input", "textarea", "select", "Field"]);

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      files.push(...collectSourceFiles(full));
      continue;
    }
    if (/\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

/**
 * The computed-field names found on an `id`/`name` attribute of a form
 * control (`FORM_CONTROL_TAGS`) anywhere in `source`. Walks both a
 * self-closing form control (`<input id="length" />`) and an opening tag of
 * one with children (`<Field id="length">...</Field>`) - `ts.forEachChild`
 * still reaches both shapes' attributes either way, but the two are
 * distinct node kinds in the TypeScript AST and each needs its own
 * `isJsxAttributes`-bearing parent checked explicitly.
 */
function computedFieldInputs(source: string, filePath: string): Set<string> {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const found = new Set<string>();

  function checkAttributes(tagName: string, attributes: ts.JsxAttributes) {
    if (!FORM_CONTROL_TAGS.has(tagName)) {
      return;
    }
    for (const prop of attributes.properties) {
      if (!ts.isJsxAttribute(prop)) {
        continue;
      }
      const attrName = prop.name.getText(sourceFile);
      if (attrName !== "id" && attrName !== "name") {
        continue;
      }
      const literal = stringLiteralValue(prop.initializer);
      if (literal !== null && COMPUTED_FIELD_NAMES.has(literal)) {
        found.add(literal);
      }
    }
  }

  /**
   * The literal string an `id`/`name` attribute's initializer holds, or
   * `null` if it is not a plain string. Handles both JSX attribute value
   * shapes - `id="length"` (the initializer is a `StringLiteral` node
   * directly, every real instance in this codebase today) and `id={"length"}`
   * (the initializer is a `JsxExpression` wrapping one) - so a future control
   * built from a template rather than a bare attribute string cannot slip
   * past this guard on syntax alone. Anything else (a variable, a template
   * literal with substitutions, a ternary) is not a literal this guard can
   * resolve and is left unflagged, same as `fr-83-no-semantic-tag-
   * stripping.test.ts`'s own identifier-only walk leaves a computed name
   * unflagged.
   */
  function stringLiteralValue(
    initializer: ts.JsxAttribute["initializer"],
  ): string | null {
    if (!initializer) {
      return null;
    }
    if (ts.isStringLiteral(initializer)) {
      return initializer.text;
    }
    if (ts.isJsxExpression(initializer) && initializer.expression) {
      const expression = initializer.expression;
      if (
        ts.isStringLiteral(expression) ||
        ts.isNoSubstitutionTemplateLiteral(expression)
      ) {
        return expression.text;
      }
    }
    return null;
  }

  function visit(node: ts.Node) {
    if (ts.isJsxSelfClosingElement(node)) {
      checkAttributes(node.tagName.getText(sourceFile), node.attributes);
    } else if (ts.isJsxOpeningElement(node)) {
      checkAttributes(node.tagName.getText(sourceFile), node.attributes);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

describe("FR-24: no form control on the entry-edit screens offers a computed field as input", () => {
  it("flags a known violation, so this guard can actually fail", () => {
    // Positive control (mirrors `fr-83-no-semantic-tag-stripping.test.ts`'s
    // own `test_guard_flags_a_known_violation` counterpart): proves the
    // walker still matches something, so a refactor that quietly breaks the
    // AST walk does not rot into a guard that always passes.
    const violation = `<input id="length" value={entry.data.length} onChange={noop} />`;
    expect(computedFieldInputs(violation, "control.tsx").size).toBeGreaterThan(0);
  });

  it("flags the codebase's own Field wrapper the same way as a native control", () => {
    const violation = `<Field id="row_version" label="Row version">{(props) => <input {...props} />}</Field>`;
    expect(computedFieldInputs(violation, "control-field.tsx").size).toBeGreaterThan(0);
  });

  it("flags an id/name given as a JSX expression, not only a bare attribute string", () => {
    // `id="length"` and `id={"length"}` are different AST shapes (a plain
    // `StringLiteral` initializer vs. a `JsxExpression` wrapping one) - both
    // must trip this guard, or a control built from a template rather than
    // a bare attribute string slips past on syntax alone.
    const violation = `<input name={"row_version"} />`;
    expect(computedFieldInputs(violation, "control-expr.tsx").size).toBeGreaterThan(0);
  });

  it("does not flag a plain property read that happens to share a computed field's name", () => {
    // The exact shape at `admin-catalogue-edit.tsx`'s own `<dd>{entry.data.
    // length}</dd>` - a rendered value, not a form control, and this guard
    // must not confuse the two. `terms.length`/array `.length` elsewhere in
    // the frontend is the same shape again, generalised.
    const reading = `
      function View() {
        return <dd>{entry.data.length}</dd>;
      }
    `;
    expect(computedFieldInputs(reading, "reading.tsx").size).toBe(0);
  });

  it("does not itself trip the guard by naming the computed fields in prose", () => {
    // Several real panel docstrings explain *why* there is no control for
    // `length`/`fsn`/`row_version` etc - naming them in a comment must not
    // be what this guard reacts to.
    const docstringOnly = `
      /**
       * Deliberately no control for length, fsn, au_preferred_term,
       * display_term, row_version, version or history - see FR-24/FR-85.
       */
      export const nothing = 1;
    `;
    expect(computedFieldInputs(docstringOnly, "prose.tsx").size).toBe(0);
  });

  it("does not flag an id/name attribute that is merely a substring or a different field", () => {
    // `id="add-terms"` (a real id in `designations-panel.tsx`) must not
    // match `terms`-shaped names, and an unrelated field name entirely
    // (`"preferred_term"`, not one of the banned names) must not match
    // either - the comparison is by exact set membership, not `includes`.
    const unrelated = `<input id="add-terms" name="preferred_term" />`;
    expect(computedFieldInputs(unrelated, "unrelated.tsx").size).toBe(0);
  });

  const files = collectSourceFiles(SRC_ROOT);

  it("found at least one .tsx file to walk", () => {
    // The guard below (`it.each(files)`) silently passes with zero
    // assertions if `files` is empty - a broken `collectSourceFiles` (a
    // moved `src` root, a typo'd extension filter) would otherwise rot this
    // into a guard that always passes for the wrong reason, exactly the
    // failure mode `fr-83-no-semantic-tag-stripping.test.ts`'s own positive
    // control is written to catch on the walker itself. This asserts the
    // other half: that the walker actually got handed real files to run on.
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files)("%s", (file) => {
    const found = computedFieldInputs(readFileSync(file, "utf-8"), file);
    expect(
      found,
      `${relative(SRC_ROOT, file)} offers ${[...found].join(", ")} as a form control input`,
    ).toEqual(new Set());
  });
});
