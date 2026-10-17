import { describe, expect, it } from "vitest";

import { TIMER_BAND, bandOf, bandParam, parseStored, validBand } from "@/lib/beepFilter";

describe("beepFilter", () => {
  it("names the band each choice filters to", () => {
    expect(bandOf({ kind: "off" })).toBeNull();
    expect(bandOf({ kind: "tone" })).toEqual(TIMER_BAND);
    expect(bandOf({ kind: "custom", lo: 2000, hi: 4000 })).toEqual({ lo: 2000, hi: 4000 });
    expect(bandParam({ kind: "tone" })).toBe("2200-3500");
    expect(bandParam({ kind: "off" })).toBeUndefined();
  });

  it("filters the timer tone to the timer's own band when the server found one", () => {
    expect(bandOf({ kind: "tone" }, [2507, 2907])).toEqual({ lo: 2507, hi: 2907 });
    expect(bandParam({ kind: "tone" }, [2507, 2907])).toBe("2507-2907");
    expect(bandOf({ kind: "custom", lo: 2000, hi: 4000 }, [2507, 2907])).toEqual({ lo: 2000, hi: 4000 });
  });

  it("accepts the server's band range only", () => {
    expect(validBand(2200, 3500)).toBe(true);
    expect(validBand(3500, 2200)).toBe(false);
    expect(validBand(50, 3000)).toBe(false);
    expect(validBand(2200, 13000)).toBe(false);
  });

  it("reads what a browser stored, falling back to the timer tone", () => {
    expect(parseStored(null)).toEqual({ kind: "tone" });
    expect(parseStored("not json")).toEqual({ kind: "tone" });
    expect(parseStored(JSON.stringify({ kind: "off" }))).toEqual({ kind: "off" });
    expect(parseStored(JSON.stringify({ kind: "custom", lo: 9000, hi: 100 }))).toEqual({ kind: "tone" });
    expect(parseStored(JSON.stringify({ kind: "custom", lo: 2000, hi: 4000 }))).toEqual({ kind: "custom", lo: 2000, hi: 4000 });
  });
});
