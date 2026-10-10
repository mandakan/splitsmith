import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";

import {
  WALK_DECIDED_EVENT,
  WALK_METHOD,
  countCheck,
  decisionsFrom,
  isDecided,
  stopFlags,
  typicalShotLevel,
  unmarkedBursts,
  walkActionForKey,
  walkHref,
  walkStops,
} from "./walk";

/** 1 ms bins over ``duration`` s of quiet noise with a shot-like burst
 *  (sharp rise, decaying tail) at each onset. */
function envelope(duration: number, onsets: number[], level = 1): { peaks: number[]; duration: number } {
  const n = Math.round(duration * 1000);
  const peaks = new Array<number>(n).fill(0.01);
  for (const t of onsets) {
    const i0 = Math.round(t * 1000);
    for (let k = 0; k < 120 && i0 + k < n; k++) {
      peaks[i0 + k] = Math.max(peaks[i0 + k], k < 3 ? level * (0.3 + 0.35 * k) : level * Math.exp(-(k - 3) / 25));
    }
  }
  return { peaks, duration };
}

function marker(id: string, kind: AuditMarker["kind"], time: number): AuditMarker {
  return {
    id,
    kind,
    time,
    candidateNumber: id.startsWith("cand-") ? Number(id.slice(5)) : null,
    confidence: null,
    peakAmplitude: null,
    note: "",
  };
}

describe("walkStops", () => {
  const peaks = envelope(3, [0.5, 1.0, 1.5, 2.5]);
  const markers = [
    marker("cand-1", "detected", 0.5),
    marker("cand-2", "rejected", 1.0),
    marker("cand-3", "rejected", 0.8),
    marker("manual-shot-4", "manual", 2.5),
  ];

  it("visits every marker, kept or rejected, and every unmarked burst, in time order", () => {
    const stops = walkStops(markers, peaks);
    expect(stops.map((s) => [s.key, s.origin])).toEqual([
      ["cand-1", "kept"],
      ["cand-3", "rejected"],
      ["cand-2", "rejected"],
      ["burst-1500", "burst"],
      ["manual-shot-4", "kept"],
    ]);
  });

  it("places an unmarked burst at its onset", () => {
    expect(unmarkedBursts(markers, peaks)).toEqual([1.5]);
  });

  it("does not make a stop of a burst a rejected candidate already sits on", () => {
    expect(unmarkedBursts(markers, peaks).some((t) => Math.abs(t - 1.0) < 0.05)).toBe(false);
  });

  it("skips bursts before the beep", () => {
    const early = envelope(3, [0.2, 0.5]);
    expect(unmarkedBursts([marker("cand-1", "detected", 0.5)], early, 0.3)).toEqual([]);
    expect(unmarkedBursts([marker("cand-1", "detected", 0.5)], early, 0)).toEqual([0.2]);
  });

  it("finds no bursts when nothing is kept to measure a shot's level by", () => {
    expect(typicalShotLevel([], peaks)).toBe(0);
    expect(unmarkedBursts([], peaks)).toEqual([]);
  });

  it("finds the real shots when every kept shot sits in their tails, and no echo besides", () => {
    // A snapped fixture whose shots all landed 100 ms late, in the tails.
    const real = envelope(3, [0.6, 1.2, 1.8]);
    for (const t of [0.75, 1.35, 1.95]) {
      const i = Math.round(t * 1000);
      for (let k = 0; k < 10; k++) real.peaks[i + k] = Math.max(real.peaks[i + k], 0.08); // an echo bump
    }
    const late = [0.7, 1.3, 1.9].map((t, i) => marker(`cand-${i + 1}`, "detected", t));
    expect(unmarkedBursts(late, real, 0.5)).toEqual([0.6, 1.2, 1.8]);
  });

  it("leaves a reflection in a louder sound's tail alone, but not a fast follow-up shot", () => {
    const env = envelope(3, [0.6, 1.4, 1.55]);
    const i = Math.round(0.7 * 1000); // a reflection 100 ms into the first shot's tail
    for (let k = 0; k < 8; k++) env.peaks[i + k] = Math.max(env.peaks[i + k], 0.3);
    const kept = [marker("cand-1", "detected", 0.6), marker("cand-2", "detected", 1.4)];
    expect(unmarkedBursts(kept, env, 0.5)).toEqual([1.55]);
  });

  it("does not make a stop of a quiet sound", () => {
    const quiet = envelope(3, [0.5, 1.2]);
    for (let i = 1200; i < 1320; i++) quiet.peaks[i] *= 0.2;
    expect(unmarkedBursts([marker("cand-1", "detected", 0.5)], quiet)).toEqual([]);
  });
});

describe("decisions", () => {
  it("reads only this walk version's decisions", () => {
    const events = [
      { kind: WALK_DECIDED_EVENT, payload: { stop: "cand-1", state: "shot", time: 0.5, method: WALK_METHOD } },
      { kind: WALK_DECIDED_EVENT, payload: { stop: "cand-2", state: "shot", time: 0.9, method: "walk-0" } },
      { kind: "marker_kept", payload: { id: "cand-3" } },
    ];
    expect(decisionsFrom(events)).toEqual([{ key: "cand-1", state: "shot", time: 0.5 }]);
  });

  it("lapses a candidate's decision when the shot moves after it, whatever its key", () => {
    // Confirmed at 2.2, then nudged to 2.25 without Enter: a resume must stop
    // there again. Keys are no evidence either: a re-detection renumbers.
    const d = [{ key: "cand-5", state: "shot" as const, time: 2.2 }];
    expect(isDecided("shot", 2.2, d)).toBe(true);
    expect(isDecided("shot", 2.25, d)).toBe(false);
    expect(isDecided("not_shot", 2.2, d)).toBe(false);
  });

  it("holds a manual shot's decision by its time, since a reload renumbers it", () => {
    const d = [{ key: "manual-abc", state: "shot" as const, time: 2.5 }];
    expect(isDecided("shot", 2.5, d)).toBe(true);
    expect(isDecided("shot", 2.502, d)).toBe(false);
  });
});

describe("stopFlags", () => {
  const peaks = envelope(3, [0.5, 1.0]);
  const level = 1;

  it("warns about a shot sitting in silence", () => {
    const m = marker("cand-1", "detected", 2.0);
    const flags = stopFlags({
      stop: { key: m.id, markerId: m.id, time: 2.0, origin: "kept" },
      marker: m,
      markers: [m],
      peaks,
      level,
      snapDisplacementMs: null,
    });
    expect(flags.map((f) => f.text).join(" ")).toMatch(/No burst within 25 ms/);
  });

  it("warns about a second shot close by and a long snap, and points at the rise foot", () => {
    const a = marker("cand-1", "detected", 0.51);
    const b = marker("cand-2", "detected", 0.55);
    const text = stopFlags({
      stop: { key: a.id, markerId: a.id, time: 0.51, origin: "kept" },
      marker: a,
      markers: [a, b],
      peaks,
      level,
      snapDisplacementMs: 57.4,
    })
      .map((f) => f.text)
      .join(" | ");
    expect(text).toMatch(/Snapped 57 ms/);
    expect(text).toMatch(/Another shot 40 ms after/);
    expect(text).toMatch(/Rise foot 1\d ms earlier/);
  });

  it("says nothing about a shot sitting on its onset", () => {
    const m = marker("cand-1", "detected", 0.5);
    const flags = stopFlags({
      stop: { key: m.id, markerId: m.id, time: 0.5, origin: "kept" },
      marker: m,
      markers: [m],
      peaks,
      level,
      snapDisplacementMs: 3,
    });
    expect(flags).toEqual([]);
  });

  it("names an unmarked burst for what it is", () => {
    const flags = stopFlags({
      stop: { key: "burst-1000", markerId: null, time: 1.0, origin: "burst" },
      marker: null,
      markers: [],
      peaks,
      level,
      snapDisplacementMs: null,
    });
    expect(flags[0].text).toMatch(/proposed no candidate/);
  });
});

describe("keys", () => {
  const key = (k: string, mods: Partial<{ shiftKey: boolean; metaKey: boolean; ctrlKey: boolean; altKey: boolean }> = {}) =>
    walkActionForKey({ key: k, shiftKey: false, metaKey: false, ctrlKey: false, altKey: false, ...mods });

  it("maps each key to one action", () => {
    expect(key("Enter")).toEqual({ kind: "confirm" });
    expect(key("s")).toEqual({ kind: "shot" });
    expect(key("X")).toEqual({ kind: "not_shot" });
    expect(key("f")).toEqual({ kind: "rise_foot" });
    expect(key(" ")).toEqual({ kind: "listen" });
    expect(key("Backspace")).toEqual({ kind: "back" });
    expect(key("ArrowLeft")).toEqual({ kind: "nudge", ms: -1 });
    expect(key("ArrowRight", { shiftKey: true })).toEqual({ kind: "nudge", ms: 5 });
  });

  it("leaves modified keys to the page (undo, save, zoom)", () => {
    expect(key("z", { metaKey: true })).toBeNull();
    expect(key("s", { ctrlKey: true })).toBeNull();
    expect(key("+")).toBeNull();
  });
});

describe("countCheck and walkHref", () => {
  it("passes a matching count and words a mismatch", () => {
    expect(countCheck(24, 24).ok).toBe(true);
    expect(countCheck(26, 24)).toMatchObject({ ok: false });
    expect(countCheck(26, 24).text).toMatch(/26 shots, but the stage has 24 rounds/);
    expect(countCheck(3, null).ok).toBe(true);
  });

  it("opens the walk, with the video when there is one", () => {
    expect(walkHref("/f/a b.json", null)).toBe("/review?fixture=%2Ff%2Fa%20b.json&walk=1");
    expect(walkHref("/f/a.json", "/v/x.MOV")).toBe("/review?fixture=%2Ff%2Fa.json&video=%2Fv%2Fx.MOV&walk=1");
  });
});
