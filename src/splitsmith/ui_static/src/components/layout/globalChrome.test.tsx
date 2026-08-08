/**
 * Single-mount guard (#550).
 *
 * The refactor's whole claim is that global chrome is defined and mounted
 * once. This is the assertion that fails if a fifth mount is added later,
 * which is exactly how the duplication accrued the first time.
 */
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("global chrome mounts", () => {
  it("renders AccountChip from exactly two call sites", () => {
    const sites = readdirSync("src", { recursive: true, encoding: "utf8" })
      .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
      .map((f) => `src/${f}`)
      .filter((f) => /<AccountChip\b/.test(readFileSync(f, "utf8")));
    // GlobalBar (desktop, via RootLayout) and MatchShell's mobile nav
    // drawer. The drawer is deliberate: RootLayout renders no global bar
    // on mobile, so the drawer is the only sign-out on a phone.
    expect(sites.sort()).toEqual([
      "src/components/layout/GlobalBar.tsx",
      "src/components/match/MatchShell.tsx",
    ]);
  });

  it("renders HostedAccountChip from exactly the same two call sites", () => {
    const sites = readdirSync("src", { recursive: true, encoding: "utf8" })
      .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
      .map((f) => `src/${f}`)
      .filter((f) => /<HostedAccountChip\b/.test(readFileSync(f, "utf8")));
    // Same two sites as AccountChip and for the same reason: the global
    // bar on desktop (and on /pick on a phone), plus MatchShell's mobile
    // nav drawer, which is the only account surface inside a match on a
    // phone. The two chips self-gate on opposite deployment modes.
    expect(sites.sort()).toEqual([
      "src/components/layout/GlobalBar.tsx",
      "src/components/match/MatchShell.tsx",
    ]);
  });
});
