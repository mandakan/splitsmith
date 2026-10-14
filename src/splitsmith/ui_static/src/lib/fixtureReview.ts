/**
 * Reviewing a corpus fixture in the Audit page (spec 2026-10-14).
 *
 * A fixture is a shot list over its own audio clip; the Audit page reviews it
 * as one stage whose primary camera is the fixture's source video. The review
 * signals (`/api/fixture/review-signals`) become flags on the shot list and
 * the band, and the sign-off counts a snap's shots against the shots it was
 * snapped with, never the scorecard's rounds.
 */
import type { FixtureReviewSignals, MatchProject, StageAudit, StageVideo } from "@/lib/api";
import type { Anomaly } from "@/lib/anomalies";

/** What the page opens: the fixture JSON and its source video, both paths. */
export interface FixtureTarget {
  path: string;
  videoPath: string | null;
}

type FixtureDoc = StageAudit & { fixture_window_in_source?: [number, number] | number[] | null };

/** Where the fixture's clip starts in its source video: the video's clock is
 *  this far ahead of the fixture's (the editor's own mapping). */
export function fixtureVideoOffset(doc: StageAudit): number {
  const w = (doc as FixtureDoc).fixture_window_in_source;
  return Array.isArray(w) && typeof w[0] === "number" ? w[0] : 0;
}

/** The fixture as a one-stage project for the Audit page's derivations. */
export function fixtureProject(doc: StageAudit, videoPath: string | null): MatchProject {
  const now = new Date(0).toISOString();
  const beep = doc.beep_time ?? null;
  const video: StageVideo = {
    path: videoPath ?? "",
    video_id: "fixture",
    role: "primary",
    added_at: now,
    match_timestamp: null,
    processed: { beep: true, shot_detect: true, trim: false },
    beep_time: beep != null ? beep + fixtureVideoOffset(doc) : null,
    beep_source: "manual",
    beep_reviewed: true,
    beep_peak_amplitude: null,
    beep_duration_ms: null,
    beep_confidence: null,
    beep_candidates: [],
    beep_auto_detect_failed: false,
    beep_alignment_confidence: null,
    beep_alignment_delta_ms: null,
    notes: "",
    camera_mount: null,
  } as unknown as StageVideo;
  const stage = {
    stage_number: doc.stage_number ?? 1,
    stage_name: doc.stage_name ?? "Fixture",
    time_seconds: doc.stage_time_seconds ?? 0,
    scorecard_updated_at: null,
    videos: [video],
    skipped: false,
    placeholder: false,
    time_seconds_manual: false,
    stage_rounds: (doc as FixtureDoc & { stage_rounds?: unknown }).stage_rounds ?? null,
    scorecard: null,
  };
  return {
    schema_version: 1,
    name: "Fixture review",
    created_at: now,
    updated_at: now,
    competitor_name: null,
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [stage],
    unassigned_videos: [],
    last_scanned_dir: null,
    raw_dir: null,
    audio_dir: null,
    trimmed_dir: null,
    exports_dir: null,
    probes_dir: null,
    thumbs_dir: null,
    trim_pre_buffer_seconds: 0,
    trim_post_buffer_seconds: 0,
    automation: {},
    nudges_dismissed_stages: [],
    compare_camera: null,
    raw_videos: [],
  } as unknown as MatchProject;
}

/** The review signals as flags: each at its time, so the shot list marks the
 *  shot there and F reaches it. */
export function signalAnomalies(s: FixtureReviewSignals): Anomaly[] {
  const out: Anomaly[] = [];
  for (const m of s.moves) {
    const dir = m.move_ms < 0 ? "earlier" : "later";
    out.push({
      kind: "review_move",
      severity: "warn",
      message: `Leading edge looks ${Math.abs(Math.round(m.move_ms))} ms ${dir}, at ${m.suggested.toFixed(3)} s`,
      shot_number: null,
      time: m.time,
    });
  }
  for (const e of s.edges) {
    out.push({
      kind: "review_edge",
      severity: "warn",
      message: `Snapped ${Math.abs(Math.round(e.displacement_ms))} ms away, at the edge of its snap window`,
      shot_number: null,
      time: e.time,
    });
  }
  for (const e of s.echoes ?? []) {
    out.push({
      kind: "review_echo",
      severity: "warn",
      message: `On an echo? A blast at ${e.earlier.toFixed(3)} s, ${Math.round((e.time - e.earlier) * 1000)} ms before: Earlier rise moves it there`,
      shot_number: null,
      time: e.time,
    });
  }
  for (const u of s.unmarked) {
    out.push({
      kind: "review_unmarked",
      severity: "warn",
      message: "A sound the detector kept, with no shot near it",
      shot_number: null,
      time: u.time,
    });
  }
  return out;
}

/** Two shot lists matched one to one by nearest time within ``tolSeconds``
 *  (a reviewed shot may move by a snap window); what is left over each side. */
function unmatched(a: number[], b: number[], tolSeconds: number): { onlyA: number[]; onlyB: number[] } {
  const usedB = new Set<number>();
  const onlyA: number[] = [];
  for (const t of a) {
    let best = -1;
    let bestD = Infinity;
    b.forEach((u, i) => {
      const d = Math.abs(u - t);
      if (!usedB.has(i) && d <= tolSeconds && d < bestD) {
        best = i;
        bestD = d;
      }
    });
    if (best === -1) onlyA.push(t);
    else usedB.add(best);
  }
  return { onlyA, onlyB: b.filter((_, i) => !usedB.has(i)) };
}

const MATCH_TOL_S = 0.1;

/** The sign-off line. A snap counts against the shots it was snapped with,
 *  naming what was added or removed; the scorecard's rounds are a note. */
export function signOffLine(kept: number[], s: FixtureReviewSignals | null): { ok: boolean; text: string } {
  const rounds = s?.rounds != null ? ` The scorecard says ${s.rounds} rounds.` : "";
  const ref = s?.reference ?? null;
  if (!ref) return { ok: true, text: `${kept.length} shots.${rounds}` };
  const { onlyA: added, onlyB: removed } = unmatched(kept, ref.times, MATCH_TOL_S);
  if (added.length === 0 && removed.length === 0) {
    return { ok: true, text: `${kept.length} shots, as snapped from the primary.${rounds}` };
  }
  const parts = [
    added.length ? `added at ${added.map((t) => `${t.toFixed(2)} s`).join(", ")}` : null,
    removed.length ? `removed at ${removed.map((t) => `${t.toFixed(2)} s`).join(", ")}` : null,
  ].filter(Boolean);
  return {
    ok: false,
    text: `${kept.length} shots against ${ref.times.length} snapped from the primary: ${parts.join("; ")}.`,
  };
}

/** The fixture's stem (its slug in the review queue) from its JSON path. */
export function fixtureStem(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.json$/, "");
}

/** The Audit page reviewing a fixture, with its source video when that is
 *  on this disk. */
export function fixtureAuditHref(auditPath: string, videoPath: string | null): string {
  const video = videoPath ? `&video=${encodeURIComponent(videoPath)}` : "";
  return `/review/audit?fixture=${encodeURIComponent(auditPath)}${video}`;
}
