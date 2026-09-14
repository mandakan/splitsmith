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
      // rules-of-hooks catches real bugs (conditional/looped hook calls) --
      // keep it at error. exhaustive-deps and react-refresh are advisory.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Unused vars stays an error so dead imports from the refactor surface.
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  // Visual budget (docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md s5).
  // Pages and feature components get type and spacing from components/ui,
  // never from arbitrary Tailwind values. Files that predate the rule carry
  // a file-level disable that the PR rebuilding that page deletes, so the
  // count only goes down.
  {
    files: ["src/pages/**/*.{ts,tsx}", "src/components/**/*.{ts,tsx}"],
    ignores: ["src/components/ui/**", "**/*.test.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/text-\\[[0-9.]+(rem|px)\\]/], TemplateElement[value.raw=/text-\\[[0-9.]+(rem|px)\\]/]",
          message:
            "Arbitrary text size. Use a type role from components/ui (PageHeader, Label, Stat) or the Tailwind text-* scale.",
        },
        {
          selector: "Literal[value=/tracking-\\[/], TemplateElement[value.raw=/tracking-\\[/]",
          message: "Arbitrary letter-spacing. Label is the only tracked-caps style.",
        },
        {
          selector: "Literal[value=/\\bfont-display\\b/], TemplateElement[value.raw=/\\bfont-display\\b/]",
          message: 'Antonio is PageHeader and Button variant="primary" only.',
        },
        {
          selector: "Literal[value=/\\bbg-led\\b.*\\btext-bg\\b/], TemplateElement[value.raw=/\\bbg-led\\b.*\\btext-bg\\b/]",
          message: 'Cream on saturated red fails contrast; use Button variant="primary".',
        },
      ],
    },
  },
);
