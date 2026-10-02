import { describe, expect, it } from "vitest";

import {
  allMuted,
  audibleSlugs,
  audioFromMoment,
  audioToMoment,
  solo,
  tileVolume,
  toggleAll,
  toggleMute,
} from "./compareAudio";

const ALL = ["anna", "bob", "cleo", "dan"];

describe("compare audio mix", () => {
  it("hears everyone by default, each at 1/N, and a lone shooter at full level", () => {
    expect(audibleSlugs(ALL, new Set())).toEqual(ALL);
    expect(tileVolume(ALL, new Set())).toBe(0.25);
    expect(tileVolume(ALL, new Set(["anna", "bob", "cleo"]))).toBe(1);
    expect(tileVolume(ALL, new Set(ALL))).toBe(0);
  });

  it("a click mutes or unmutes one shooter", () => {
    const m = toggleMute(new Set(), "bob");
    expect([...m]).toEqual(["bob"]);
    expect([...toggleMute(m, "bob")]).toEqual([]);
  });

  it("Alt-click hears only that shooter, and again hears everyone", () => {
    const only = solo(new Set(["cleo"]), "bob", ALL);
    expect(audibleSlugs(ALL, only)).toEqual(["bob"]);
    expect([...solo(only, "bob", ALL)]).toEqual([]);
  });

  it("one button mutes all, and unmutes all once everyone is muted", () => {
    const m = toggleAll(new Set(["anna"]), ALL);
    expect(allMuted(ALL, m)).toBe(true);
    expect([...toggleAll(m, ALL)]).toEqual([]);
  });

  it("moment links: one heard is cam= (old links read the same), otherwise the muted list", () => {
    expect(audioToMoment(ALL, new Set(["anna", "cleo", "dan"]))).toEqual({ cam: "bob" });
    expect(audioToMoment(ALL, new Set(["dan"]))).toEqual({ mute: ["dan"] });
    expect(audioToMoment(ALL, new Set())).toEqual({});
    expect([...audioFromMoment(ALL, { cam: "bob" })]).toEqual(["anna", "cleo", "dan"]);
    expect([...audioFromMoment(ALL, { mute: ["dan", "nobody"] })]).toEqual(["dan"]);
    expect([...audioFromMoment(ALL, { cam: "nobody" })]).toEqual([]);
  });
});
