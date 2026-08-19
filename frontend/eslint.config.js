import js from "@eslint/js";
import eslintConfigPrettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  {
    // Backstops issue #146's acceptance criterion "no component builds a
    // catalogue URL by string concatenation": internal URLs come from the
    // route table (`src/router/route-tree.ts`) via `<Link to>`, `navigate`,
    // or `router.buildLocation` - never a template literal, `+` concatenation,
    // a raw string literal href, or a direct `location` assignment. See
    // docs/architecture/frontend-routing.md.
    //
    // This is a backstop, not an exhaustive guarantee - it pattern-matches
    // syntax shapes and cannot see through an indirection (a variable built
    // elsewhere, a helper function, string.concat/replace). The type system
    // (the `Register` module augmentation in router.tsx, which makes a wrong
    // `to`/`params`/`search` a `pnpm typecheck` failure) and code review are
    // what a determined bypass still has to get past.
    files: ["src/**/*.{ts,tsx}"],
    // The route table itself is where paths live. Test files legitimately
    // build a raw URL to deep-link the router directly (e.g. asserting a
    // code round-trips through the path) - that is testing the contract,
    // not a component sidestepping it.
    ignores: ["src/router/route-tree.ts", "src/**/*.test.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          // `` `/catalogue${x}` ``, `` `/admin/${x}` ``, `` `${base}/catalogue` `` -
          // no trailing-slash requirement, so a bare `/admin` or a leading
          // interpolation is caught too.
          selector:
            "TemplateLiteral > TemplateElement[value.raw=/\\/(catalogue|releases|submissions|admin)(\\/|$)/]",
          message:
            "Build an internal URL from the route table - <Link to>/navigate/router.buildLocation - never by string concatenation (issue #146, FR-17).",
        },
        {
          selector:
            "BinaryExpression[operator='+'] > Literal[value=/^\\/(catalogue|releases|submissions|admin)/]",
          message:
            "Build an internal URL from the route table - <Link to>/navigate/router.buildLocation - never by string concatenation (issue #146, FR-17).",
        },
        {
          // <a href="/catalogue/NPTC-1">, <a href="/admin">. `to=` (the
          // typed <Link>/navigate prop) is deliberately not matched here.
          selector:
            "JSXAttribute[name.name='href'] Literal[value=/^\\/(catalogue|releases|submissions|admin)(\\/|$|\\?)/]",
          message:
            "Use <Link to> (typed against the route table), not a raw href, for an internal URL (issue #146, FR-17).",
        },
        {
          // window.location.assign("/admin"), window.location.replace(...).
          selector:
            "CallExpression[callee.property.name=/^(assign|replace)$/][callee.object.property.name='location'] > Literal[value=/^\\/(catalogue|releases|submissions|admin)(\\/|$|\\?)/]",
          message:
            "Use router.navigate (typed against the route table), not location.assign/replace, for an internal URL (issue #146, FR-17).",
        },
        {
          // window.location.href = "/admin".
          selector:
            "AssignmentExpression[left.property.name='href'] > Literal[value=/^\\/(catalogue|releases|submissions|admin)(\\/|$|\\?)/]",
          message:
            "Use router.navigate (typed against the route table), not a direct location.href assignment, for an internal URL (issue #146, FR-17).",
        },
      ],
    },
  },
  // Prettier last: turns off any ESLint rule that would conflict with the
  // formatter, so the two never disagree about the same line.
  eslintConfigPrettier,
);
