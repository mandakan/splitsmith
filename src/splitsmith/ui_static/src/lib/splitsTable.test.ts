import { describe, expect, it } from "vitest";

import type {
  MatchProject,
  ShooterListEntry,
  StageEntry,
  StageFigures,
  StageScorecard,
  StageStatus,
} from "@/lib/api";
import {
  buildSplitsRows,
  firstPlayable,
  formatHits,
  scoreboardTotals,
  scorecardSyncedAt,
  splitsStats,
} from "@/lib/splitsTable";

interface StageSpec {
  n: number;
  name: string;
  status: StageStatus;
  figures?: StageFigures | null;
  time?: number;
  scorecard?: StageScorecard | null;
  videos?: number;
  updatedAt?: string | null;
}

function scorecard(over: Partial<StageScorecard> = {}): StageScorecard {
  return {
    hit_factor: 2.12,
    stage_points: 103,
    stage_pct: 71.5,
    alphas: 10,
    charlies: 16,
    deltas: 5,
    misses: 0,
    no_shoots: 0,
    procedurals: 0,
    dq: false,
    ...over,
  };
}

function figures(over: Partial<StageFigures> = {}): StageFigures {
  return { draw: 1.84, avg_split: 0.52, fastest_split: 0.31, shot_count: 28, split_count: 9, ...over };
}

function stage(spec: StageSpec): StageEntry {
  return {
    stage_number: spec.n,
    stage_name: spec.name,
    time_seconds: spec.time ?? 0,
    scorecard_updated_at: spec.updatedAt ?? null,
    videos: Array.from({ length: spec.videos ?? (spec.status === "todo" ? 0 : 1) }, (_, i) =>
      // Only `role` is read by the derivation; the rest of StageVideo is irrelevant here.
      ({ path: `raw/v${i}.mp4`, video_id: `v${i}`, role: "primary" }) as unknown as StageEntry["videos"][number],
    ),
    skipped: spec.status === "skipped",
    placeholder: false,
    time_seconds_manual: false,
    status: spec.status,
    stage_rounds: null,
    scorecard: spec.scorecard ?? null,
    figures: spec.figures ?? null,
  };
}

function project(stages: StageSpec[]): MatchProject {
  return {
    schema_version: 1,
    name: "demo",
    created_at: "",
    updated_at: "",
    competitor_name: null,
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: stages.map(stage),
    unassigned_videos: [],
    last_scanned_dir: null,
    raw_dir: null,
    audio_dir: null,
    trimmed_dir: null,
    exports_dir: null,
    probes_dir: null,
    thumbs_dir: null,
    trim_pre_buffer_seconds: 5,
    trim_post_buffer_seconds: 5,
    automation: {},
    nudges_dismissed_stages: [],
    compare_camera: null,
    raw_videos: [],
    origin: "local",
  } as unknown as MatchProject;
}

function shooter(slug: string, name: string): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 0,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

const SOLO = [shooter("me", "Mathias")];

const DEMO: StageSpec[] = [
  { n: 1, name: "B100 Höger", status: "todo", time: 48.6 },
  { n: 2, name: "B100 Vänster", status: "audited", figures: figures(), time: 48.63, scorecard: scorecard(), updatedAt: "2026-06-28T09:40:00Z" },
  { n: 3, name: "B6 Rear", status: "audited", figures: figures({ draw: 1.97, avg_split: 0.385, fastest_split: 0.25, shot_count: 30 }), time: 32.09, scorecard: scorecard({ hit_factor: 2.46, stage_points: 79, alphas: 12, charlies: 6, deltas: 1 }), updatedAt: "2026-06-28T09:41:00Z" },
  { n: 4, name: "B6 Front", status: "ready", figures: figures({ draw: 1.7, avg_split: 0.4, fastest_split: 0.3, shot_count: 18 }), time: 28.27, scorecard: scorecard({ hit_factor: 1.59 }) },
  { n: 5, name: "B5 Rear", status: "skipped", time: 20.81 },
  { n: 6, name: "B3-1", status: "todo", time: 22.8 },
  { n: 7, name: "B3", status: "todo", time: 24.0 },
  { n: 8, name: "Stage B2 Left", status: "todo", time: 26.9 },
];

function soloRows(share = false) {
  return buildSplitsRows({ projects: { me: project(DEMO) }, shooters: SOLO, leadSlug: "me", filterSlug: null, share });
}

describe("buildSplitsRows", () => {
  it("builds one cell per shooter with figures, time and scorecard, lead = leadSlug", () => {
    const rows = soloRows();
    const r2 = rows.find((r) => r.kind === "stage" && r.stageNumber === 2);
    expect(r2?.kind).toBe("stage");
    if (r2?.kind !== "stage") return;
    expect(r2.cells).toHaveLength(1);
    expect(r2.lead).toBe(r2.cells[0]);
    expect(r2.lead).toMatchObject({ slug: "me", shooterName: "Mathias", audited: true, draw: 1.84, avgSplit: 0.52, fastestSplit: 0.31, shotCount: 28, timeSeconds: 48.63 });
    expect(r2.lead?.scorecard?.hit_factor).toBe(2.12);
    expect(r2.auditedCount).toBe(1);
  });

  it("collapses a no-footage run into one row with from/to and both names, keeps a lone one as count 1", () => {
    const rows = soloRows();
    expect(rows[0]).toMatchObject({ kind: "collapsed", from: 1, to: 1, firstName: "B100 Höger", lastName: "B100 Höger", reason: "no_footage", count: 1 });
    const last = rows[rows.length - 1];
    expect(last).toMatchObject({ kind: "collapsed", from: 6, to: 8, firstName: "B3-1", lastName: "Stage B2 Left", reason: "no_footage", count: 3 });
    expect(last.kind === "collapsed" && last.leads.map((c) => c.timeSeconds)).toEqual([22.8, 24.0, 26.9]);
  });

  it("keeps owner not-audited stages (skipped included) as one stage row each, never collapsed", () => {
    const rows = soloRows();
    const kinds = rows.map((r) => (r.kind === "stage" ? `s${r.stageNumber}` : `c${r.from}-${r.to}`));
    expect(kinds).toEqual(["c1-1", "s2", "s3", "s4", "s5", "c6-8"]);
    const r5 = rows[4];
    expect(r5.kind === "stage" && r5.lead?.skipped).toBe(true);
    expect(r5.kind === "stage" && r5.lead?.audited).toBe(false);
  });

  it("share surface collapses every non-audited run as no_video, footage or not", () => {
    const rows = soloRows(true);
    const kinds = rows.map((r) => (r.kind === "stage" ? `s${r.stageNumber}` : `${r.reason}:${r.from}-${r.to}`));
    expect(kinds).toEqual(["no_video:1-1", "s2", "s3", "no_video:4-8"]);
  });

  it("filterSlug picks the lead; a shooter without a project gets no cell", () => {
    const shooters = [shooter("me", "Mathias"), shooter("anna", "Anna"), shooter("erik", "Erik")];
    const projects = {
      me: project(DEMO),
      anna: project([{ n: 2, name: "B100 Vänster", status: "audited", figures: figures({ draw: 1.44 }), time: 40 }]),
      erik: null,
    };
    const all = buildSplitsRows({ projects, shooters, leadSlug: "me", filterSlug: null, share: false });
    const r2 = all.find((r) => r.kind === "stage" && r.stageNumber === 2);
    if (r2?.kind !== "stage") throw new Error("expected stage row");
    expect(r2.cells.map((c) => c.slug)).toEqual(["me", "anna"]);
    expect(r2.lead?.slug).toBe("me");
    expect(r2.auditedCount).toBe(2);
    const anna = buildSplitsRows({ projects, shooters, leadSlug: "me", filterSlug: "anna", share: false });
    const a2 = anna.find((r) => r.kind === "stage" && r.stageNumber === 2);
    expect(a2?.kind === "stage" && a2.lead?.draw).toBe(1.44);
    // Stage 3: Anna's project has no stage 3, so filtering to her yields no cell and the row collapses.
    const a3 = anna.find((r) => r.kind === "collapsed" && r.from <= 3 && r.to >= 3);
    expect(a3).toBeDefined();
  });

  it("on All, a stage stays a row when any shooter's cell is audited even if the lead's is not", () => {
    const shooters = [shooter("me", "Mathias"), shooter("anna", "Anna")];
    const projects = {
      me: project([{ n: 1, name: "S1", status: "ready" }]),
      anna: project([{ n: 1, name: "S1", status: "audited", figures: figures() }]),
    };
    const rows = buildSplitsRows({ projects, shooters, leadSlug: "me", filterSlug: null, share: false });
    expect(rows[0].kind).toBe("stage");
  });
});

describe("splitsStats", () => {
  it("averages draw and split over audited leads, min fastest, sums shots; scored time over every lead with a time", () => {
    const s = splitsStats(soloRows());
    expect(s.avgDraw).toBeCloseTo((1.84 + 1.97) / 2, 6);
    expect(s.avgSplit).toBeCloseTo((0.52 + 0.385) / 2, 6);
    expect(s.fastestSplit).toBe(0.25);
    expect(s.shots).toBe(58);
    expect(s.scoredTime).toBeCloseTo(48.6 + 48.63 + 32.09 + 28.27 + 20.81 + 22.8 + 24.0 + 26.9, 6);
    expect(s.audited).toBe(2);
    expect(s.total).toBe(8);
  });

  it("is null / zero on an empty match", () => {
    expect(splitsStats([])).toEqual({ avgDraw: null, avgSplit: null, fastestSplit: null, shots: 0, scoredTime: null, audited: 0, total: 0 });
  });
});

describe("scoreboardTotals", () => {
  it("sums scored leads only and derives HF from points over time", () => {
    const t = scoreboardTotals(soloRows());
    expect(t).not.toBeNull();
    expect(t?.time).toBeCloseTo(48.63 + 32.09 + 28.27, 6);
    expect(t?.hitFactor).toBeCloseTo((103 + 79 + 103) / (48.63 + 32.09 + 28.27), 6);
    expect(t?.alphas).toBe(32);
    expect(t?.charlies).toBe(38);
    expect(t?.deltas).toBe(11);
    expect(t?.misses).toBe(0);
  });

  it("sums the same stages on the share surface, where scored not-audited stages are collapsed", () => {
    expect(scoreboardTotals(soloRows(true))).toEqual(scoreboardTotals(soloRows()));
    expect(splitsStats(soloRows(true)).scoredTime).toBe(splitsStats(soloRows()).scoredTime);
  });

  it("is null when no lead has a scorecard", () => {
    const rows = buildSplitsRows({ projects: { me: project([{ n: 1, name: "S1", status: "audited", figures: figures() }]) }, shooters: SOLO, leadSlug: "me", filterSlug: null, share: false });
    expect(scoreboardTotals(rows)).toBeNull();
  });
});

describe("helpers", () => {
  it("formatHits omits zero M / NS / P and appends DQ", () => {
    expect(formatHits(scorecard())).toBe("10A 16C 5D");
    expect(formatHits(scorecard({ misses: 2, no_shoots: 1, procedurals: 1, dq: true }))).toBe("10A 16C 5D 2M 1NS 1P DQ");
    expect(formatHits(scorecard({ alphas: null, charlies: null, deltas: null }))).toBe("—");
  });

  it("firstPlayable is the first stage row whose lead is audited", () => {
    expect(firstPlayable(soloRows())).toEqual({ slug: "me", stageNumber: 2 });
    expect(firstPlayable([])).toBeNull();
  });

  it("scorecardSyncedAt is the latest scorecard_updated_at", () => {
    expect(scorecardSyncedAt(project(DEMO))).toBe("2026-06-28T09:41:00Z");
    expect(scorecardSyncedAt(null)).toBeNull();
  });
});
