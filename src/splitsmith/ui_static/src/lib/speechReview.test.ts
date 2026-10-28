import { describe, expect, it } from "vitest";

import {
  canSave,
  initialRegions,
  isDirty,
  maskedSeconds,
  problems,
  regionProblem,
  type SpeechReviewState,
} from "./speechReview";

const PROTECTED = [{ start: 4.95, end: 5.05 }];

function state(over: Partial<SpeechReviewState> = {}): SpeechReviewState {
  return {
    slug: "s",
    duration: 10,
    proposal: {
      method: "silero-vad-5.1.2",
      masker_version: 1,
      regions: [
        { start: 3, end: 4 },
        { start: 1, end: 2 },
      ],
      protected_overlaps: [],
    },
    masked_regions: [],
    review: null,
    protected: PROTECTED,
    masker_version: 1,
    method: "silero-vad-5.1.2",
    ...over,
  };
}

describe("initialRegions", () => {
  it("starts from the proposal, sorted, before any review", () => {
    expect(initialRegions(state())).toEqual([
      { start: 1, end: 2 },
      { start: 3, end: 4 },
    ]);
  });

  it("starts from what the fixture stores once reviewed", () => {
    const reviewed = state({
      review: { decision: "masked", reviewed_at: "2026-10-28T12:00:00Z", masker_version: 1 },
      masked_regions: [{ start: 6, end: 7, method: "silero-vad-5.1.2", masker_version: 1 }],
    });
    expect(initialRegions(reviewed)).toEqual([{ start: 6, end: 7 }]);
  });

  it("is empty when the detector was unavailable and nothing is stored", () => {
    expect(initialRegions(state({ proposal: null }))).toEqual([]);
  });
});

describe("problems", () => {
  it("names a region over a shot or the beep", () => {
    expect(regionProblem({ start: 4.9, end: 5.0 }, PROTECTED, 10)).toBe("covers a shot or the beep");
  });

  it("names a region with no length or past the clip", () => {
    expect(regionProblem({ start: 2, end: 2 }, PROTECTED, 10)).toBe("no length");
    expect(regionProblem({ start: 9, end: 11 }, PROTECTED, 10)).toBe("past the clip");
  });

  it("names the later of two overlapping regions", () => {
    const found = problems(
      [
        { start: 1, end: 2 },
        { start: 1.5, end: 2.5 },
      ],
      PROTECTED,
      10,
    );
    expect([...found.entries()]).toEqual([[1, "overlaps another"]]);
  });
});

describe("canSave", () => {
  it("needs a region to accept masks", () => {
    expect(canSave("masked", [], PROTECTED, 10)).toBe(false);
    expect(canSave("masked", [{ start: 1, end: 2 }], PROTECTED, 10)).toBe(true);
  });

  it("lets no speech through whatever the editor holds", () => {
    expect(canSave("no_speech", [{ start: 4.9, end: 5.0 }], PROTECTED, 10)).toBe(true);
  });

  it("refuses an exclusion whose masks cover a shot", () => {
    expect(canSave("excluded", [{ start: 4.9, end: 5.0 }], PROTECTED, 10)).toBe(false);
    expect(canSave("excluded", [], PROTECTED, 10)).toBe(true);
  });
});

describe("isDirty", () => {
  it("is always dirty before a review", () => {
    expect(isDirty(state(), [])).toBe(true);
  });

  it("compares with the stored masks to the millisecond", () => {
    const reviewed = state({
      review: { decision: "masked", reviewed_at: "2026-10-28T12:00:00Z", masker_version: 1 },
      masked_regions: [{ start: 6, end: 7, method: "m", masker_version: 1 }],
    });
    expect(isDirty(reviewed, [{ start: 6.0001, end: 7 }])).toBe(false);
    expect(isDirty(reviewed, [{ start: 6.01, end: 7 }])).toBe(true);
  });
});

it("sums the masked seconds", () => {
  expect(
    maskedSeconds([
      { start: 1, end: 2 },
      { start: 3, end: 3.5 },
    ]),
  ).toBeCloseTo(1.5);
});
