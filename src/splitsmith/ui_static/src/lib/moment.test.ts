import { describe, expect, it } from "vitest";
import { momentHref, momentToSearch, parseMoment, resolveMomentView, WHO_MAX } from "@/lib/moment";

describe("momentToSearch / parseMoment", () => {
  it("round-trips a full compare moment", () => {
    const m = { t: 4.32, cam: "alice", who: ["alice", "bob"] };
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("round-trips a bare results moment and formats t to 2 decimals", () => {
    const params = momentToSearch({ t: 1.005 });
    expect(params.toString()).toBe("t=1.00");
    expect(parseMoment(params)).toEqual({ t: 1 });
  });

  it("keeps negative pre-beep times", () => {
    expect(parseMoment(new URLSearchParams("t=-1.5"))).toEqual({ t: -1.5 });
  });

  it("returns null without t, or with junk / non-finite / out-of-range t", () => {
    expect(parseMoment(new URLSearchParams(""))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=abc"))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=Infinity"))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=3600.01"))).toBeNull();
  });

  it("ignores unknown params and drops empty who entries", () => {
    const m = parseMoment(new URLSearchParams("t=2&foo=bar&who=alice,,"));
    expect(m).toEqual({ t: 2, who: ["alice"] });
  });

  it("clamps an out-of-range t so our own links never mint a dead moment", () => {
    expect(parseMoment(momentToSearch({ t: 3700 }))).toEqual({ t: 3600 });
    expect(parseMoment(momentToSearch({ t: -3700 }))).toEqual({ t: -3600 });
  });

  it("momentHref builds pathname?query", () => {
    expect(momentHref("/share/tok/compare/3", { t: 4.32, cam: "alice" })).toBe(
      "/share/tok/compare/3?t=4.32&cam=alice",
    );
  });

  it("caps who at WHO_MAX entries, matching the backend's _WHO_MAX", () => {
    expect(WHO_MAX).toBe(12);
    const who = Array.from({ length: 15 }, (_, i) => `shooter-${i}`);
    const params = momentToSearch({ t: 1, who });
    expect(params.get("who")).toBe(who.slice(0, WHO_MAX).join(","));
    expect(params.get("who")?.split(",")).toHaveLength(WHO_MAX);
  });
});

describe("resolveMomentView", () => {
  const roster = new Set(["alice", "bob"]);

  it("keeps only slugs present in the roster", () => {
    expect(resolveMomentView({ t: 1, cam: "alice", who: ["alice", "ghost"] }, roster)).toEqual({
      cam: "alice",
      who: ["alice"],
    });
  });

  it("returns nulls when nothing valid remains", () => {
    expect(resolveMomentView({ t: 1, cam: "ghost", who: ["ghost"] }, roster)).toEqual({
      cam: null,
      who: null,
    });
  });
});

describe("camera pick (v=)", () => {
  it("round-trips a results-form camera index", () => {
    const m = { t: 1.5, v: 2 };
    expect(momentToSearch(m).get("v")).toBe("2");
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("round-trips a compare-form per-shooter map", () => {
    const m = { t: 1.5, v: { alice: 1, bob: 2 } };
    expect(momentToSearch(m).get("v")).toBe("alice:1,bob:2");
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("never serializes index 0 (primary = absence)", () => {
    expect(momentToSearch({ t: 1, v: 0 }).get("v")).toBeNull();
    expect(momentToSearch({ t: 1, v: { alice: 0 } }).get("v")).toBeNull();
  });

  it("drops junk v tokens and keeps the valid ones", () => {
    expect(parseMoment(new URLSearchParams("t=1&v=abc"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=-1"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=999"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=alice:1,ghost:,:2,bob:999"))).toEqual({
      t: 1,
      v: { alice: 1 },
    });
  });

  it("caps the record form at WHO_MAX entries", () => {
    const v = Object.fromEntries(
      Array.from({ length: 15 }, (_, i) => [`s${i}`, 1]),
    );
    const parsed = parseMoment(momentToSearch({ t: 1, v }));
    expect(Object.keys((parsed?.v ?? {}) as Record<string, number>)).toHaveLength(WHO_MAX);
  });
});
