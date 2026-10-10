import { describe, expect, it } from "vitest";

import { parseStageLink, resolveStageLink, stageLinkSearch } from "@/lib/stageLink";

const SHOTS = [
  { shot_number: 1, time_from_beep: 1.5 },
  { shot_number: 2, time_from_beep: 1.8 },
  { shot_number: 3, time_from_beep: 4 },
];
const EVENTS = [{ id: "evt-2", start: 2.5 }];

describe("stageLinkSearch", () => {
  it("writes what it carries, rounded to the millisecond", () => {
    expect(stageLinkSearch({ t: 11.25049, shot: 7 })).toBe("?t=11.25&shot=7");
    expect(stageLinkSearch({ t: 2, shot: 3, region: "evt-2" })).toBe("?t=2&shot=3&region=evt-2");
  });

  it("is empty for nothing, and drops what it cannot carry", () => {
    expect(stageLinkSearch({})).toBe("");
    expect(stageLinkSearch({ t: NaN, shot: 0, region: "bad id" })).toBe("");
  });

  it("round-trips through parse", () => {
    expect(parseStageLink(stageLinkSearch({ t: -0.5, shot: 2, region: "evt-1" }))).toEqual({ t: -0.5, shot: 2, region: "evt-1" });
  });
});

describe("parseStageLink", () => {
  it("ignores anything malformed", () => {
    expect(parseStageLink("?t=abc&shot=1.5&region=a%20b")).toEqual({ t: null, shot: null, region: null });
    expect(parseStageLink("?t=&shot=-2")).toEqual({ t: null, shot: null, region: null });
    expect(parseStageLink("")).toEqual({ t: null, shot: null, region: null });
    expect(parseStageLink("?t=Infinity")).toEqual({ t: null, shot: null, region: null });
  });
});

describe("resolveStageLink", () => {
  it("keeps a time and a shot the stage has", () => {
    expect(resolveStageLink({ t: 3, shot: 3, region: null }, SHOTS, EVENTS)).toEqual({ t: 3, shot: 3, region: null });
  });

  it("drops a stale shot and region and takes the shot the time has passed", () => {
    expect(resolveStageLink({ t: 2, shot: 9, region: "evt-9" }, SHOTS, EVENTS)).toEqual({ t: 2, shot: 2, region: null });
  });

  it("seeks to the shot, else the region, when there is no time", () => {
    expect(resolveStageLink({ t: null, shot: 3, region: null }, SHOTS, EVENTS)).toEqual({ t: 4, shot: 3, region: null });
    expect(resolveStageLink({ t: null, shot: null, region: "evt-2" }, SHOTS, EVENTS)).toEqual({ t: 2.5, shot: 2, region: "evt-2" });
  });

  it("has nothing to seek for an empty link", () => {
    expect(resolveStageLink({ t: null, shot: null, region: null }, SHOTS, EVENTS)).toEqual({ t: null, shot: null, region: null });
  });

  it("a time before the first shot names no shot", () => {
    expect(resolveStageLink({ t: 0.2, shot: null, region: null }, SHOTS, EVENTS).shot).toBeNull();
  });
});
