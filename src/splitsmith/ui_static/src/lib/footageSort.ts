/**
 * Footage sort (spec 2026-10-01): everything the review page derives from
 * the server's ``SortView``. The engine on the server decides; this module
 * only groups its proposals into the page's sections, words them, and
 * builds the next ``SortDecisions`` from a user action. The page maps the
 * results to primitives and owns nothing.
 */
import type { SortCamera, SortClipView, SortDecisions, SortView } from "./api";

export interface ShooterSection {
  key: string;
  name: string;
  clips: SortClipView[];
}

export interface SortSections {
  /** Cameras whose clock lines up with nothing: one named clip fixes them. */
  anchorCameras: { camera: SortCamera; clips: SortClipView[] }[];
  /** Clips the engine will not decide (ties, two runs on a stage, no time). */
  needsYou: SortClipView[];
  byShooter: ShooterSection[];
  /** No squad run follows them: warm-ups, other squads, walkthroughs. */
  skipped: SortClipView[];
  /** Already in a shooter's project; never imported twice. */
  imported: SortClipView[];
}

function byStageThenStart(a: SortClipView, b: SortClipView): number {
  return (a.proposal.stage ?? 0) - (b.proposal.stage ?? 0) || (a.start ?? "").localeCompare(b.start ?? "");
}

export function sortSections(view: SortView): SortSections {
  const open = view.clips.filter((c) => c.imported_by === null);
  const anchorKeys = new Set(view.cameras.filter((c) => c.clock === "needs_anchor").map((c) => c.key));
  const anchorCameras = view.cameras
    .filter((c) => anchorKeys.has(c.key))
    .map((camera) => ({ camera, clips: open.filter((c) => c.proposal.camera_key === camera.key) }))
    .filter((group) => group.clips.length > 0);
  const byShooter = view.shooters
    .map((s) => ({
      key: s.key,
      name: s.name,
      clips: open
        .filter(
          (c) =>
            c.proposal.shooter === s.key &&
            (c.proposal.confidence === "high" || c.proposal.confidence === "medium"),
        )
        .sort(byStageThenStart),
    }))
    .filter((s) => s.clips.length > 0);
  return {
    anchorCameras,
    needsYou: open.filter(
      (c) => c.proposal.confidence === "needs_you" && !anchorKeys.has(c.proposal.camera_key),
    ),
    byShooter,
    skipped: open.filter((c) => c.proposal.confidence === "skipped"),
    imported: view.clips.filter((c) => c.imported_by !== null),
  };
}

export function importCount(view: SortView): number {
  return view.clips.filter((c) => c.checked).length;
}

// --- decisions -----------------------------------------------------------

function current(view: SortView): SortDecisions {
  return { anchors: [...view.anchors], overrides: [...view.overrides], checked: { ...view.user_checked } };
}

function without(d: SortDecisions, clipId: string): SortDecisions {
  return {
    anchors: d.anchors.filter((a) => a.clip_id !== clipId),
    overrides: d.overrides.filter((o) => o.clip_id !== clipId),
    checked: d.checked,
  };
}

function cameraOf(view: SortView, clipId: string): SortCamera | undefined {
  const clip = view.clips.find((c) => c.clip_id === clipId);
  return view.cameras.find((c) => c.key === clip?.proposal.camera_key);
}

/** Whether naming this clip's run also sets its camera's clock. */
export function setsClock(view: SortView, clipId: string): boolean {
  const clock = cameraOf(view, clipId)?.clock;
  return clock === "needs_anchor" || clock === "anchored";
}

/** The user names a clip's run. On a camera whose clock is unknown (or was
 *  set by an earlier answer) that answer is the camera's one anchor. */
export function assignClip(view: SortView, clipId: string, shooter: string, stage: number): SortDecisions {
  const d = without(current(view), clipId);
  if (setsClock(view, clipId)) {
    const camera = cameraOf(view, clipId);
    const sameCamera = new Set(camera?.clip_ids ?? []);
    d.anchors = d.anchors.filter((a) => !sameCamera.has(a.clip_id));
    d.anchors.push({ clip_id: clipId, shooter, stage });
  } else {
    d.overrides.push({ clip_id: clipId, shooter, stage });
  }
  d.checked = { ...d.checked, [clipId]: true };
  return d;
}

export function skipClip(view: SortView, clipId: string): SortDecisions {
  const d = without(current(view), clipId);
  d.overrides.push({ clip_id: clipId, skip: true });
  d.checked = { ...d.checked, [clipId]: false };
  return d;
}

/** Back to what the engine proposes. */
export function resetClip(view: SortView, clipId: string): SortDecisions {
  const d = without(current(view), clipId);
  const checked = { ...d.checked };
  delete checked[clipId];
  return { ...d, checked };
}

export function setChecked(view: SortView, clipId: string, value: boolean): SortDecisions {
  const d = current(view);
  return { ...d, checked: { ...d.checked, [clipId]: value } };
}

// --- wording -------------------------------------------------------------

export function stageLabel(stage: number): string {
  return `Stage ${String(stage).padStart(2, "0")}`;
}

/** ``42 s``, ``1:42``, ``2 h 05 min``, ``76 days``. */
export function formatSpan(seconds: number): string {
  const s = Math.round(Math.abs(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  if (s < 2 * 86400) return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
  return `${Math.round(s / 86400)} days`;
}

export function shooterName(view: SortView, key: string | null): string {
  return view.shooters.find((s) => s.key === key)?.name ?? key ?? "";
}

/** Why the clip landed where it did, or why it did not. */
export function reasonText(view: SortView, clip: SortClipView): string {
  const p = clip.proposal;
  const r = p.reason;
  if (p.decided_by === "user") return p.confidence === "skipped" ? "Skipped by you" : "Your choice";
  switch (r.issue) {
    case "no_timestamp":
      return "No recording time in the file";
    case "needs_anchor":
      return "Camera clock unknown";
    case "outside_match":
      return "Recorded away from the match days";
    case "no_candidate":
      return "No squad score follows it";
    case "ambiguous":
      return `${shooterName(view, p.shooter)} ${stageLabel(p.stage ?? 0)} and ${shooterName(view, r.rival)} ${stageLabel(r.rival_stage ?? 0)} were scored seconds apart`;
    case "conflict":
      return "Another run is on this stage too";
    default:
      break;
  }
  const scored = r.lead_seconds !== null ? `Scored ${formatSpan(r.lead_seconds)} after the clip started` : "";
  return r.run_size > 1 ? `${scored} · ${r.run_size} cameras on this run` : scored;
}

/** The camera's clock state for its chip, or null when it needs none. */
export function clockText(camera: SortCamera): string | null {
  switch (camera.clock) {
    case "trusted":
      return null;
    case "needs_anchor":
      return "Clock unknown";
    case "no_timestamps":
      return "No recording times";
    case "fitted":
    case "anchored": {
      // The offset is added to the recorded times: negative means the
      // camera's clock ran ahead.
      const way = camera.offset_seconds < 0 ? "fast" : "slow";
      return `Clock ${formatSpan(camera.offset_seconds)} ${way}`;
    }
  }
}

const SCHEME_NAMES: Record<string, string> = {
  IMG: "Phone",
  PXL: "Pixel",
  VID_datetime: "Action cam",
  datetime: "Phone",
  meta: "Glasses",
  GoPro: "GoPro",
};

export function cameraLabel(camera: SortCamera): string {
  const device = camera.model ?? SCHEME_NAMES[camera.scheme] ?? camera.scheme.toUpperCase();
  return camera.folder ? `${device} · ${camera.folder}` : device;
}
