/**
 * isShareView (#700 Task 4) - path-based share-mode detection, mirroring
 * how useMatchHref/AuthGate spot the /share/ tree. Compare.tsx uses this
 * to gate operator-only affordances (Audit/Coach tabs, audit CTAs) off
 * the anonymous share surface.
 */
import { describe, expect, it } from "vitest";

import { isShareView } from "@/lib/shareView";

describe("isShareView", () => {
  it("is true under the share tree", () => {
    expect(isShareView("/share/tok123/compare/2")).toBe(true);
  });

  it("is false on the owner match-scoped route", () => {
    expect(isShareView("/match/m1/compare/2")).toBe(false);
  });

  it("is false for a bare path that merely contains 'share' later on", () => {
    expect(isShareView("/match/m1/re-share/compare/2")).toBe(false);
  });

  it("is false for the root path", () => {
    expect(isShareView("/")).toBe(false);
  });
});
