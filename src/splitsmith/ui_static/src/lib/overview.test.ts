import { describe, expect, it } from "vitest";

import type { Job, ShooterListEntry, TriageCell, TriageResponse } from "@/lib/api";
import {
  buildOverviewRows,
  formatClock,
  nextAction,
  overviewStats,
  rowAction,
} from "@/lib/overview";

function cell(over: Partial<TriageCell>): TriageCell {
  return {
    slug: "s1",
    shooter_name: "Mathias Axell",
    stage_number: 1,
    stage_name: "Stage",
    status: "todo",
    beep_confidence: null,
    beep_tone_offset_ms: null,
    anomalies: [],
    needs_attention: null,
    video_count: 0,
    beep_time: null,
    beep_reviewed: false,
    shot_count: 0,
    draw: null,
    avg_split: null,
    time_seconds: 0,
    ...over,
  };
}

function shooter(slug: string, name: string): ShooterListEntry {
  return { identity: { accent: null, club: null, logo: null },
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 4,
    video_count: 1,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

// Four stages off the Stockholm match: no footage, audited, detected with
// flags, and a beep still to confirm.
// Without the tone threshold, so the "older server" case below can spread it as is.
const STOCKHOLM_BASE: Omit<TriageResponse, "beep_tone_flag_ms"> = {
  beep_low_confidence_threshold: 0.5,
  flagged_count: 0,
  cells: [
    cell({ stage_number: 1, stage_name: "B100 Höger", status: "todo", time_seconds: 48.6 }),
    cell({
      stage_number: 3,
      stage_name: "B6 Rear",
      status: "audited",
      video_count: 1,
      beep_time: 5.32,
      beep_reviewed: true,
      beep_confidence: 0.91,
      beep_tone_offset_ms: null,
      shot_count: 30,
      draw: 1.97,
      avg_split: 0.386,
      time_seconds: 32.12,
    }),
    cell({
      stage_number: 6,
      stage_name: "B5 All",
      status: "in_progress",
      video_count: 1,
      beep_time: 6.01,
      beep_reviewed: true,
      beep_confidence: 0.88,
      beep_tone_offset_ms: null,
      shot_count: 33,
      anomalies: [{}, {}, {}, {}] as never,
      draw: 1.93,
      avg_split: 0.44,
      time_seconds: 38.2,
    }),
    cell({
      stage_number: 10,
      stage_name: "B3",
      status: "ready",
      video_count: 1,
      beep_time: 4.9,
      beep_reviewed: false,
      beep_confidence: 0.42,
      beep_tone_offset_ms: null,
      time_seconds: 24.0,
    }),
  ],
};
const STOCKHOLM: TriageResponse = { ...STOCKHOLM_BASE, beep_tone_flag_ms: 20 };

const ONE = [shooter("s1", "Mathias Axell")];

describe("buildOverviewRows", () => {
  it("assigns one action per stage in loop order", () => {
    const rows = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [] });
    expect(rows.map((r) => r.lead!.action.kind)).toEqual([
      "add_footage",
      "splits",
      "audit",
      "confirm_beep",
    ]);
    const six = rows[2].lead!;
    expect(six.action).toEqual({ kind: "audit", accept: true });
    expect(six.flagCount).toBe(4);
    expect(six.provisional).toBe(true);
    expect(rows[1].lead!.audited).toBe(true);
    expect(rows[1].lead!.provisional).toBe(false);
  });

  it("points nextAction at the first audit or confirm in stage order", () => {
    const rows = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [] });
    expect(nextAction(rows)?.row.stageNumber).toBe(6);
  });

  it("a running job for a stage makes it 'running' and moves nextAction on", () => {
    const job = {
      id: "j",
      kind: "shot_detect",
      match_id: "m",
      stage_number: 6,
      shooter_slug: "s1",
      video_id: null,
      status: "running",
      progress: null,
      message: null,
      error: null,
      cancel_requested: false,
      acknowledged: false,
    } as Job;
    const rows = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [job] });
    expect(rows[2].lead!.action.kind).toBe("running");
    expect(nextAction(rows)?.row.stageNumber).toBe(10);
  });

  it("stats average over audited cells only and sum scored time", () => {
    const rows = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [] });
    expect(overviewStats(rows)).toEqual({
      audited: 1,
      total: 4,
      needsFootage: 1,
      avgDraw: 1.97,
      avgSplit: 0.386,
      scoredTime: 48.6 + 32.12 + 38.2 + 24.0,
    });
  });

  it("multi-shooter: the parent carries the lead's figures and the worst action", () => {
    const triage: TriageResponse = {
      ...STOCKHOLM,
      cells: [
        STOCKHOLM.cells[1],
        cell({
          ...STOCKHOLM.cells[1],
          slug: "s2",
          shooter_name: "Anna",
          status: "in_progress",
          draw: 2.1,
          avg_split: 0.47,
        }),
      ],
    };
    const rows = buildOverviewRows({
      triage,
      shooters: [shooter("s1", "Mathias Axell"), shooter("s2", "Anna")],
      leadSlug: "s1",
      jobs: [],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].cells.map((c) => c.slug)).toEqual(["s1", "s2"]);
    expect(rows[0].lead?.slug).toBe("s1");
    expect(rows[0].auditedCount).toBe(1);
    expect(rows[0].filmedCount).toBe(2);
    expect(rows[0].worst.kind).toBe("audit");
  });
});

describe("rowAction", () => {
  it("skipped stages have no action; a confirmed low-confidence beep is still confirmed", () => {
    const base = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [] })[2].lead!;
    expect(rowAction({ ...base, status: "skipped" }, 0.5).kind).toBe("none");
    expect(rowAction({ ...base, beepConfidence: 0.3, beepReviewed: true }, 0.5).kind).toBe("audit");
  });

  it("a stage whose beep is still being detected is running, not a beep to confirm", () => {
    const base = buildOverviewRows({ triage: STOCKHOLM, shooters: ONE, leadSlug: "s1", jobs: [] })[2].lead!;
    const waiting = { ...base, beepTime: null, beepReviewed: false };
    expect(rowAction({ ...waiting, running: true }, 0.5).kind).toBe("running");
    // No job left and still no beep: now it is the user's to place.
    expect(rowAction({ ...waiting, running: false }, 0.5).kind).toBe("confirm_beep");
  });
});

describe("formatClock", () => {
  it("minutes above sixty seconds, two decimals below", () => {
    expect(formatClock(316.9)).toBe("5:16.9");
    expect(formatClock(48.63)).toBe("48.63");
    expect(formatClock(60)).toBe("1:00.0");
  });
});

describe("the tone check (spec 2026-10-17)", () => {
  const checkBeep = (offset: number | null, flag: number | null = 20) =>
    buildOverviewRows({
      triage: {
        ...STOCKHOLM_BASE,
        ...(flag === null ? {} : { beep_tone_flag_ms: flag }),
        cells: [cell({ video_count: 1, beep_time: 5, beep_reviewed: true, beep_tone_offset_ms: offset })],
      } as TriageResponse,
      shooters: [shooter("s1", "Mathias Axell")],
      leadSlug: null,
      jobs: [],
    })[0].cells[0].checkBeep;

  it("flags a beep at or past the threshold, and only then", () => {
    expect(checkBeep(-51)).toBe(-51);
    expect(checkBeep(20)).toBe(20);
    expect(checkBeep(19.9)).toBeNull();
    expect(checkBeep(null)).toBeNull();
  });

  it("never flags against an older server that sends no threshold", () => {
    expect(checkBeep(-51, null)).toBeNull();
  });
});
