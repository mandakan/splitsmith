import { describe, expect, it } from "vitest";

import { jobTarget } from "@/lib/jobLabels";

const names = new Map([
  ["hostfinalen", "Höstfinalen XI"],
  ["hfo", "HFO Masters 2026"],
]);

describe("jobTarget", () => {
  it("names the match of a match-wide job instead of '(no target)'", () => {
    const job = { match_id: "hostfinalen", stage_number: null, video_id: null };
    expect(jobTarget(job, { matchNames: names, currentMatchId: "hostfinalen" })).toBe("Höstfinalen XI");
  });

  it("says 'whole match' when the name is not known yet", () => {
    const job = { match_id: "new-match", stage_number: null, video_id: null };
    expect(jobTarget(job, { matchNames: names })).toBe("whole match");
  });

  it("keeps a stage job on the current match to its stage and camera", () => {
    const job = { match_id: "hostfinalen", stage_number: 3, video_id: "abcdef123" };
    expect(jobTarget(job, { matchNames: names, currentMatchId: "hostfinalen" })).toBe("stage 03 · cam abcdef");
  });

  it("leads a stage job on another match with that match's name", () => {
    const job = { match_id: "hfo", stage_number: 12, video_id: null };
    expect(jobTarget(job, { matchNames: names, currentMatchId: "hostfinalen" })).toBe("HFO Masters 2026 · stage 12");
  });

  it("is empty for a job with no match and no stage", () => {
    expect(jobTarget({ match_id: null, stage_number: null, video_id: null })).toBe("");
  });
});
