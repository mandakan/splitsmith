/**
 * One hue per meaning, given in one place. A class or region kind coloured
 * by a map of its own drifts: the draw went pink in four maps and stayed red
 * in Coach's legend, which had its own copy.
 */
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { LANE_TICK } from "@/components/coach/laneDrag";
import { CHIP_TICK_BG, HUE_FILL } from "@/components/ui/chipTicks";
import { BUDGET_TICK } from "@/lib/timeBudget";

// A map entry keyed by a class or kind whose value names a colour utility.
const KEYED_COLOUR =
  /^\s*(first_shot|draw|movement|transition|split|fire|reload|activation|unclassified)\s*:\s*"[^"]*\b(?:bg|text|border)-(?:draw|beep|manual|done|live|led|ink-2|muted|surface-3)\b/m;

describe("the hues", () => {
  it("are given only in chipTicks.ts", () => {
    const sites = readdirSync("src", { recursive: true, encoding: "utf8" })
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && !f.endsWith("chipTicks.ts"))
      .filter((f) => KEYED_COLOUR.test(readFileSync(`src/${f}`, "utf8")));
    expect(sites).toEqual([]);
  });

  it("give the draw interval and the draw region one hue, never the brand red", () => {
    expect(BUDGET_TICK.first_shot).toBe(LANE_TICK.draw);
    expect(CHIP_TICK_BG[BUDGET_TICK.first_shot]).not.toBe("bg-led");
    expect(HUE_FILL[BUDGET_TICK.first_shot]).toMatch(/^bg-draw /);
  });
});
