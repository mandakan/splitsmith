import { describe, expect, it } from "vitest";
import { momentHref, momentToSearch, parseMoment, resolveMomentView } from "@/lib/moment";

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

  it("momentHref builds pathname?query", () => {
    expect(momentHref("/share/tok/compare/3", { t: 4.32, cam: "alice" })).toBe(
      "/share/tok/compare/3?t=4.32&cam=alice",
    );
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
