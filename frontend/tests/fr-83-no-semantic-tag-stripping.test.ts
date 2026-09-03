// Lives outside `src` and in `tsconfig.node.json`'s program instead
// (review finding): a `/// <reference types="node" />` is program-wide, not
// file-scoped, so putting one on a file `tsconfig.app.json` still includes
// would leak `process`/`Buffer` into every browser module's typecheck - the
// same result as the `"types": ["node"]` this replaced. Being outside `src`
// altogether is what actually keeps Node's ambient globals off the app's own
// program; see `tsconfig.app.json`'s `"types": []` for the other half.
//
// `node:fs`/`node:path`/`node:url`: a Vitest source-scanning guard, not app
// code - Vite's build never resolves a `node:*` specifier into the browser
// bundle in any case.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * FR-83's stripping rule (`nptc.exports.semantic_tag.strip_semantic_tag`) has
 * exactly one legitimate home, and it is server-side: `Binding.display_term`
 * is already the stripped value by the time it reaches the wire. The code
 * binding panel (issue #150) renders `fsn` verbatim and never `display_term`,
 * on purpose - see `bindings-panel.tsx`'s module docstring - because a second
 * strip anywhere in this frontend is exactly the double-stripping hazard
 * FR-83 exists to prevent (the backend's own guard,
 * `test_catalogue_bindings.py::test_semantic_tag_functions_are_referenced_only_at_known_sites`,
 * is the AST walk this test mirrors on the frontend side).
 *
 * An AST walk, not a text search: `bindings-panel.tsx`'s own docstring names
 * every banned identifier in prose, and a regex over raw file text would flag
 * that comment as a violation. `ts.forEachChild` does not descend into
 * comment trivia, so identifiers mentioned only in a docstring are invisible
 * to it - only an actual reference in code trips this guard.
 */

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../src");

const BANNED_NAMES = new Set([
  "display_term",
  "displayTerm",
  "strip_semantic_tag",
  "stripSemanticTag",
  "semantic_tag",
  "semanticTag",
  "render_display_term",
  "renderDisplayTerm",
]);

/**
 * `schema.ts` is generated from the OpenAPI document (`pnpm generate:api`)
 * and must type the wire field `Binding.display_term` verbatim - that is the
 * value this guard exists to stop the frontend from *re-deriving*, not a
 * violation of deriving it. Its own name is enough of an allowlist: nothing
 * else under `frontend/src` should ever need to.
 */
const ALLOWED_FILES = new Set([resolve(SRC_ROOT, "api/schema.ts")]);

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      files.push(...collectSourceFiles(full));
      continue;
    }
    if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

function referencedBannedNames(source: string, filePath: string): Set<string> {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const found = new Set<string>();
  function visit(node: ts.Node) {
    if (
      (ts.isIdentifier(node) ||
        ts.isPrivateIdentifier(node) ||
        ts.isStringLiteral(node)) &&
      BANNED_NAMES.has(node.text)
    ) {
      found.add(node.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

describe("FR-83: no frontend module strips or re-derives a semantic tag", () => {
  it("flags a known violation, so this guard can actually fail", () => {
    // Positive control (mirrors the backend guard's own
    // `test_guard_flags_a_known_violation`): proves the walker still matches
    // something, so a refactor that quietly breaks it doesn't rot into a
    // test that always passes.
    const violation = "const displayTerm = fsn.replace(/\\s*\\([^)]*\\)\\s*$/, '');";
    expect(referencedBannedNames(violation, "control.ts").size).toBeGreaterThan(0);
  });

  it("does not itself trip the guard by naming the banned identifiers in prose", () => {
    // `bindings-panel.tsx`'s docstring names every one of `BANNED_NAMES` -
    // proving that mention alone, inside a comment, is not what this guard
    // flags.
    const docstringOnly = `
      /**
       * Deliberately never references display_term, strip_semantic_tag,
       * semantic_tag or render_display_term - see the module docstring.
       */
      export const nothing = 1;
    `;
    expect(referencedBannedNames(docstringOnly, "prose.ts").size).toBe(0);
  });

  const files = collectSourceFiles(SRC_ROOT).filter((file) => !ALLOWED_FILES.has(file));

  it.each(files)("%s", (file) => {
    const found = referencedBannedNames(readFileSync(file, "utf-8"), file);
    expect(
      found,
      `${relative(SRC_ROOT, file)} references ${[...found].join(", ")}`,
    ).toEqual(new Set());
  });
});
