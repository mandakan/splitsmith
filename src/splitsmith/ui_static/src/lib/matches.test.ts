import { describe, expect, it } from "vitest";

import type { RecentProjectDetail } from "@/lib/api";
import {
  continueHref,
  continueLabel,
  continueVerb,
  filterMatches,
  formatRelative,
  matchCounts,
  pickContinue,
  progressStates,
} from "@/lib/matches";

function match(over: Partial<RecentProjectDetail> = {}): RecentProjectDetail {
  return {
    path: "/matches/stockholm",
    name: "Stockholm IPSC Open 2026",
    last_opened_at: "2026-09-01T00:00:00Z",
    kind: "match",
    match_id: "stockholm-2026",
    shooter_count: 1,
    stage_count: 12,
    stages_audited: 4,
    video_count: 11,
    match_date: "2026-06-27",
    club: "Stockholm PK",
    last_modified_at: "2026-09-13T10:00:00Z",
    status: "in_progress",
    manual: false,
    shooter_names: ["Mathias Axell"],
    origin: "local",
    next_step: { kind: "audit", shooter_slug: "s_ma", stage_number: 5, stage_name: "B5 Rear" },
    ...over,
  };
}

describe("matchCounts", () => {
  it("counts per status and skips missing folders", () => {
    const c = matchCounts([
      match(),
      match({ status: "exported" }),
      match({ kind: "missing", status: "unknown" }),
      match({ status: "awaiting_footage" }),
    ]);
    expect(c).toEqual({ all: 3, awaiting_footage: 1, in_progress: 1, exported: 1, archived: 0 });
  });
});

describe("filterMatches", () => {
  it("searches name and club, and the path only on a local install", () => {
    const rows = [match(), match({ name: "Bromma Classifier", club: "Bromma", path: "/m/bromma" })];
    expect(filterMatches(rows, "bromma", "all").map((r) => r.name)).toEqual(["Bromma Classifier"]);
    expect(filterMatches(rows, "/m/", "all")).toHaveLength(0);
    expect(filterMatches(rows, "/m/", "all", { localFs: true })).toHaveLength(1);
  });
  it("filters by status; missing folders only show under All", () => {
    const rows = [match(), match({ status: "exported" }), match({ kind: "missing", status: "unknown" })];
    expect(filterMatches(rows, "", "exported")).toHaveLength(1);
    expect(filterMatches(rows, "", "all")).toHaveLength(3);
  });
});

describe("pickContinue", () => {
  it("takes the most recently touched match with a next step, never an archived one", () => {
    const older = match({ name: "Older", last_modified_at: "2026-08-01T00:00:00Z" });
    const newer = match({ name: "Newer", last_modified_at: "2026-09-10T00:00:00Z" });
    const archived = match({ name: "Archived", status: "archived", last_modified_at: "2026-09-12T00:00:00Z" });
    const noStep = match({ name: "No step", next_step: null, last_modified_at: "2026-09-12T00:00:00Z" });
    expect(pickContinue([older, newer, archived, noStep])?.name).toBe("Newer");
    expect(pickContinue([archived, noStep])).toBeNull();
  });
});

describe("continue label, verb and href", () => {
  it("names the audit stage with a leading zero on the ordinal", () => {
    const m = match();
    expect(continueLabel(m.next_step!)).toBe("Audit stage 05 B5 Rear");
    expect(continueVerb(m.next_step!)).toBe("Audit stage 05");
    expect(continueHref(m)).toBe("/match/stockholm-2026/audit/s_ma/5");
  });
  it("points footage at Footage and export at the shooter's export", () => {
    const footage = match({ next_step: { kind: "footage", shooter_slug: null, stage_number: null, stage_name: null } });
    expect(continueLabel(footage.next_step!)).toBe("Add footage");
    expect(continueHref(footage)).toBe("/match/stockholm-2026/ingest");
    const done = match({ next_step: { kind: "export", shooter_slug: "s_ma", stage_number: null, stage_name: null } });
    expect(continueVerb(done.next_step!)).toBe("Export");
    expect(continueHref(done)).toBe("/match/stockholm-2026/export/s_ma");
  });
});

describe("progressStates", () => {
  it("marks audited stages done, the next one in progress, the rest todo", () => {
    expect(progressStates(match({ stage_count: 6, stages_audited: 2 }))).toEqual([
      "done", "done", "progress", "todo", "todo", "todo",
    ]);
    expect(progressStates(match({ stage_count: 3, stages_audited: 3, status: "exported" }))).toEqual([
      "done", "done", "done",
    ]);
    expect(progressStates(match({ stage_count: 2, stages_audited: 0, status: "awaiting_footage", video_count: 0 }))).toEqual([
      "todo", "todo",
    ]);
  });
});

describe("formatRelative", () => {
  it("reads in lower case without leading zeros", () => {
    const now = Date.parse("2026-09-14T12:00:00Z");
    expect(formatRelative(new Date("2026-09-14T09:00:00Z"), now)).toBe("3 h ago");
    expect(formatRelative(new Date("2026-07-14T09:00:00Z"), now)).toBe("2 mo ago");
    expect(formatRelative(new Date("nope"), now)).toBe("—");
  });
});
