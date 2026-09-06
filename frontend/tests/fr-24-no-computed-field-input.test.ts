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
 * So the walk looks for a JSX form control - a native `input`/`textarea`/
 * `select`, or any component wrapping one whose own tag name ends in `Field`
 * (`components/field.tsx`'s own `Field`, and `ChangelogNoteField`, which
 * forwards its `id` straight through to a `Field` inside - see
 * `changelog-note-field.tsx`) - whose `id` or `name` attribute *names* a
 * computed field. "Names" is not exact-string equality: `id`s in this
 * codebase are prefixed and hyphenated (`add-terms`, `amend-note`,
 * `retire-binding-note`), so a future computed-field control would plausibly
 * be `id="amend-length"` or `id="bind-au-preferred-term"`, not a bare
 * `id="length"`. The walk tokenises on `-`/`_` and matches a computed
 * field's own tokens as a contiguous run inside the attribute's tokens, so
 * either shape trips it.
 *
 * An AST walk, not a text search: a panel docstring is free to *name* these
 * fields in prose (several already do, explaining why there is no control
 * for them), and `ts.forEachChild` does not descend into comment trivia, so
 * mentioning one there cannot trip this.
 *
 * **Known limit, deliberately not chased further (issue #61 review).** Every
 * `property-controls/*` component (`text.tsx`, `textarea.tsx`, `uri.tsx`,
 * `number.tsx`, `concept-picker.tsx`) and `repeatable-values.tsx` itself
 * forwards `id={id}` - a parameter, not a literal - down to its own inner
 * `Field`. A registry-defined property's `key` is runtime data (from
 * `GET /registry/properties`), never a string literal anywhere in this
 * frontend's source, so there is no literal for a syntactic walk to read at
 * the one call site that would actually matter. Resolving `id={id}` back to
 * its origin is full dataflow analysis, out of proportion for this guard;
 * see `docs/requirements/requirements.yaml`'s own FR-24 note for how this
 * limit is represented in the traceability record rather than overclaimed.
 */

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../src");

//: The computed fields FR-24 covers for this screen set (issue #61's plan):
//: `length` (FR-85, the preferred term's published character count),
//: `fsn`/`au_preferred_term` (bound from the terminology server, FR-06/FR-82
//: - a code binding's own two read-only fields, not editor input), `display_
//: term` (FR-83's server-stripped value, already banned outright by the
//: FR-83 guard - named here too since it is also never a form control's
//: `id`/`name`), and `row_version`/`version`/`history` (concurrency and
//: audit bookkeeping, never something an editor types). Each name's own
//: `_`-separated words are what `containsComputedField` looks for as a
//: contiguous run of tokens, so `au_preferred_term` also matches an id
//: tokenising to `["bind", "au", "preferred", "term"]`.
const COMPUTED_FIELD_NAMES = [
  "length",
  "fsn",
  "au_preferred_term",
  "display_term",
  "row_version",
  "version",
  "history",
];

const COMPUTED_FIELD_TOKENS: string[][] = COMPUTED_FIELD_NAMES.map((name) =>
  name.split("_"),
);

/** `value` split on `-`/`_` into lower-case tokens, dropping empty runs from
 * a leading/doubled separator. */
function tokenise(value: string): string[] {
  return value
    .toLowerCase()
    .split(/[-_]/)
    .filter((token) => token.length > 0);
}

/**
 * The computed field name matched inside `value`'s own tokens, or `null`.
 * Matches a whole, contiguous run of tokens - `"amend-length"` matches
 * `length` (single-token run), `"bind-au-preferred-term"` matches
 * `au_preferred_term` (three-token run) - never a mere substring, so
 * `"add-terms"` does not match `length`/`term`/anything else in the list
 * (`terms` is not `term`, and neither is one of the banned names anyway).
 */
function containsComputedField(value: string): string | null {
  const valueTokens = tokenise(value);
  for (const fieldTokens of COMPUTED_FIELD_TOKENS) {
    for (let start = 0; start + fieldTokens.length <= valueTokens.length; start += 1) {
      if (fieldTokens.every((token, offset) => valueTokens[start + offset] === token)) {
        return fieldTokens.join("_");
      }
    }
  }
  return null;
}

//: Native elements this guard always treats as a form control.
const NATIVE_FORM_CONTROL_TAGS = new Set(["input", "textarea", "select"]);

/** A form control for this guard's purposes: a native element, or any
 * component whose own tag name ends in `Field` - this codebase's own
 * convention for a field wrapper (`Field` itself, `ChangelogNoteField`, and
 * any future `XyzField`) that carries the control's `id`/`name` as its own
 * JSX attribute rather than on the native element nested inside it. See the
 * module docstring for why a plain `Control`-named component (the
 * `property-controls/*` family) is not covered the same way. */
function isFormControlTag(tagName: string): boolean {
  return NATIVE_FORM_CONTROL_TAGS.has(tagName) || tagName.endsWith("Field");
}

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      files.push(...collectSourceFiles(full));
      continue;
    }
    // `.(ts|tsx)`, matching `fr-83-no-semantic-tag-stripping.test.ts`'s own
    // filter exactly - a plain `.ts` file cannot contain JSX syntax at all
    // (the parser needs `.tsx` for that), so no `.ts` file will ever trip
    // the JSX-attribute walk below, but there is no reason to diverge from
    // the sibling guard's own file selection to save checking that.
    if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

/**
 * The computed-field names found on an `id`/`name` attribute of a form
 * control (`isFormControlTag`) anywhere in `source`. Walks both a
 * self-closing form control (`<input id="length" />`) and an opening tag of
 * one with children (`<Field id="length">...</Field>`) - `ts.forEachChild`
 * still reaches both shapes' attributes either way, but the two are
 * distinct node kinds in the TypeScript AST and each needs its own
 * `isJsxAttributes`-bearing parent checked explicitly.
 *
 * Parsed as `.tsx` only when `filePath` actually is one, mirroring
 * `fr-83-no-semantic-tag-stripping.test.ts`'s own per-file `ScriptKind`
 * choice - forcing TSX parsing on a `.ts` file risks misreading a generic
 * type-assertion (`<T>value`) as a JSX opening tag, the exact ambiguity the
 * two script kinds exist to keep apart.
 */
function computedFieldInputs(source: string, filePath: string): Set<string> {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const found = new Set<string>();

  function checkAttributes(tagName: string, attributes: ts.JsxAttributes) {
    if (!isFormControlTag(tagName)) {
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
      if (literal === null) {
        continue;
      }
      const matched = containsComputedField(literal);
      if (matched !== null) {
        found.add(matched);
      }
    }
  }

  /**
   * The literal string an `id`/`name` attribute's initializer holds, or
   * `null` if it is not a plain string. Handles both JSX attribute value
   * shapes - `id="length"` (the initializer is a `StringLiteral` node
   * directly, every real instance in this codebase today) and `id={"length"}`
   * (the initializer is a `JsxExpression` wrapping one) - so a control built
   * from a template rather than a bare attribute string cannot slip past
   * this guard on syntax alone. Anything else (a variable, a template
   * literal with substitutions, a ternary) is not a literal this guard can
   * resolve and is left unflagged - see the module docstring's own note on
   * `property-controls/*`'s `id={id}`, the one real instance of that limit.
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

  it("flags a realistically prefixed id, not only a bare computed-field name", () => {
    // Issue #61 review: real ids in this codebase are hyphenated and
    // prefixed (`add-terms`, `amend-note`) - a future computed-field control
    // would plausibly be `id="amend-length"`, not a bare `id="length"`.
    const violation = `<input id="amend-length" />`;
    expect(computedFieldInputs(violation, "control-prefixed.tsx").size).toBeGreaterThan(
      0,
    );
  });

  it("flags a multi-word computed field spread across hyphenated tokens", () => {
    // `au_preferred_term`'s own three tokens, found as a contiguous run
    // inside a longer, differently-prefixed id.
    const violation = `<input id="bind-au-preferred-term" />`;
    expect(computedFieldInputs(violation, "control-multiword.tsx").size).toBeGreaterThan(
      0,
    );
  });

  it("flags any wrapper component whose tag name ends in Field, not only Field itself", () => {
    // `ChangelogNoteField` forwards its own `id` straight through to a
    // `Field` inside it (`changelog-note-field.tsx`) - a hypothetical future
    // `LengthField` would follow the same shape, and this guard has to
    // catch the call site's own literal `id`, not only `Field`'s literal
    // name.
    const violation = `<ChangelogNoteField id="amend-length" changelogNote={note} />`;
    expect(computedFieldInputs(violation, "control-wrapper.tsx").size).toBeGreaterThan(0);
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
    // either - the comparison is by whole-token runs, not raw substring.
    const unrelated = `<input id="add-terms" name="preferred_term" />`;
    expect(computedFieldInputs(unrelated, "unrelated.tsx").size).toBe(0);
  });

  it("does not flag a real, un-prefixed changelog-note id sharing no tokens with a computed field", () => {
    // The real ids this guard's own widened tag match now newly reaches -
    // `bind-note`, `retire-binding-note`, `replace-note` (`bindings-panel.
    // tsx`) - must still read as clean.
    const notes = `
      <ChangelogNoteField id="bind-note" changelogNote={a} />
      <ChangelogNoteField id="retire-binding-note" changelogNote={b} />
      <ChangelogNoteField id="replace-note" changelogNote={c} />
    `;
    expect(computedFieldInputs(notes, "notes.tsx").size).toBe(0);
  });

  it("does not flag property-controls' own id={id} forwarding (the documented limit)", () => {
    // The module docstring's own accepted gap: a parameter, not a literal,
    // so there is nothing for a syntactic walk to read here even though
    // `id` is passed straight to a `Field`.
    const forwarded = `
      function TextControl({ id, label }: ControlProps) {
        return <Field id={id} label={label}>{(props) => <input {...props} />}</Field>;
      }
    `;
    expect(computedFieldInputs(forwarded, "text-control.tsx").size).toBe(0);
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
    expect(files.some((file) => file.endsWith(".tsx"))).toBe(true);
  });

  it.each(files)("%s", (file) => {
    const found = computedFieldInputs(readFileSync(file, "utf-8"), file);
    expect(
      found,
      `${relative(SRC_ROOT, file)} offers ${[...found].join(", ")} as a form control input`,
    ).toEqual(new Set());
  });
});
