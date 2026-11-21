// @ts-check
import js from "@eslint/js";
import i18next from "eslint-plugin-i18next";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

/** JSX attributes whose values are identifiers or URLs, never user-facing text. */
const NON_TEXT_JSX_ATTRIBUTES = [
  // eslint-plugin-i18next defaults (an explicit list replaces them)
  "className",
  "styleName",
  "style",
  "type",
  "key",
  "id",
  "width",
  "height",
  // routing, forms and test hooks
  "to",
  "href",
  "path",
  "name",
  "htmlFor",
  "autoComplete",
  "inputMode",
  "data-testid",
  "kind",
  "value",
];

export default defineConfig([
  globalIgnores(["dist", "coverage"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    // ADR-0013: every user-facing string comes from src/locales via i18next.
    files: ["src/**/*.tsx"],
    ignores: ["src/**/*.test.tsx", "src/test/**"],
    plugins: { i18next },
    rules: {
      "i18next/no-literal-string": [
        "error",
        { mode: "jsx-only", "jsx-attributes": { exclude: NON_TEXT_JSX_ATTRIBUTES } },
      ],
    },
  },
  {
    // ADR-0016: the customer surfaces never import dashboard code, so the chat can be split out.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/agent/**", "src/app/**", "src/test/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/agent", "**/agent/**"],
              message: "Only the router may reference the agent dashboard.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
]);
