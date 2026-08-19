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
    // or `router.buildLocation` - never a template literal or `+` on a path
    // segment. See docs/architecture/frontend-routing.md.
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
          selector:
            "TemplateLiteral > TemplateElement[value.raw=/\\/(catalogue|releases|submissions|admin)\\//]",
          message:
            "Build an internal URL from the route table - <Link to>/navigate/router.buildLocation - never by string concatenation (issue #146, FR-17).",
        },
        {
          selector:
            "BinaryExpression[operator='+'] > Literal[value=/^\\/(catalogue|releases|submissions|admin)/]",
          message:
            "Build an internal URL from the route table - <Link to>/navigate/router.buildLocation - never by string concatenation (issue #146, FR-17).",
        },
      ],
    },
  },
  // Prettier last: turns off any ESLint rule that would conflict with the
  // formatter, so the two never disagree about the same line.
  eslintConfigPrettier,
);
