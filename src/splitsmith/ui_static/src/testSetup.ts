/**
 * Global Vitest setup (jsdom environment).
 *
 * - Registers jest-dom's matchers (toBeDisabled, toHaveTextContent, ...)
 *   on vitest's own `expect`, per the ecosystem convention, rather than
 *   hand-rolling assertion helpers per test file.
 * - Runs Testing Library's `cleanup()` after every test so one
 *   component test's rendered tree never leaks into the next.
 * - Stubs `ResizeObserver`, which jsdom does not implement. Any component
 *   under test that measures itself (useShellHeaderHeight, MatchShell's
 *   header, ...) needs a constructor to exist even though this stub does
 *   nothing; a test that wants resize callbacks to actually fire should
 *   still install its own richer mock. Set once per test file (isolate
 *   defaults on, so each file gets a fresh `window`) rather than per
 *   test -- nothing here is stateful enough to need resetting.
 * - Stubs `matchMedia` for the same reason, reporting no match: jsdom
 *   does not implement it, and `useIsMobile` reads it synchronously on
 *   first render, so any component that gained a responsive branch
 *   (GlobalBar and the account chips, #733) would otherwise take down
 *   every suite that renders it. No match = the desktop branch, which
 *   is what the suites written before those branches existed assert. A
 *   file that wants the mobile branch mocks `@/lib/useIsMobile`
 *   directly -- see GlobalBar.mobile.test.tsx -- which is both clearer
 *   about intent and immune to the query string changing.
 */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});

// Node >= 26 defines its own experimental `localStorage`/`sessionStorage`
// globals, which are `undefined` unless the process runs with
// --localstorage-file. In the vitest jsdom environment those globals shadow
// jsdom's real Web Storage, so any component that reads storage on render
// (mode toggle, stage-drawer collapse, device-code stash, ...) crashes.
// Provide an in-memory stand-in per test file; the jsdom window is fresh per
// file (isolate on), so nothing leaks across files.
function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    key: (i: number) => [...store.keys()][i] ?? null,
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

if (window.localStorage == null) {
  Object.defineProperty(window, "localStorage", {
    value: memoryStorage(),
    configurable: true,
  });
}
if (window.sessionStorage == null) {
  Object.defineProperty(window, "sessionStorage", {
    value: memoryStorage(),
    configurable: true,
  });
}

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

window.ResizeObserver =
  ResizeObserverStub as unknown as typeof window.ResizeObserver;

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
