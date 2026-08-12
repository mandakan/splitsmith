import { describe, expect, it } from "vitest";

import type { CoachShot } from "@/lib/api";
import { buildCoachPatch, buildUndoPatch } from "@/lib/coachPatch";

function shot(overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    id: null,
    shot_number: 3,
    ms_after_beep: 1500,
    time_from_beep: 1.5,
    time_absolute: 3.5,
    split: 0.42,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("buildCoachPatch", () => {
  it("class change becomes a manual override", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "movement", note: "" })).toEqual({
      interval_class: "movement",
      interval_class_source: "manual",
    });
  });

  it("same class and same note is a no-op (null)", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "split", note: "" })).toBeNull();
    expect(buildCoachPatch(shot({ coaching_note: "wide" }), { intervalClass: "split", note: "wide" })).toBeNull();
  });

  it("note-only change patches the note, trimmed", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "split", note: "  push harder  " })).toEqual({
      coaching_note: "push harder",
    });
  });

  it("emptying an existing note clears it", () => {
    expect(buildCoachPatch(shot({ coaching_note: "old" }), { intervalClass: "split", note: " " })).toEqual({
      clear_note: true,
    });
  });

  it("null draft class leaves classification untouched", () => {
    expect(buildCoachPatch(shot({ interval_class: null, interval_class_source: null }), { intervalClass: null, note: "n" })).toEqual({
      coaching_note: "n",
    });
  });

  it("a whitespace-padded stored note does not ride a class-only patch", () => {
    expect(
      buildCoachPatch(shot({ coaching_note: " wide entry " }), {
        intervalClass: "movement",
        note: " wide entry ",
      }),
    ).toEqual({ interval_class: "movement", interval_class_source: "manual" });
  });
});

describe("buildUndoPatch", () => {
  it("restores a prior manual class verbatim", () => {
    const prev = shot({ interval_class: "reload", interval_class_source: "manual" });
    expect(buildUndoPatch(prev, { interval_class: "split", interval_class_source: "manual" })).toEqual({
      interval_class: "reload",
      interval_class_source: "manual",
    });
  });

  it("reverts a prior auto class by clearing (server re-derives)", () => {
    expect(buildUndoPatch(shot(), { interval_class: "movement", interval_class_source: "manual" })).toEqual({
      clear_class: true,
    });
  });

  it("restores a prior note, clears a previously-absent note", () => {
    expect(buildUndoPatch(shot({ coaching_note: "old" }), { clear_note: true })).toEqual({
      coaching_note: "old",
    });
    expect(buildUndoPatch(shot(), { coaching_note: "new" })).toEqual({ clear_note: true });
  });

  it("only inverts touched fields", () => {
    const prev = shot({ coaching_note: "keep" });
    expect(buildUndoPatch(prev, { interval_class: "movement", interval_class_source: "manual" })).toEqual({
      clear_class: true,
    });
  });
});
