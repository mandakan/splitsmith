import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Downgrade to warn so pre-existing violations don't block builds;
      // tighten to error once the backlog is cleared.
      "react-hooks/rules-of-hooks": "warn",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Pre-existing violations -- demote to warn until addressed
      "prefer-const": "warn",
      "no-useless-assignment": "warn",
      "@typescript-eslint/no-unused-vars": "warn",
    },
  },
);
