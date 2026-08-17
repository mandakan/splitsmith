/**
 * Review-state predicates + row tags (batch-promote gap, #331 follow-up).
 *
 * 102 of 126 corpus fixtures entered via batch promote-stages, which
 * stamps ``promoted_at`` but no anchor block; the old predicates keyed
 * on ``anchor_slug`` alone and treated them all as reviewed. The old
 * strict-equality "mismatch" tag also lit up 91% of the corpus for
 * normal IPSC makeup shots. These tests pin the replacements.
 */
import { describe, expect, it } from "vitest";

import type { LabFixtureRecord } from "@/lib/api";
import { fixtureTags } from "@/pages/dev/DevCorpus";

import { filterFixtures, isPromoted, needsReview } from "./corpusFilter";

function record(over: Partial<LabFixtureRecord> = {}): LabFixtureRecord {
  return {
    slug: "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
    audit_path: "/fixtures/x.json",
    audio_path: "/fixtures/x.wav",
    has_audio: true,
    n_shots: 12,
    expected_rounds: 12,
    stage_time_seconds: 20,
    beep_time: 1.5,
    source: null,
    source_video: null,
    audit_mtime: 1,
    audio_mtime: 1,
    anchor_slug: null,
    event_id: "hfo-masters-2026:1",
    promoted_at: null,
    n_labeled_shots: 0,
    n_labeled_rejects: 0,
    in_calibration: false,
    ...over,
  };
}

describe("needsReview / isPromoted", () => {
  it("batch-promoted fixture pends until a human label lands", () => {
    const batch = record({ promoted_at: "2026-08-14T12:00:00+00:00" });
    expect(isPromoted(batch)).toBe(true);
    expect(needsReview(batch)).toBe(true);
    expect(needsReview(record({ ...batch, n_labeled_shots: 1 }))).toBe(false);
    expect(needsReview(record({ ...batch, n_labeled_rejects: 1 }))).toBe(false);
  });

  it("anchor fixture pends regardless of labels (they are copies)", () => {
    const anchored = record({ anchor_slug: "stage-shots-a-2026-stage1", n_labeled_shots: 5 });
    expect(needsReview(anchored)).toBe(true);
  });

  it("hand-dropped legacy fixture never pends", () => {
    expect(needsReview(record())).toBe(false);
  });
});

describe("filterFixtures", () => {
  const batchPending = record({ promoted_at: "2026-08-14T12:00:00+00:00" });
  const batchLabeled = record({
    slug: "stage-shots-hfo-masters-2026-stage2-s0fe3d797",
    promoted_at: "2026-08-14T12:40:00+00:00",
    n_labeled_shots: 3,
  });
  const inModel = record({ slug: "stage-shots-blacksmith-2026-stage6", in_calibration: true });

  it("'pending' matches batch-promoted unlabeled fixtures", () => {
    const out = filterFixtures([batchPending, batchLabeled, inModel], "", "pending");
    expect(out.map((f) => f.slug)).toEqual([batchPending.slug]);
  });

  it("'promoted' matches promoted_at without an anchor block", () => {
    const out = filterFixtures([batchPending, batchLabeled, inModel], "", "promoted");
    expect(out.map((f) => f.slug)).toEqual([batchPending.slug, batchLabeled.slug]);
  });

  it("'not-in-model' excludes calibration members", () => {
    const out = filterFixtures([batchPending, inModel], "", "not-in-model");
    expect(out.map((f) => f.slug)).toEqual([batchPending.slug]);
  });
});

describe("fixtureTags", () => {
  it("does not flag makeup shots (n_shots above the round minimum)", () => {
    const keys = fixtureTags(record({ n_shots: 16, expected_rounds: 12 })).map((t) => t.key);
    expect(keys.some((k) => k.includes("mismatch") || k.includes("short"))).toBe(false);
  });

  it("flags a fixture with fewer shots than the round minimum", () => {
    const keys = fixtureTags(record({ n_shots: 10, expected_rounds: 12 })).map((t) => t.key);
    expect(keys).toContain("short 2");
  });

  it("always states calibration membership", () => {
    expect(fixtureTags(record({ in_calibration: true })).map((t) => t.key)).toContain("in model");
    expect(fixtureTags(record()).map((t) => t.key)).toContain("not in model");
  });

  it("marks batch-promoted unlabeled fixtures as needing review", () => {
    const keys = fixtureTags(record({ promoted_at: "2026-08-14T12:00:00+00:00" })).map((t) => t.key);
    expect(keys).toContain("promoted");
    expect(keys).toContain("needs review");
  });
});
