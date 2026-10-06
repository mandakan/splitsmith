/**
 * Route tests render through ``@/App``, whose pages load on first navigation
 * (``lib/lazyPage``, #894). The first test in a file to reach a page pays
 * that page's transform inside its ``findBy``, which under a loaded suite
 * can outlast testing-library's 1 s default and flake. A route test file
 * calls this once to give its async queries room for that load; the
 * timeout is restored afterwards.
 */
import { configure, getConfig } from "@testing-library/react";
import { afterAll, beforeAll } from "vitest";

export const LAZY_PAGE_TIMEOUT_MS = 10_000;

export function allowLazyPages(): void {
  let previous = 1000;
  beforeAll(() => {
    previous = getConfig().asyncUtilTimeout;
    configure({ asyncUtilTimeout: LAZY_PAGE_TIMEOUT_MS });
  });
  afterAll(() => {
    configure({ asyncUtilTimeout: previous });
  });
}
