/**
 * Global Vitest setup (jsdom environment).
 *
 * - Registers jest-dom's matchers (toBeDisabled, toHaveTextContent, ...)
 *   on vitest's own `expect`, per the ecosystem convention, rather than
 *   hand-rolling assertion helpers per test file.
 * - Runs Testing Library's `cleanup()` after every test so one
 *   component test's rendered tree never leaks into the next.
 */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});
