// Lives outside `src` and in `tsconfig.node.json`'s program instead - see
// `fr-83-no-semantic-tag-stripping.test.ts`'s own note on why: a Vitest
// source-scanning guard is not app code, and keeping it outside `src`
// keeps Node's ambient globals off the app's own typecheck.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * ADR-0013 SS3 (FR-77): a `PropertyDefinition`'s *interaction* is named by
 * `ControlKind`, exhaustively dispatched through `CONTROLS`
 * (`property-controls/index.ts`) - never by branching on `datatype` itself.
 * `datatype` is a free-text column with no closed set the frontend can ever
 * safely enumerate (that is the whole point of ADR-0012's no-CHECK/no-ENUM
 * decision), so a `switch`/`if`-chain keyed on it is exactly the "proxy
 * switch" ADR-0013 SS5 names as limit 1: it happens to work today because
 * `binding_target`/`datatype` correlate with the control the server would
 * have chosen anyway, and breaks the day a new datatype reuses an existing
 * `ControlKind` in a way the frontend's own guess does not anticipate.
 *
 * An AST walk, not a text search - mirroring
 * `fr-83-no-semantic-tag-stripping.test.ts` exactly: a `switch` on
 * `.datatype`, or an equality check against one of the datatype literals
 * the backend's own registry recognises (`BUILTIN_DATATYPES`,
 * `backend/src/nptc/registry/datatypes/__init__.py`), each targeting a
 * property access or identifier named `datatype`.
 */

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../src");

/** Mirrors `BUILTIN_DATATYPES` in `backend/src/nptc/registry/datatypes/__init__.py`. */
const DATATYPE_LITERALS = new Set(["code", "string", "decimal", "positiveInt", "url"]);

function isDatatypeAccess(node: ts.Expression): boolean {
  if (ts.isIdentifier(node)) {
    return node.text === "datatype";
  }
  if (ts.isPropertyAccessExpression(node)) {
    return node.name.text === "datatype";
  }
  return false;
}

function isDatatypeLiteral(node: ts.Expression): boolean {
  return ts.isStringLiteralLike(node) && DATATYPE_LITERALS.has(node.text);
}

const EQUALITY_OPERATORS = new Set([
  ts.SyntaxKind.EqualsEqualsToken,
  ts.SyntaxKind.EqualsEqualsEqualsToken,
  ts.SyntaxKind.ExclamationEqualsToken,
  ts.SyntaxKind.ExclamationEqualsEqualsToken,
]);

function findDatatypeBranches(source: string, filePath: string): string[] {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const found: string[] = [];

  function visit(node: ts.Node) {
    if (ts.isSwitchStatement(node) && isDatatypeAccess(node.expression)) {
      found.push(`switch (${node.expression.getText(sourceFile)})`);
    }
    if (
      ts.isBinaryExpression(node) &&
      EQUALITY_OPERATORS.has(node.operatorToken.kind) &&
      ((isDatatypeAccess(node.left) && isDatatypeLiteral(node.right)) ||
        (isDatatypeAccess(node.right) && isDatatypeLiteral(node.left)))
    ) {
      found.push(node.getText(sourceFile));
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
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
    if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

describe("ADR-0013 SS3 / FR-77: no frontend module branches a form control on datatype", () => {
  it("flags a known violation, so this guard can actually fail", () => {
    const violation = `
      switch (definition.datatype) {
        case "code": return ConceptPicker;
        default: return TextControl;
      }
    `;
    expect(findDatatypeBranches(violation, "control.ts").length).toBeGreaterThan(0);
  });

  it("flags an equality check against a known datatype literal either way round", () => {
    expect(
      findDatatypeBranches(
        'if (definition.datatype === "code") { pick(); }',
        "control.ts",
      ).length,
    ).toBeGreaterThan(0);
    expect(
      findDatatypeBranches(
        'if ("string" === definition.datatype) { pick(); }',
        "control.ts",
      ).length,
    ).toBeGreaterThan(0);
  });

  it("does not flag a switch or comparison on ControlKind - the sanctioned dispatch", () => {
    const sanctioned = `
      const CONTROLS: Record<ControlKind, ComponentType<ControlProps>> = {
        text: TextControl,
        concept_picker: ConceptPickerControl,
      };
      if (formControl.control === "concept_picker") { doSomething(); }
    `;
    expect(findDatatypeBranches(sanctioned, "index.ts")).toEqual([]);
  });

  it("does not itself trip the guard by naming datatype literals in prose or unrelated code", () => {
    const prose = `
      /** Branching on datatype ("code", "string", "decimal") is what this guard forbids. */
      export const datatype = "code"; // an assignment, not a comparison
      const other = { datatype: "code" }; // a property declaration, not a branch
    `;
    expect(findDatatypeBranches(prose, "prose.ts")).toEqual([]);
  });

  const files = collectSourceFiles(SRC_ROOT);

  it.each(files)("%s", (file) => {
    const found = findDatatypeBranches(readFileSync(file, "utf-8"), file);
    expect(
      found,
      `${relative(SRC_ROOT, file)} branches on datatype: ${found.join(", ")}`,
    ).toEqual([]);
  });
});
