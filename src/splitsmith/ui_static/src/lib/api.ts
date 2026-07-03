/**
 * Typed client for the splitsmith UI backend.
 *
 * Mirrors the Pydantic models in src/splitsmith/ui/project.py and the
 * endpoint surface in src/splitsmith/ui/server.py. When the backend grows,
 * extend this file rather than scattering fetch() calls across the SPA.
 */

export type VideoRole = "primary" | "secondary" | "ignored";
/** ``"aligned"`` means the in-stream beep detector failed on a secondary
 *  and ``cross_align`` projected the primary's beep into the secondary's
 *  timeline. Treated as a *suggestion* the user must verify on the
 *  waveform picker before "Mark reviewed". */
export type BeepSource = "auto" | "manual" | "aligned";

/** One ranked beep candidate emitted by ``detect-beep`` (issue #22).
 *  ``score`` is the composite ranking score: silence-preference tilted by
 *  tonal concentration and duration plausibility. ``confidence`` is the
 *  calibrated [0, 1] threshold value (#219 / #220 layer 3a) -- >=0.7
 *  the auto-trust band, < 0.6 lands in the HITL queue. The raw
 *  ``silence_score`` and ``tonal_score`` components are surfaced so the
 *  candidate card can explain *why* a confidence is what it is.
 *  ``beep_candidates[0]`` matches the promoted ``beep_time`` on the
 *  parent ``StageVideo``. */
export interface BeepCandidate {
  time: number;
  score: number;
  peak_amplitude: number;
  duration_ms: number;
  silence_score: number;
  tonal_score: number;
  confidence: number;
}

/** Response from POST /api/stages/{n}/videos/{vid}/beep/snap. The user
 *  placed a marker by ear; the server returns the rise-foot leading edge
 *  of the strongest run inside a tight window so the SPA can offer the
 *  refinement as Accept / Dismiss. */
export interface BeepSnapResult {
  snapped_time: number;
  delta: number;
  peak_amplitude: number;
  score: number;
  duration_ms: number;
}

export interface StageVideo {
  path: string;
  /** Stable URL-safe id derived from ``path``. Used by per-video API
   *  endpoints (/api/stages/{n}/videos/{video_id}/...) so the SPA can
   *  target a specific camera without re-encoding the path. Computed
   *  server-side; do not generate it client-side. */
  video_id: string;
  role: VideoRole;
  added_at: string;
  /** The recording-finished time the match heuristic uses for this video
   *  (UTC ISO-8601). Captured at registration via the canonical
   *  ``video_match.video_timestamp`` helper so the SPA, the CLI, and the
   *  classifier agree. Null on legacy projects -- the timeline omits the
   *  tick rather than guessing. */
  match_timestamp: string | null;
  processed: { beep: boolean; shot_detect: boolean; trim: boolean };
  beep_time: number | null;
  beep_source: BeepSource | null;
  /** Has the user explicitly listened to the detected beep and
   *  confirmed it (issue #71)? Auto-detected beeps default to false
   *  until the user clicks "Mark reviewed" or types a manual override.
   *  Resets when ``beep_time`` changes. The pipeline doesn't gate on
   *  this -- it's a UI nudge to reduce wrong-beep regressions. */
  beep_reviewed: boolean;
  beep_peak_amplitude: number | null;
  beep_duration_ms: number | null;
  /** Calibrated detector confidence in [0, 1] for the chosen beep
   *  (#219 / #220 layer 3a). Manual entries clamp to 1.0; auto-detected
   *  beeps carry the value the formula in ``beep_detect`` produced.
   *  Null on legacy projects from before this field existed and on
   *  ``beep_source == "aligned"`` where the detector confidence isn't
   *  meaningful for a secondary. The HITL queue uses this against
   *  ``automation.beep_low_confidence_threshold`` to decide whether
   *  the beep needs review. */
  beep_confidence: number | null;
  /** Ranked alternative candidates from the most recent auto-detection run.
   *  Empty when the project predates issue #22 or after a manual override. */
  beep_candidates: BeepCandidate[];
  /** True when both in-stream beep detection AND the cross-correlation
   *  fallback ran on a secondary and neither produced a usable timestamp.
   *  Distinguishes "tried and failed; pick on waveform" from "not started"
   *  in the no-beep UI. False on primaries (in-stream failure is fatal
   *  there). */
  beep_auto_detect_failed: boolean;
  /** Peak-to-runner-up confidence ratio from the cross-correlation
   *  fallback / sanity check. Populated whenever cross-align ran on a
   *  secondary -- as a promoted suggestion (``beep_source="aligned"``),
   *  as a below-floor diagnostic (``beep_auto_detect_failed=true``), or
   *  as a sanity check after in-stream succeeded. Null otherwise. */
  beep_alignment_confidence: number | null;
  /** Difference (in-stream minus cross-align) in milliseconds when both
   *  methods produced a usable timestamp on a secondary. The SPA flags
   *  large disagreements (> ~250 ms) since they typically mean the
   *  in-stream detector locked onto a steel-strike or other transient
   *  rather than the buzzer. Null when only one method ran. */
  beep_alignment_delta_ms: number | null;
  notes: string;
  /** Selects the per-class threshold set used by the ensemble (issue #143):
   *  ``head|chest|belt|helmet`` -> headcam; ``hand|tripod|monopod|gimbal``
   *  -> handheld. Stamped heuristically from camera make at register time
   *  and overridable via PATCH .../camera-mount. ``null`` falls back to
   *  the artifact's default class. */
  camera_mount: CameraMount | null;
  /** Camera make/model from ffprobe, or null when the QuickTime tag is
   *  missing (Meta Vanguard glasses are the present example). Drives the
   *  per-camera-model amplitude-floor lookup (#304); unknown values fall
   *  back to the generic-headcam floor. Overridable via PATCH
   *  .../camera-model when ffprobe yielded nothing. */
  camera_make: string | null;
  camera_model: string | null;
}

/** Mirror of ``splitsmith.ensemble.calibration.normalize_camera_model_key``.
 *  Lower-cases and whitespace-collapses both fields so the SPA can
 *  cross-reference a project's saved make/model against the calibration's
 *  lookup table without round-tripping to the server. */
export function normalizeCameraModelKey(
  make: string | null | undefined,
  model: string | null | undefined,
): string | null {
  if (!make || !model) return null;
  const norm = (s: string) => s.trim().toLowerCase().split(/\s+/).join(" ");
  const m = norm(make);
  const mo = norm(model);
  if (!m || !mo) return null;
  return `${m} ${mo}`;
}

export interface CalibratedCameraModel {
  /** Canonical lookup key the runtime uses (lower-cased ``"<make> <model>"``). */
  key: string;
  /** Original-case make/model for display. */
  make: string;
  model: string;
  /** Per-model within-stage amplitude floor (#304). Informational on the
   *  dropdown -- the runtime resolves it from the calibration. */
  amp_floor: number;
}

export type CameraMount =
  | "head"
  | "chest"
  | "belt"
  | "helmet"
  | "hand"
  | "tripod"
  | "monopod"
  | "gimbal";

export const CAMERA_MOUNTS: readonly CameraMount[] = [
  "head",
  "chest",
  "belt",
  "helmet",
  "hand",
  "tripod",
  "monopod",
  "gimbal",
] as const;

/** Per-stage lifecycle state, mirror of :class:`splitsmith.ui.project.StageStatus`.
 *  Computed by the backend on every GET project payload; the SPA never
 *  recomputes this -- see ``lib/stageStatus.ts`` for display mapping. */
export type StageStatus =
  | "todo"
  | "partial"
  | "ready"
  | "in_progress"
  | "audited"
  | "skipped";

/** Round count + target breakdown for a stage, mirrored from the backend
 *  ``config.StageRounds``. Sourced from SSI Scoreboard on import; any field
 *  may be null for manually-created stages or older imports. */
export interface StageRounds {
  expected: number | null;
  paper_targets: number | null;
  steel_targets: number | null;
}

export interface StageEntry {
  stage_number: number;
  stage_name: string;
  time_seconds: number;
  scorecard_updated_at: string | null;
  videos: StageVideo[];
  skipped: boolean;
  placeholder: boolean;
  /** True when the duration was entered manually (POST /api/stages/{n}/time)
   *  rather than imported from a scoreboard. Preserved across scoreboard
   *  syncs so a manual value isn't clobbered. */
  time_seconds_manual: boolean;
  /** Lifecycle status computed by the backend (see :class:`StageStatus`).
   *  Optional in the type because legacy responses may omit it; callers
   *  should fall back via :func:`deriveStageStatus` when missing. */
  status?: StageStatus;
  /** Round/target metadata already sent by the project API; surfaced in the
   *  Ingest stage reference panel. Null when the match carries no round data. */
  stage_rounds: StageRounds | null;
}

export interface MatchProject {
  schema_version: number;
  name: string;
  created_at: string;
  updated_at: string;
  competitor_name: string | null;
  scoreboard_match_id: string | null;
  scoreboard_content_type: number | null;
  selected_shooter_id: number | null;
  selected_competitor_id: number | null;
  // Server-computed PII-free token for the pinned shooter
  // ("s" + first 8 chars of sha256("ssi-<id>")). Null when no shooter
  // is pinned. Used to suffix fixture slugs so promotions on different
  // shooters at the same match/stage don't collide on filename.
  shooter_token: string | null;
  match_date: string | null;
  stages: StageEntry[];
  unassigned_videos: StageVideo[];
  last_scanned_dir: string | null;
  raw_dir: string | null;
  audio_dir: string | null;
  trimmed_dir: string | null;
  exports_dir: string | null;
  probes_dir: string | null;
  thumbs_dir: string | null;
  trim_pre_buffer_seconds: number;
  trim_post_buffer_seconds: number;
  /** #215 -- project-level automation override. Each field is optional;
   *  a ``null`` (or missing) value means the project inherits from the
   *  global default. Resolved view available via ``getAutomation()``. */
  automation: AutomationOverride;
  /** #218 phase 4 -- per-project list of stage numbers whose audit-
   *  pending nudge the user has explicitly dismissed. */
  nudges_dismissed_stages: number[];
}

/** GET /api/scoreboard/source response. ``mode === "local"`` means the
 *  offline JSON path will serve every scoreboard request -- no network is
 *  used. ``http_token_set`` reflects whether ``SPLITSMITH_SSI_TOKEN`` is
 *  set on the server process; the SPA reads it to render a setup hint
 *  before the user runs into a 401. */
export interface ScoreboardSource {
  mode: "local" | "online";
  local_match_json_path: string | null;
  http_token_set: boolean;
}

/** One row in the ``GET /api/scoreboard/search`` array (mirrors
 *  ``splitsmith.ui.scoreboard.models.MatchRef``). */
export interface ScoreboardMatchRef {
  id: number;
  content_type: number;
  name: string;
  venue: string | null;
  date: string;
  ends: string | null;
  status: string;
  region: string;
  discipline: string;
  level: string;
  registration_status: string;
  scoring_completed: number;
}

export interface ScoreboardAuthDetail {
  code: "scoreboard_auth";
  message: string;
  env_var: string;
  docs_url: string;
}

export interface ScoreboardRateLimitDetail {
  code: "scoreboard_rate_limited";
  message: string;
  retry_after: number | null;
}

export interface ScoreboardOfflineDetail {
  code: "scoreboard_offline";
  message: string;
}

export interface StageTimesBlockedOnUpstreamDetail {
  code: "stage_times_blocked_on_upstream";
  message: string;
  upstream_issue: string;
  upstream_url: string;
}

export interface StageTimesOfflinePureMatchDataDetail {
  code: "stage_times_offline_pure_matchdata";
  message: string;
}

export interface CompetitorNotInMatchDetail {
  code: "competitor_not_in_match";
  message: string;
}

export type ScoreboardErrorDetail =
  | ScoreboardAuthDetail
  | ScoreboardRateLimitDetail
  | ScoreboardOfflineDetail
  | StageTimesBlockedOnUpstreamDetail
  | StageTimesOfflinePureMatchDataDetail
  | CompetitorNotInMatchDetail;

/** One row in the ``GET /api/scoreboard/shooter/search`` array. */
export interface ScoreboardShooterRef {
  shooterId: number;
  name: string;
  club: string | null;
  division: string | null;
  lastSeen: string;
}

/** One competitor inside ``GET /api/scoreboard/match-data``. The SPA reads
 *  this to map a picked ``shooterId`` to the per-match ``competitor_id``
 *  before posting to ``/select-shooter``. */
export interface ScoreboardMatchCompetitor {
  id: number;
  shooterId: number;
  name: string;
  competitor_number: number | null;
  club: string | null;
  division: string | null;
}

export interface ScoreboardMatchData {
  name: string;
  date: string | null;
  level: string | null;
  competitors: ScoreboardMatchCompetitor[];
  scoring_completed: number;
  match_status: string;
}

/** Pull a typed scoreboard error out of an ApiError, or null if the body
 *  doesn't match. The Ingest screen renders different banner copy for each
 *  ``code`` per #50's error UX requirements. */
export function asScoreboardError(err: unknown): ScoreboardErrorDetail | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.body;
  if (!body || typeof body !== "object") return null;
  const code = (body as { code?: unknown }).code;
  if (
    code === "scoreboard_auth" ||
    code === "scoreboard_rate_limited" ||
    code === "scoreboard_offline" ||
    code === "stage_times_blocked_on_upstream" ||
    code === "stage_times_offline_pure_matchdata" ||
    code === "competitor_not_in_match"
  ) {
    return body as ScoreboardErrorDetail;
  }
  return null;
}

export interface PlaceholderStagesRequest {
  stage_count: number;
  match_name?: string | null;
  match_date?: string | null;
}

export interface ProjectSettingsPatch {
  raw_dir?: string | null;
  audio_dir?: string | null;
  trimmed_dir?: string | null;
  exports_dir?: string | null;
  probes_dir?: string | null;
  thumbs_dir?: string | null;
  trim_pre_buffer_seconds?: number | null;
  trim_post_buffer_seconds?: number | null;
  /** #215 -- project-level automation override. Replaces the whole
   *  override object; pass field values (or null to clear an
   *  override back to the global default). */
  automation?: AutomationOverride | null;
  confirm?: boolean;
}

/** Mirror of ``splitsmith.automation.AutomationOverride`` (#215). */
export interface AutomationOverride {
  shot_detect_on_beep_verified?: boolean | null;
  /** HITL gate threshold (#219). Auto-detected beeps with confidence
   *  at or above this value pre-flip ``beep_reviewed`` so the
   *  shot-detect chain fires; below it the beep lands in the HITL
   *  queue and the user (or an agent) has to pick. Range [0, 1]. */
  beep_low_confidence_threshold?: number | null;
}

/** Mirror of the resolved-settings shape returned by ``GET /api/automation``
 *  (#216). The SPA renders ``<SettingProvenance>`` next to each toggle by
 *  reading the matching ``provenance`` entry. */
export interface ResolvedAutomationResponse {
  settings: {
    shot_detect_on_beep_verified: boolean;
    beep_low_confidence_threshold: number;
  };
  provenance: Record<string, AutomationFieldProvenance>;
}

export type AutomationProvenanceSource =
  | "cli"
  | "project"
  | "global"
  | "default";

/** Per-field source + values. ``cli_value`` / ``project_value`` /
 *  ``global_value`` are bool-or-number because the automation block now
 *  mixes toggles and thresholds. The provenance widget renders via
 *  JSON.stringify so the union is invisible at the call site. */
export interface AutomationFieldProvenance {
  source: AutomationProvenanceSource;
  cli_value: boolean | number | null;
  project_value: boolean | number | null;
  global_value: boolean | number;
}

export type HitlItemKind = "beep_low_confidence" | "beep_missing";

/** One row in the project's HITL queue (#219). Items are ordered by
 *  stage_number ascending. ``confidence`` is null for ``beep_missing``
 *  entries (no candidate was produced) and populated for
 *  ``beep_low_confidence`` entries (the threshold value the auto-trust
 *  gate measured against is in ``HitlQueueResponse.threshold``). */
export interface HitlQueueItem {
  kind: HitlItemKind;
  stage_number: number;
  video_id: string;
  confidence: number | null;
  suggested_action: string;
}

export interface HitlQueueResponse {
  items: HitlQueueItem[];
  threshold: number;
}

export interface NonEmptyOldDir {
  field: "raw_dir" | "audio_dir" | "trimmed_dir" | "exports_dir" | "probes_dir" | "thumbs_dir";
  path: string;
  file_count: number;
}

export interface NonEmptyOldDirsDetail {
  code: "non_empty_old_dirs";
  message: string;
  dirs: NonEmptyOldDir[];
}

export interface ScanResponse {
  registered: string[];
  auto_assigned: Record<string, string>;
  skipped: string[];
}

/** Filesystem state of a registered ``raw/<name>`` symlink. ``ok`` =
 *  resolves to a present file; ``broken`` = symlink with missing
 *  target; ``missing_link`` = the symlink itself is gone;
 *  ``not_a_symlink`` = a regular file (link_mode="copy") that the
 *  relink flow can't help with. */
export type LinkStatus = "ok" | "broken" | "missing_link" | "not_a_symlink";

export interface LinkStatusEntry {
  video_id: string;
  name: string;
  link_path: string;
  current_target: string | null;
  status: LinkStatus;
}

export interface LinkStatusResponse {
  entries: LinkStatusEntry[];
}

export interface RelinkEntry {
  video_id: string;
  name: string;
  link_path: string;
  current_target: string | null;
  current_status: LinkStatus;
  candidates: string[];
  chosen_path: string | null;
  ambiguous: boolean;
  found: boolean;
}

export interface RelinkScanResponse {
  search_root: string;
  entries: RelinkEntry[];
}

export interface RelinkAppliedEntry {
  video_id: string;
  name: string;
  link_path: string;
  previous_target: string | null;
  new_target: string;
}

export interface RelinkApplyResponse {
  applied: RelinkAppliedEntry[];
}

export type FsEntryKind = "dir" | "video" | "file";

export interface FsEntry {
  name: string;
  kind: FsEntryKind;
  video_count: number | null;
  size_bytes: number | null;
  mtime: number | null;
  duration: number | null;
  thumbnail_url: string | null;
}

export interface FsProbeResponse {
  duration: number | null;
  thumbnail_url: string | null;
  /** Pixel width / height / codec name from ffprobe (cached). Null when the
   *  on-disk probe cache is empty or the source could not be probed -- the
   *  unassigned-tray UI shows "—" rather than guessing. */
  width: number | null;
  height: number | null;
  codec: string | null;
  /** ``stat().st_size`` of the source file, in bytes. Null when the file
   *  could not be stat'd (broken symlink etc.). */
  size_bytes: number | null;
}

/** ``video_match.classify_video_against_stages`` output. ``contested`` ==
 *  candidate for >= 2 stages' windows; ``orphan`` == in nobody's window;
 *  ``no_timestamp`` == registration didn't capture a timestamp (legacy or
 *  source offline). */
export type VideoClassification =
  | "in_window"
  | "contested"
  | "orphan"
  | "no_timestamp";

export interface StageMatchWindow {
  stage_number: number;
  scorecard_updated_at: string | null;
  tolerance_minutes: number;
  /** Window lower bound (UTC ISO-8601). Null for placeholder stages. */
  lower: string | null;
  /** Window upper bound (= scorecard_updated_at; the heuristic's window is
   *  asymmetric because the scorecard is typed *after* the run). */
  upper: string | null;
}

export interface VideoMatchAnalysisEntry {
  path: string;
  timestamp: string | null;
  classification: VideoClassification;
  stage_numbers: number[];
}

/** Result of GET /api/project/match-analysis. The SPA's match-window
 *  timeline reads tolerance + windows + classifications from here so the
 *  heuristic stays the single source of truth. */
export interface MatchAnalysis {
  tolerance_minutes: number;
  stages: StageMatchWindow[];
  videos: VideoMatchAnalysisEntry[];
}

/** Per-secondary cam status surfaced on the Analysis & Export overview
 *  (issue #54). Mirrors ``splitsmith.ui.project.SecondaryExportStatus``;
 *  the Export page renders one checkbox row per entry so the user can
 *  include / exclude individual cams from the next Generate. */
export interface SecondaryExportStatus {
  video_id: string;
  path: string;
  /** Display label (defaults to the basename of ``path``). */
  label: string;
  has_beep: boolean;
  beep_reviewed: boolean;
  source_reachable: boolean;
  /** Path to the per-cam lossless trim under ``exports/`` -- only set
   *  when the file is on disk. ``null`` before the user runs Generate
   *  (or when the cam was excluded last time). */
  trim_path: string | null;
  trim_present: boolean;
}

/** Per-stage status row for the Analysis & Export overview (#17).
 *  Mirrors splitsmith.ui.project.StageExportStatus. The SPA renders one
 *  card per row; ``ready_to_export`` decides whether Generate is enabled.
 *  ``source_reachable`` flags the dangling-symlink case (USB unplugged) so
 *  the row can warn before the user clicks Generate. */
export interface StageExportStatus {
  stage_number: number;
  stage_name: string;
  skipped: boolean;
  has_primary: boolean;
  primary_processed: { beep: boolean; shot_detect: boolean; trim: boolean };
  audit_shot_count: number;
  /** Size of the detector's candidate pool. NOT "pending" -- once shot
   *  detection has run, every candidate is kept (in shots[]) or rejected
   *  (not in shots[]). Render as "X shots audited from Y candidates". */
  total_candidate_count: number;
  audit_path: string | null;
  trimmed_video_path: string | null;
  lossless_trim_present: boolean;
  csv_path: string | null;
  fcpxml_path: string | null;
  report_path: string | null;
  /** Pre-rendered alpha overlay MOV (issue #45). ``null`` until the user
   *  opts in via the Overlay toggle on Generate. When present, the FCPXML
   *  references it as a connected clip on V2. */
  overlay_path: string | null;
  has_exports: boolean;
  last_export_at: string | null;
  ready_to_export: boolean;
  source_reachable: boolean | null;
  /** Multi-cam roster (issue #54). One entry per secondary on the stage,
   *  including cams without a beep / unreachable cams (the SPA renders
   *  those as disabled rows). Empty when the stage is single-cam. */
  secondaries: SecondaryExportStatus[];
}

export interface ExportOverview {
  stages: StageExportStatus[];
}

/** Encoder for the alpha overlay MOV.
 *  - ``"auto"``: ``hevc-alpha`` on macOS w/ VideoToolbox, otherwise
 *    ``prores-4444``. Default; produces the smallest file the host can
 *    write without losing alpha.
 *  - ``"hevc-alpha"``: ~10-20x smaller than ProRes 4444 for sparse-text
 *    overlays. macOS only; FCP imports natively.
 *  - ``"prores-4444"``: cross-platform / archival. Largest files. */
export type OverlayCodec = "auto" | "hevc-alpha" | "prores-4444";

export interface ExportStageRequestPayload {
  write_trim?: boolean;
  write_csv?: boolean;
  write_fcpxml?: boolean;
  write_report?: boolean;
  /** Render the per-frame PIL + ffmpeg overlay MOV (issue #45). Defaults
   *  off because it's the slowest writer; opt in per stage. */
  write_overlay?: boolean;
  /** Encoder for the overlay MOV. Defaults to ``"auto"``. */
  overlay_codec?: OverlayCodec;
  /** Cap overlay output height (aspect preserved). FCPXML emits a
   *  separate ``<format>`` so FCP scales the smaller overlay across the
   *  timeline. ``null`` / undefined matches source. */
  overlay_max_height?: number | null;
  /** Cap overlay output frame rate. Source rate is kept when below the
   *  cap. ``null`` / undefined matches source. */
  overlay_max_fps?: number | null;
  /** Allowlist of secondary ``video_id``s to include in the multi-cam
   *  FCPXML / per-cam trims (issue #54). Omit (or pass ``null``) to keep
   *  the legacy "every secondary with a beep" default; pass ``[]`` to
   *  exclude all secondaries; pass a list to ship only the named cams. */
  secondary_video_ids?: string[] | null;
}

export interface ExportStageResult {
  stage_number: number;
  trimmed_video_path: string | null;
  csv_path: string | null;
  fcpxml_path: string | null;
  report_path: string | null;
  overlay_path: string | null;
  shots_written: number;
  anomalies: string[];
}

/** Match-level stitched-FCPXML export (issue #171). The selected stages
 *  must already have a lossless trim + audit shots; the match export
 *  composes from those without re-encoding. */
export interface MatchExportRequestPayload {
  stage_numbers: number[];
  /** Seconds of footage kept before the beep in each stage. Clamped
   *  server-side to the project's pre-buffer (default 5.0). */
  head_pad_seconds?: number;
  /** Seconds of footage kept after the final shot in each stage.
   *  Clamped server-side to the project's post-buffer. */
  tail_pad_seconds?: number;
  include_secondaries?: boolean;
  include_overlay?: boolean;
  /** Forwarded to the per-stage overlay re-render. The match handler
   *  treats any non-default value as "force re-render" so a stale
   *  overlay on disk doesn't shadow the dialog's format choice. */
  overlay_codec?: OverlayCodec;
  overlay_max_height?: number | null;
  overlay_max_fps?: number | null;
  /** Defaults to the bound project's name. Slugified for the output
   *  filename: ``<slug>-match.fcpxml``. */
  project_name?: string | null;
  /** Issue #193. ``"stacked"`` keeps secondaries full-frame on V2/V3/...
   *  ``"pip-corners"`` adds an FCPXML adjust-transform so each cam lands
   *  in a rotating corner (TR -> TL -> BR -> BL) at 25% scale. */
  pip_layout?: "stacked" | "pip-corners";
  /** Issue #197 / #174. ``"fcpxml"`` writes Final Cut Pro 1.10
   *  (default). ``"fcp7xml"`` writes a Final Cut Pro 7-style ``.xml``
   *  importable into Premiere Pro and DaVinci Resolve. ``"mp4"`` bakes
   *  the stitched composition into a single ffmpeg-encoded MP4
   *  (overlays / PiP burned in, no NLE needed). */
  output_format?: "fcpxml" | "fcp7xml" | "mp4";
  /** Issue #195. Uniform transition between every consecutive stage
   *  pair. ``"none"`` keeps today's hard cuts. Only FCPXML supports
   *  transitions today; selecting one with FCP7 XML or MP4 surfaces
   *  an anomaly note and falls back to hard cuts. */
  transition_kind?: "none" | "zoom" | "static";
  /** Total transition length in seconds; ignored when
   *  ``transition_kind`` is ``"none"``. Each adjacent stage's effective
   *  window must contain at least half this value of material. */
  transition_duration_seconds?: number;
  /** Issue #196. Per-stage title cards. ``"slate"`` adds a pre-stage
   *  card on the spine; ``"lower-third"`` is a connected text clip
   *  overlaid on the start of the primary. FCPXML only today. */
  title_kind?: "none" | "slate" | "lower-third";
  /** Title duration in seconds; ignored when ``title_kind`` is
   *  ``"none"``. Slates default to 1.5s; lower-thirds default to
   *  3.0s but the dialog uses one input either way. */
  title_duration_seconds?: number;
  /** Issue #173. Filesystem path to an optional intro clip placed
   *  before stage 0 on the spine. ``~`` expands server-side. Frame
   *  rate must match the timeline; missing files surface as
   *  anomalies (non-fatal). FCPXML only today. */
  intro_path?: string | null;
  /** Filesystem path to an optional outro clip placed after the
   *  last stage. Same semantics as ``intro_path``. */
  outro_path?: string | null;
  /** Issue #204. Generate a YouTube-shaped JSON sidecar alongside
   *  the export plus a per-shot ``.srt``. FCPXML route also gets
   *  chapter markers embedded so they survive an NLE round-trip
   *  into an MP4 chapter atom. */
  youtube_sidecar?: boolean;
  /** Issue #204 layer 2. Encode the MP4 with YouTube's recommended
   *  H.264 profile / GOP / colour / audio params. Only meaningful
   *  when ``output_format == "mp4"``. */
  youtube_preset?: boolean;
}

/** Body of a single export template (issue #198). Mirrors the dialog's
 *  controls; missing fields leave the dialog default unchanged when
 *  the template is applied. */
export interface MatchExportTemplate {
  schema_version: number;
  name?: string;
  description?: string;
  head_pad_seconds?: number;
  tail_pad_seconds?: number;
  include_secondaries?: boolean;
  include_overlay?: boolean;
  pip_layout?: "stacked" | "pip-corners";
  output_format?: "fcpxml" | "fcp7xml" | "mp4";
  transition_kind?: "none" | "zoom" | "static";
  transition_duration_seconds?: number;
  title_kind?: "none" | "slate" | "lower-third";
  title_duration_seconds?: number;
  intro_path?: string;
  outro_path?: string;
}

export interface MatchExportTemplateEntry {
  id: string;
  source: "builtin" | "user";
  template: MatchExportTemplate;
}

export interface MatchExportResult {
  fcpxml_path: string;
  stage_count: number;
  duration_seconds: number;
  /** Soft-failure messages (missing cam trim, ffprobe drop on a cam,
   *  missing overlay). The export still wrote the FCPXML; these are
   *  surfaced so the SPA can show "exported with warnings". */
  anomalies: string[];
}

export interface RemovalPlan {
  video_path: string;
  raw_link_path: string;
  audio_cache_path: string | null;
  trimmed_cache_path: string | null;
  audit_path: string | null;
  was_primary: boolean;
  stage_number: number | null;
  audit_reset: boolean;
}

export interface RemoveVideoResponse {
  project: MatchProject;
  plan: RemovalPlan;
}

export type CleanupCategory =
  | "caches"
  | "exports-light"
  | "exports-overlays"
  | "exports-trims"
  | "audit-trims"
  | "audio"
  | "audit-data";

export interface CleanupItem {
  path: string;
  size_bytes: number;
  category: CleanupCategory;
}

export interface CleanupTotals {
  file_count: number;
  bytes: number;
}

export interface CleanupPlan {
  items: CleanupItem[];
  totals_by_category: Partial<Record<CleanupCategory, CleanupTotals>>;
  total_bytes: number;
  total_file_count: number;
}

export interface CleanupResult {
  deleted: string[];
  failed: [string, string][];
  bytes_freed: number;
}

export interface CleanupApplyResponse {
  plan: CleanupPlan;
  result: CleanupResult;
}

/** One sidebar bookmark in the FolderPicker. ``kind`` lets the SPA
 *  group entries (Recent / Home / Removable & network) and pick the
 *  right icon. Mirrors ``splitsmith.ui.server.SuggestedStart``. */
export interface SuggestedStart {
  path: string;
  label: string;
  kind: "recent" | "home" | "removable" | "network";
}

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  suggested_starts: SuggestedStart[];
}

export interface PeaksResult {
  duration: number;
  sample_rate: number;
  bins: number;
  peaks: number[];
  /** Where the beep falls in the served clip's local timeline (seconds).
   *  Null when no beep is detected for the primary yet. */
  beep_time: number | null;
  /** True when the audio came from the short-GOP trimmed MP4; false when
   *  the audit screen is operating on the full source for lack of a trim. */
  trimmed: boolean;
}

/**
 * Audit JSON shape (issue #15). Mirrors the on-disk file at
 * `<project>/audit/stage<N>.json`. Same schema the existing audit-prep /
 * audit-apply CLI flow uses; the audit screen v2 reads and writes this
 * format so external tooling stays compatible.
 */
export interface AuditCandidate {
  candidate_number: number;
  time: number;
  ms_after_beep: number;
  peak_amplitude?: number | null;
  confidence?: number | null;
}

export interface AuditShot {
  shot_number: number;
  candidate_number: number | null;
  time: number;
  ms_after_beep: number;
  source?: "detected" | "manual";
  /** Free-text user note ("draw", "reload", "transition", ...). Persisted
   *  in the per-stage audit JSON and rendered in the splits CSV. Optional
   *  for backwards compatibility with audit JSONs written before #17. */
  notes?: string;
}

export interface AuditEvent {
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
}

/** Coaching annotation set on a single shot (issue #159).
 *
 * - ``interval_class`` and ``interval_class_source`` are set together or
 *   both null. ``manual`` survives reclassification; ``auto`` is rewritten
 *   on every reclassify call.
 * - ``stale=true`` means the stored class disagrees with what the rule
 *   would assign now (typical after an Audit timestamp edit). The Coach
 *   page surfaces a "stale" badge with click-to-accept.
 * - ``reload_hint`` is purely UI: gap exceeds the reload-hint threshold,
 *   prompting the user to reclassify as ``reload`` if appropriate.
 */
export type CoachIntervalClass =
  | "first_shot"
  | "split"
  | "transition"
  | "movement"
  | "reload"
  | "activation";
export type CoachIntervalClassSource = "auto" | "manual";

export interface CoachShot {
  shot_number: number;
  ms_after_beep: number;
  /** Seconds from the beep. */
  time_from_beep: number;
  /** Seconds in the source timeline -- the value the SPA seeks the
   *  primary video to when the user clicks this row. */
  time_absolute: number;
  /** Seconds since previous shot, or the draw for the first shot. */
  split: number;
  interval_class: CoachIntervalClass | null;
  interval_class_source: CoachIntervalClassSource | null;
  improvement_flag: boolean;
  coaching_note: string | null;
  stale: boolean;
  reload_hint: boolean;
}

export interface CoachVideoEntry {
  path: string;
  role: VideoRole;
  /** Where the beep falls in the clip the SPA actually receives from
   *  /api/videos/stream for this video -- the trimmed clip when one
   *  exists, the source otherwise. ``null`` for cameras without a beep
   *  yet; those are unsyncable and the SPA leaves them disabled. */
  beep_in_clip: number | null;
}

export interface CoachStageResponse {
  stage_number: number;
  stage_name: string;
  /** Where the beep falls in the served primary clip; same coordinate
   *  system as ``shots[i].time_absolute``. */
  beep_time: number;
  videos: CoachVideoEntry[];
  shots: CoachShot[];
}

/** One bin of a Coach histogram (#163). ``lo`` inclusive, ``hi`` exclusive. */
export interface CoachHistogramBucket {
  lo: number;
  hi: number;
  count: number;
}

/** Distribution of gap-times for one ``interval_class`` across one stage
 *  or one match. ``buckets`` only contains non-empty bins to keep the
 *  payload small; ``count``/``mean_s``/etc. are computed over the full
 *  set of values. */
export interface CoachIntervalDistribution {
  interval_class: CoachIntervalClass;
  bucket_size_s: number;
  buckets: CoachHistogramBucket[];
  count: number;
  mean_s: number | null;
  median_s: number | null;
  p90_s: number | null;
}

export interface CoachTopShotEntry {
  stage_number: number;
  stage_name: string;
  shot_number: number;
  interval_class: CoachIntervalClass;
  gap_s: number;
  coaching_note: string | null;
  improvement_flag: boolean;
}

export interface CoachFlaggedShotEntry {
  stage_number: number;
  stage_name: string;
  shot_number: number;
  interval_class: CoachIntervalClass | null;
  gap_s: number | null;
  coaching_note: string | null;
}

export interface CoachStageDistributions {
  stage_number: number;
  stage_name: string;
  distributions: CoachIntervalDistribution[];
  first_shot_s: number | null;
}

export interface CoachMatchDistributions {
  distributions: CoachIntervalDistribution[];
  first_shot_seconds: number[];
  top_splits: CoachTopShotEntry[];
  top_transitions: CoachTopShotEntry[];
  flagged_shots: CoachFlaggedShotEntry[];
  stage_count: number;
  shot_count: number;
}

export interface CoachShotPatch {
  interval_class?: CoachIntervalClass | null;
  interval_class_source?: CoachIntervalClassSource | null;
  clear_class?: boolean;
  improvement_flag?: boolean | null;
  coaching_note?: string | null;
  clear_note?: boolean;
}

export interface StageAudit {
  stage_number: number;
  stage_name: string;
  beep_time?: number;
  tolerance_ms?: number;
  stage_time_seconds?: number;
  fixture_window_in_source?: [number, number];
  shots: AuditShot[];
  _candidates_pending_audit?: { candidates: AuditCandidate[] };
  audit_events?: AuditEvent[];
  source?: string;
}

export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

/** Mirror of splitsmith.ui.jobs.Job. Long-running endpoints (detect-beep,
 *  trim, future shot-detect/export) submit a job and return a snapshot;
 *  the SPA polls /api/me/jobs/{id} until status leaves "pending" / "running". */
export interface Job {
  id: string;
  kind: string;
  stage_number: number | null;
  /** Targets a specific StageVideo when the operation is per-camera
   *  (multi-cam beep / trim). Null for stage-level jobs (shot_detect,
   *  export). The SPA disambiguates concurrent per-camera jobs in
   *  jobs rail by this id. */
  video_id: string | null;
  status: JobStatus;
  progress: number | null;
  message: string | null;
  error: string | null;
  /** True after the SPA POSTed /api/me/jobs/{id}/cancel for this job. The flag
   *  stays True on the terminal snapshot so the row can be labelled
   *  "Cancelled by user" instead of "Aborted". */
  cancel_requested: boolean;
  /** True once the user has dismissed this failure via
   *  /api/me/jobs/{id}/acknowledge (issue #73). Meaningful only on FAILED
   *  jobs; the jobs rail badge counts failures with acknowledged=false
   *  and the registry rolls acknowledged failures off faster than
   *  unacknowledged ones. */
  acknowledged: boolean;
  /** Optional structured result payload set by the worker. Present on
   *  successful jobs whose output is meaningful to the SPA -- e.g.
   *  match-export emits ``{ fcpxml_path, stage_count, duration_seconds,
   *  anomalies }``. The schema is per-kind: branch on ``Job.kind`` to
   *  interpret. ``null`` for jobs that signal success only by writing
   *  files. */
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** Structured error payload emitted by source-bound endpoints (detect-beep,
 *  trim, beep-preview, video stream, export) when the primary's source file
 *  is not reachable on disk -- typically because external storage is
 *  unplugged. Status code is 424 (Failed Dependency); ``code`` is the
 *  discriminator the SPA matches on. */
export interface SourceUnreachableDetail {
  code: "source_unreachable";
  stage_number: number | null;
  path: string;
  message: string;
}

/** Pull a SourceUnreachableDetail out of an ApiError if the body matches.
 *  Returns null otherwise so callers can fall through to generic display. */
export function asSourceUnreachable(err: unknown): SourceUnreachableDetail | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status !== 424) return null;
  const body = err.body;
  if (!body || typeof body !== "object") return null;
  const code = (body as { code?: unknown }).code;
  if (code !== "source_unreachable") return null;
  return body as SourceUnreachableDetail;
}

/** True when ``err`` is the structured 409 the server raises whenever a
 *  project-bound endpoint is hit while the server is unbound (picker
 *  mode). The SPA listens for this to redirect to /pick.
 */
export function isNoProjectError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status !== 409) return false;
  const body = err.body;
  if (!body || typeof body !== "object") return false;
  return (body as { code?: unknown }).code === "no_project";
}

/** Server health snapshot. ``bound === false`` means no project is open --
 *  the SPA renders the picker until the user selects one. */
export interface ServerHealth {
  status: string;
  /** Engine version (issue #131). */
  version?: string;
  bound: boolean;
  project_name: string | null;
  project_root: string | null;
  /** Stable match identifier (#353 Phase 3). Populated for ``kind="match"``;
   *  ``null`` for legacy single-shooter projects and the unbound state.
   *  SPA URLs are migrating to a ``/match/:matchId/`` prefix; this field
   *  is the source of truth for what to write into the URL. */
  match_id?: string | null;
  /** Discriminates the on-disk layout. ``"match"`` for the Match -> Shooter
   *  folder layout; ``"legacy"`` for single-shooter projects that predate
   *  the split; ``null`` when the server is unbound. */
  kind: "match" | "legacy" | null;
  /** Slug that the SPA can plug into ``/audit/<slug>/...`` URLs from any
   *  slugless surface (Home, sidebar Audit/Coach/Export rows) so the
   *  user lands somewhere sensible without picking a shooter first. For
   *  a match this is the alphabetically-first registered shooter; for a
   *  legacy project it's the deterministic legacy slug. Null when the
   *  match is empty or the server is unbound. */
  default_shooter_slug: string | null;
  schema_version: number | null;
}

/** The authenticated account behind a request. ``GET /api/me`` returns
 *  this (200) or 401 when unauthenticated. In local mode it is always the
 *  loopback sentinel (``id === "local"``); in hosted mode it is the
 *  database user resolved from the session cookie. */
export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
}

/** Saved SSI Scoreboard identity for the operator. Returned by
 *  ``GET /api/me/scoreboard-identity``; null when nothing is pinned. */
export interface ScoreboardIdentity {
  shooter_id: number;
  display_name: string | null;
  division: string | null;
  club: string | null;
  base_url: string | null;
}

/** One entry from ``GET /api/me/recent-projects``. ``last_opened_at``
 *  is an ISO-8601 UTC timestamp the picker uses to sort. ``path`` is
 *  resolved server-side; we don't normalise it client-side. ``kind``
 *  was added in #320 -- ``null`` covers entries written before the
 *  field existed (the picker resolves them on open). */
export interface RecentProject {
  path: string;
  name: string;
  last_opened_at: string;
  kind?: "match" | "legacy" | null;
}

/** What a project delete actually removed (mirrors the server's
 *  DeletionSummary). Returned even on partial failure, with ``errors``
 *  populated, so the UI can report "removed X, N errors". */
export interface DeletionSummary {
  match_id: string | null;
  recent_project_removed: boolean;
  match_row_removed: boolean;
  state_docs_removed: number;
  storage_objects_deleted: number;
  raw_uploads_deleted: string[];
  raw_uploads_skipped_shared: string[];
  jobs_cancelled: number;
  local_dir_removed: boolean;
  errors: string[];
}

export interface DeleteProjectResponse {
  summary: DeletionSummary;
  projects: RecentProject[];
}

export interface DeleteProjectOptions {
  /** Desktop only: also delete the project folder on disk. */
  deleteLocalFiles?: boolean;
  /** Hosted only: also delete raw uploads that fed only this match. */
  deleteRawUploads?: boolean;
}

/** Detailed RecentProject with on-disk metadata (`?detail=true`, #322). */
export interface RecentProjectDetail {
  path: string;
  name: string;
  last_opened_at: string;
  kind: "match" | "legacy" | "missing" | "unknown";
  shooter_count: number;
  stage_count: number;
  stages_audited: number;
  /** Total raw videos attached across all shooters. Drives the
   *  "awaiting footage" pill + footage-aware menu treatment (#425). */
  video_count: number;
  match_date: string | null;
  club: string | null;
  last_modified_at: string | null;
  status:
    | "awaiting_footage"
    | "in_progress"
    | "exported"
    | "archived"
    | "unknown";
  manual: boolean;
  shooter_names: string[];
}

export interface CreateMatchStageDraft {
  stage_number: number;
  stage_name: string;
  expected_rounds?: number | null;
  target_type?: string | null;
}

export interface CreateMatchManualBody {
  name: string;
  /** Optional in hosted mode -- server synthesizes
   *  ``users/<user_id>/projects/<slug>/`` under ``$SPLITSMITH_PROJECTS_DIR``
   *  when omitted/null. Required in local mode. */
  project_folder?: string | null;
  match_date?: string | null;
  club?: string | null;
  match_type?: string | null;
  default_division?: string | null;
  stages: CreateMatchStageDraft[];
  primary_shooter: { name: string; division?: string | null };
}

/** One competitor pulled from a scoreboard match's roster, ready to be
 *  materialised as a shooter when the match is created. No "primary" /
 *  "me" flag -- the operator running the app may be coaching and not in
 *  the roster at all (issue #350). Mirrors :class:`CreateMatchCompetitorPick`
 *  in the backend. */
export interface CreateMatchCompetitorPick {
  name: string;
  division?: string | null;
  selected_shooter_id?: number | null;
  selected_competitor_id: number;
}

export interface CreateMatchScoreboardBody {
  /** Optional in hosted mode (see :type:`CreateMatchManualBody`). */
  project_folder?: string | null;
  name: string;
  match_id: number;
  content_type: number;
  competitors: CreateMatchCompetitorPick[];
}

/** One camera inside a shooter (#324). */
export interface ShooterCameraInfo {
  group_key: string;
  make: string | null;
  model: string | null;
  mount: string | null;
  role: "primary" | "secondary";
  video_count: number;
  stage_numbers: number[];
}

/** One stage's lifecycle status for a single shooter, mirrored from the
 *  backend ``StageStatusEntry``. The match Overview pivots these across
 *  shooters into an aggregate per-stage grid. */
export interface StageStatusEntry {
  stage_number: number;
  status: StageStatus;
}

/** One shooter row in the /shooters page (#324). */
export interface ShooterListEntry {
  slug: string;
  name: string;
  selected_shooter_id: number | null;
  selected_competitor_id: number | null;
  stages_audited: number;
  stages_total: number;
  video_count: number;
  cameras: ShooterCameraInfo[];
  /** Stages where the audit-mode trim cache is missing and rebuildable
   *  (primary + beep + stage_time + reachable source). Drives the
   *  "Rebuild trim caches (N)" CTA on /shooters (#351). */
  stages_missing_trim: number;
  /** Per-stage status for this shooter (one entry per stage in the
   *  shooter's own project). Drives the aggregate Overview grid. */
  stage_statuses: StageStatusEntry[];
}

/** Response payload for POST /api/match/shooters/{slug}/build-trim-caches (#351). */
export interface BuildTrimCachesResult {
  shooter_slug: string;
  shooter_name: string | null;
  jobs_submitted: Job[];
  skipped: { stage: number; reason: string }[];
}

export interface ShooterListResponse {
  match_root: string;
  match_name: string;
  shooters: ShooterListEntry[];
}

/** One uploaded raw video in the operator's hosted-mode object
 *  storage, returned by ``GET /api/me/raw/list``. Mirrors the response
 *  shape of ``POST /api/me/raw/upload`` so the SPA can hand-off
 *  freshly-uploaded entries into the list view without a round-trip. */
export interface RawUploadEntry {
  filename: string;
  path: string;
  size: number;
  last_modified: string | null;
  etag: string | null;
}

/** One ``raw_videos[]`` manifest entry on ``match.json`` (doc 05).
 *  Returned by ``POST /api/shooters/{slug}/raw-videos/attach`` after
 *  the server merges into any existing entry with the same
 *  ``storage_path``. The SPA reads this back to confirm the canonical
 *  ``covers_stages`` post-merge (which may differ from what the
 *  caller posted when an earlier attach already declared coverage). */
export interface RawVideoManifestEntry {
  original_filename: string;
  size_bytes: number;
  sha256: string | null;
  uploaded_at: string;
  storage_path: string;
  covers_stages: number[];
}

/** Response from POST /api/shooters/{slug}/videos/suggest-coverage.
 *  ``covers_stages`` is ordered by scorecard time; ``span`` is the
 *  resolved wall-clock span in ISO-8601 or null when no span was
 *  resolvable. */
export interface CoverageSuggestion {
  covers_stages: number[];
  span: { start: string; end: string } | null;
}

/** Per-stage-video entry in a take-level overview. ``status`` is
 *  ``"found"`` once beep_time is set, ``"none"`` after auto-detect
 *  failed, and ``"pending"`` before the first detection pass. */
export interface TakeOverviewStage {
  stage_number: number;
  stage_name: string;
  video_id: string;
  role: string;
  beep_time: number | null;
  beep_confidence: number | null;
  beep_reviewed: boolean;
  beep_window: [number, number] | null;
  beep_window_source: string | null;
  status: "found" | "none" | "pending";
}

/** Response from GET /api/shooters/{slug}/raw-videos/overview. */
export interface TakeOverview {
  raw_video: RawVideoManifestEntry;
  duration_seconds: number | null;
  stages: TakeOverviewStage[];
  conflicts: number[];
}

/** One shot point on a shooter's timeline in /compare (#328). */
export interface CompareShotPoint {
  shot_number: number;
  time_after_beep: number;
  source: "detected" | "manual";
}

/** Per-shooter data for a stage in /compare (#328). */
export interface CompareShooterRecord {
  slug: string;
  name: string;
  video_path: string | null;
  beep_offset_in_clip: number | null;
  duration_seconds: number | null;
  stage_time_seconds: number | null;
  shots: CompareShotPoint[];
}

export interface CompareStageResponse {
  stage_number: number;
  stage_name: string;
  shooters: CompareShooterRecord[];
}

/** One pending beep review item in the cross-shooter queue (#326). */
export interface BeepQueueAltCandidate {
  time: number;
  confidence: number | null;
}

export interface BeepQueueItem {
  slug: string;
  shooter_name: string;
  stage_number: number;
  stage_name: string;
  video_id: string;
  video_path: string;
  beep_time: number | null;
  beep_confidence: number | null;
  beep_reviewed: boolean;
  status: "missing" | "low_confidence" | "unreviewed" | "confirmed";
  alt_candidates: BeepQueueAltCandidate[];
}

export interface BeepQueueStageGroup {
  stage_number: number;
  stage_name: string;
  items: BeepQueueItem[];
  total_primaries: number;
  confirmed: number;
}

export interface BeepQueueResponse {
  total_items: number;
  pending_count: number;
  confirmed_count: number;
  stages: BeepQueueStageGroup[];
}

/** Merge wizard types (#332). plan_merge dry-runs the merge; the SPA
 *  shows the user the per-shooter slug assignments + reconciled stages +
 *  detected conflicts before they commit. execute_merge actually writes
 *  the new match folder and binds the first shooter as active. */
export interface MergePlanStage {
  stage_number: number;
  stage_name: string;
  expected_rounds: number | null;
  placeholder: boolean;
}
export interface MergePlanShooterMove {
  source_root: string;
  slug: string;
  destination_root: string;
  competitor_name: string;
}
export interface MergePlanResponse {
  output_root: string;
  name: string;
  scoreboard_match_id: string | null;
  scoreboard_content_type: number | null;
  match_date: string | null;
  stages: MergePlanStage[];
  shooter_moves: MergePlanShooterMove[];
}

/** Developer-mode model chip + workflow-stepper counts. Built once by the
 *  /api/dev/model endpoint from the shipped ensemble artifacts plus the
 *  review-queue tally on disk. */
export interface DeveloperModelInfo {
  active_version: string;
  recall: number;
  precision: number | null;
  f1: number | null;
  fixture_count: number;
  built_at: string | null;
  step_counts: {
    corpus: number;
    review: number;
    validate_runs: number;
    retrain: number;
  };
}

export interface DevReviewQueueItem {
  slug: string;
  audit_path: string;
  source: "match" | "github" | "ad-hoc";
  source_label: string;
  status: "pending" | "flagged" | "done";
  n_shots: number;
  n_disagreements: number;
  promoted_at: string | null;
  venue: string | null;
  stage_number: number | null;
  shooter: string | null;
  age_seconds: number | null;
}

export interface DevReviewQueueResponse {
  pending: DevReviewQueueItem[];
  flagged: DevReviewQueueItem[];
  done: DevReviewQueueItem[];
}

/** One (stage_number, video_id) pair in a bulk camera-set call.
 *  Mirrors ``BulkCameraSetItem`` in server.py. */
export interface BulkCameraSetItem {
  stage_number: number;
  video_id: string;
}

/** Request body for POST /api/shooters/{slug}/stages/camera/bulk-set.
 *  ``set_mount`` / ``set_model`` flags distinguish "set to null" from
 *  "leave unchanged". Mirrors ``BulkCameraSetRequest`` in server.py. */
export interface BulkCameraSetRequest {
  items: BulkCameraSetItem[];
  set_mount?: boolean;
  mount?: CameraMount | null;
  set_model?: boolean;
  make?: string | null;
  model?: string | null;
}

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public body: unknown = null,
  ) {
    super(`${status}: ${detail}`);
  }
}

/** True when ``err`` is a 401 from the API -- i.e. the caller is not
 *  authenticated. The auth context uses this to distinguish "logged out"
 *  (expected, render the login surface) from a real server error. */
export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

/** Match id parsed from the current URL.
 *
 * The SPA's match-scoped routes live under ``/match/:matchId/...`` (#353
 * Phase 3). When the operator is on one of those URLs we route API
 * traffic through ``/api/matches/{matchId}/...`` so the request lands
 * on the right match without depending on the server's bound singleton.
 * Returns ``null`` for non-match URLs (picker, dev mode, root).
 *
 * Exported for the rare test / debug case; production code consults it
 * via :func:`request` automatically. */
export function currentMatchIdFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/match\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Prefixes that ride on the per-request match scope. Other ``/api/`` paths
 *  (``/api/health``, ``/api/me/*``, ``/api/server/*``, ``/api/lab/*``,
 *  ``/api/files/*``, ``/api/fs/*``, ``/api/dev/*``, etc.) stay on their
 *  legacy bare paths because they are operator-global or unbound. */
const MATCH_SCOPED_PREFIXES = ["/api/shooters/", "/api/match/"];

function scopeRequestPath(path: string): string {
  const matchId = currentMatchIdFromLocation();
  if (!matchId) return path;
  if (!MATCH_SCOPED_PREFIXES.some((p) => path.startsWith(p))) return path;
  // Rewrite ``/api/shooters/foo`` -> ``/api/matches/{id}/shooters/foo``
  // (and same for ``/api/match/...``). Preserves the query string, since
  // we splice into the path segment only.
  return `/api/matches/${encodeURIComponent(matchId)}${path.substring(4)}`;
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const { json, ...rest } = init ?? {};
  const headers: HeadersInit = {
    Accept: "application/json",
    ...(rest.headers ?? {}),
  };
  if (json !== undefined) {
    (headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const resp = await fetch(scopeRequestPath(path), {
    ...rest,
    headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    let rawDetail: unknown = null;
    try {
      const body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        rawDetail = (body as { detail: unknown }).detail;
        detail =
          typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail);
      }
    } catch {
      /* ignore */
    }
    // Server-state drift recovery: a 409 with ``code: "no_project"`` means
    // the SPA's view of the world is stale -- the server has no bound
    // project (dev-server restart, explicit unbind, etc.). Fire a custom
    // event so the match shell can force a health refresh and redirect
    // to /pick. Without this, the page just shows broken-everything with
    // no explanation and the jobs rail quietly disappears. ``not_a_match``
    // doesn't trigger the redirect; that's a per-route concern (the
    // bound project is fine, just legacy).
    if (
      resp.status === 409 &&
      rawDetail &&
      typeof rawDetail === "object" &&
      (rawDetail as { code?: unknown }).code === "no_project" &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent("splitsmith:no-project"));
    }
    throw new ApiError(resp.status, detail, rawDetail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** Files at or above this size use the presigned multipart path (direct
 *  browser -> R2); smaller files take the single-shot serve-proxied
 *  upload. The serve proxy 502s on Railway past a few hundred MB, so the
 *  threshold sits well below that. */
const MULTIPART_THRESHOLD_BYTES = 64 * 1024 * 1024;

interface RawUploadResult {
  path: string;
  size: number;
  sha256: string | null;
  filename: string;
}

/** PUT one part's bytes straight to R2 via its presigned URL. Resolves
 *  with the part's ETag (which the complete call needs). The ETag header
 *  is only readable when the R2 bucket CORS exposes it
 *  (Access-Control-Expose-Headers: ETag). */
function putUploadPart(
  url: string,
  blob: Blob,
  opts: { onProgress?: (bytesSent: number) => void; signal?: AbortSignal },
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(
            new ApiError(
              0,
              "R2 returned no ETag for the part -- check the bucket CORS exposes the ETag header",
              null,
            ),
          );
          return;
        }
        resolve(etag);
        return;
      }
      reject(new ApiError(xhr.status, `part upload failed: ${xhr.statusText}`, null));
    };
    xhr.onerror = () => reject(new ApiError(0, "network error during part upload", null));
    xhr.onabort = () => reject(new ApiError(0, "upload cancelled", null));
    if (opts.signal) {
      if (opts.signal.aborted) {
        reject(new ApiError(0, "upload cancelled", null));
        return;
      }
      opts.signal.addEventListener("abort", () => xhr.abort());
    }
    xhr.send(blob);
  });
}

/** Upload a large file by splitting it into parts and PUTting each
 *  straight to R2 via presigned URLs, so no bytes pass through the API
 *  process. ``onProgress`` reports cumulative bytes across all parts. On
 *  any failure the in-progress multipart is best-effort aborted so R2
 *  doesn't keep staged parts. */
async function uploadRawMultipart(
  file: File,
  opts: { onProgress?: (bytesSent: number, total: number) => void; signal?: AbortSignal },
): Promise<RawUploadResult> {
  const create = await request<{
    upload_id: string;
    filename: string;
    key: string;
    part_size: number;
  }>("/api/me/raw/upload/multipart/create", {
    method: "POST",
    json: { filename: file.name },
  });
  const { upload_id, filename, part_size } = create;
  const partCount = Math.max(1, Math.ceil(file.size / part_size));
  const parts: { part_number: number; etag: string }[] = [];
  let uploadedBytes = 0;

  try {
    for (let i = 0; i < partCount; i++) {
      if (opts.signal?.aborted) throw new ApiError(0, "upload cancelled", null);
      const start = i * part_size;
      const blob = file.slice(start, Math.min(start + part_size, file.size));
      const partNumber = i + 1;
      const { url } = await request<{ url: string }>(
        "/api/me/raw/upload/multipart/part-url",
        { method: "POST", json: { filename, upload_id, part_number: partNumber } },
      );
      const etag = await putUploadPart(url, blob, {
        signal: opts.signal,
        onProgress: (sent) => opts.onProgress?.(uploadedBytes + sent, file.size),
      });
      uploadedBytes += blob.size;
      opts.onProgress?.(uploadedBytes, file.size);
      parts.push({ part_number: partNumber, etag });
    }
  } catch (err) {
    try {
      await request("/api/me/raw/upload/multipart/abort", {
        method: "POST",
        json: { filename, upload_id },
      });
    } catch {
      /* best-effort: R2's lifecycle rule also sweeps abandoned multiparts */
    }
    throw err;
  }

  return request<RawUploadResult>("/api/me/raw/upload/multipart/complete", {
    method: "POST",
    json: { filename, upload_id, parts },
  });
}

export const api = {
  getProject: (slug: string) =>
    request<MatchProject>(`/api/shooters/${encodeURIComponent(slug)}/project`),

  /** Fetch the canonical match-window analysis (per-stage windows +
   *  per-video classification). Drives the ingest screen's timeline; SPA
   *  carries no policy of its own beyond rendering. */
  getMatchAnalysis: (slug: string) =>
    request<MatchAnalysis>(
      `/api/shooters/${encodeURIComponent(slug)}/project/match-analysis`,
    ),

  listFolder: (slug: string, path?: string, opts?: { probe?: boolean }) => {
    const params = new URLSearchParams();
    if (path) params.set("path", path);
    if (opts?.probe) params.set("probe", "true");
    const qs = params.toString();
    return request<FsListing>(
      `/api/shooters/${encodeURIComponent(slug)}/fs/list${qs ? `?${qs}` : ""}`,
    );
  },

  /** Directory-only listing without a bound project (#322). Used by the
   * create-match folder picker -- the create flow runs before any project
   * exists, so the shooter-scoped fs/list endpoint isn't reachable. */
  listFolderUnbound: (path?: string) => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<FsListing>(`/api/fs/list-dirs${qs}`);
  },

  probeFile: (slug: string, path: string) =>
    request<FsProbeResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/fs/probe?path=${encodeURIComponent(path)}`,
    ),

  removeVideo: (slug: string, videoPath: string, resetAudit = false) =>
    request<RemoveVideoResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/remove`,
      {
        method: "POST",
        json: { video_path: videoPath, reset_audit: resetAudit },
      },
    ),

  getCleanupPlan: (slug: string, categories: CleanupCategory[]) => {
    const qs = categories.length
      ? `?categories=${encodeURIComponent(categories.join(","))}`
      : "";
    return request<CleanupPlan>(
      `/api/shooters/${encodeURIComponent(slug)}/project/cleanup/plan${qs}`,
    );
  },

  applyCleanup: (slug: string, categories: CleanupCategory[]) =>
    request<CleanupApplyResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/project/cleanup`,
      {
        method: "POST",
        json: { categories },
      },
    ),

  importScoreboard: (slug: string, data: unknown, overwrite = false) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/import`,
      {
        method: "POST",
        json: { data, overwrite },
      },
    ),

  // SSI Scoreboard v1 wiring (#50). The UI calls these without picking the
  // backend implementation -- the server resolves Local vs Http per request
  // based on whether ``<project>/scoreboard/match.json`` exists.

  getScoreboardSource: (slug: string) =>
    request<ScoreboardSource>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/source`,
    ),

  /** Drop-and-populate: the SPA reads the file as text, parses JSON, and
   *  posts it here. Backend writes to ``<project>/scoreboard/match.json``
   *  and uses LocalJsonScoreboard so subsequent requests stay fully offline. */
  uploadScoreboard: (slug: string, data: unknown, overwrite = false) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/upload`,
      {
        method: "POST",
        json: { data, overwrite },
      },
    ),

  /** Free-text match search. In offline mode this hits the dropped match
   *  only; in online mode it goes to ``GET /api/v1/events?q=``. */
  searchScoreboardMatches: (slug: string, q: string) =>
    request<ScoreboardMatchRef[]>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/search?q=${encodeURIComponent(q)}`,
    ),

  /** Cache-first full match fetch -> populate project. */
  fetchScoreboardMatch: (
    slug: string,
    contentType: number,
    matchId: number,
    overwrite = false,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/fetch`,
      {
        method: "POST",
        json: { content_type: contentType, match_id: matchId, overwrite },
      },
    ),

  /** Resolve the current source's ``MatchData``. The SPA uses this to map
   *  a picked ``shooterId`` to a per-match ``competitor_id`` before
   *  pinning. 404 when the project has no match loaded. */
  /** Fetch full match data (incl. competitor list) without a bound
   *  shooter (#322). Used by the create-from-scoreboard flow to populate
   *  the multi-shooter picker before any project exists. */
  getScoreboardMatchDataUnbound: (contentType: number, matchId: number) =>
    request<ScoreboardMatchData>(
      `/api/scoreboard/matches/${contentType}/${matchId}`,
    ),

  getScoreboardMatchData: (slug: string) =>
    request<ScoreboardMatchData>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/match-data`,
    ),

  /** Find shooters by name. Offline mode searches this match's competitor
   *  list only; online mode hits the live shooter index. */
  searchScoreboardShooters: (slug: string, q: string) =>
    request<ScoreboardShooterRef[]>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/shooter/search?q=${encodeURIComponent(q)}`,
    ),

  /** Pin (shooter, competitor) and merge stage times. The competitor id is
   *  resolved client-side from ``getScoreboardMatchData``; the server
   *  refuses to guess. The response carries the updated project plus a
   *  ``stage_times_merged`` count for the SPA to confirm. */
  selectScoreboardShooter: (
    slug: string,
    shooterId: number,
    competitorId: number,
  ) =>
    request<MatchProject & { stage_times_merged: number }>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/select-shooter`,
      {
        method: "POST",
        json: { shooter_id: shooterId, competitor_id: competitorId },
      },
    ),

  /** Re-pull and re-merge stage times for the pinned competitor. Used for
   *  in-progress matches where new scorecards land while the user is
   *  ingesting; clears the project-local cache for every cid in the match. */
  refreshScoreboardTimes: (slug: string) =>
    request<MatchProject & { stage_times_merged: number }>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/refresh-times`,
      { method: "POST" },
    ),


  createPlaceholderStages: (slug: string, req: PlaceholderStagesRequest) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/project/placeholder-stages`,
      {
        method: "POST",
        json: req,
      },
    ),

  scanVideos: (
    slug: string,
    sourceDir: string,
    autoAssignPrimary = true,
    linkMode: "symlink" | "copy" = "symlink",
  ) =>
    request<ScanResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/scan`,
      {
        method: "POST",
        json: {
          source_dir: sourceDir,
          auto_assign_primary: autoAssignPrimary,
          link_mode: linkMode,
        },
      },
    ),

  scanFiles: (
    slug: string,
    sourcePaths: string[],
    autoAssignPrimary = true,
    linkMode: "symlink" | "copy" = "symlink",
  ) =>
    request<ScanResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/scan`,
      {
        method: "POST",
        json: {
          source_paths: sourcePaths,
          auto_assign_primary: autoAssignPrimary,
          link_mode: linkMode,
        },
      },
    ),

  /** Per-video filesystem status of the ``raw/<name>`` symlinks. The
   *  Project page polls this so it can badge broken/missing entries
   *  outside the relink dialog. */
  getLinkStatus: (slug: string) =>
    request<LinkStatusResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/link-status`,
    ),

  /** Recursive dry-run: walk ``searchRoot`` and report per-video
   *  candidates by basename. Pure -- no filesystem mutations. */
  relinkScan: (slug: string, searchRoot: string) =>
    request<RelinkScanResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/relink/scan`,
      {
        method: "POST",
        json: { search_root: searchRoot },
      },
    ),

  /** Apply a confirmed set of symlink rewrites. ``decisions`` maps
   *  ``video_id`` -> absolute target path. Any video_id not listed is
   *  left untouched. */
  relinkApply: (slug: string, decisions: Record<string, string>) =>
    request<RelinkApplyResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/relink/apply`,
      {
        method: "POST",
        json: { decisions },
      },
    ),

  updateSettings: (slug: string, patch: ProjectSettingsPatch) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/project/settings`,
      {
        method: "POST",
        json: patch,
      },
    ),

  getAutomation: (slug: string) =>
    request<ResolvedAutomationResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/automation`,
    ),

  /** Project-level work queue: beeps the auto-trust gate (#219) didn't
   *  clear -- either missing (auto-detect found nothing) or below the
   *  ``beep_low_confidence_threshold``. The HITL panel polls this on
   *  the Ingest page; the MCP wrapper exposes it as a resource so an
   *  agent can drive the picks. */
  getHitlQueue: (slug: string) =>
    request<HitlQueueResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/hitl-queue`,
    ),

  dismissNudge: (slug: string, stageNumber: number, dismissed: boolean) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/project/nudges/dismiss`,
      {
        method: "POST",
        json: { stage_number: stageNumber, dismissed },
      },
    ),

  moveAssignment: (
    slug: string,
    videoPath: string,
    toStageNumber: number | null,
    role: VideoRole = "secondary",
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/assignments/move`,
      {
        method: "POST",
        json: {
          video_path: videoPath,
          to_stage_number: toStageNumber,
          role,
        },
      },
    ),

  /** Promote ``videoPath`` to primary on ``stageNumber``. The server
   *  refuses with a 409 (``code: "audit_exists"``) when the stage has
   *  shots in its audit JSON and ``confirm`` is false; the SPA should
   *  prompt then re-call with ``confirm=true``. On confirm, the existing
   *  audit JSON is renamed to ``.bak`` and detection re-runs on the new
   *  primary's audio. */
  /** Override the heuristic camera mount on a single video. The mapping
   *  to ensemble threshold class (handheld vs headcam) happens server-
   *  side via ``camera_class_from_mount``. Pass ``null`` to clear the
   *  override and fall back to the artifact's default class on the next
   *  shot-detect run. */
  setCameraMount: (
    slug: string,
    stageNumber: number,
    videoId: string,
    mount: CameraMount | null,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/camera-mount`,
      {
        method: "PATCH",
        json: { mount },
      },
    ),

  /** Override the ffprobed camera make + model on a single video
   *  (#303-followup). Pass ``null`` for both to clear back to "Other
   *  (generic headcam)" -- the runtime then falls back to the
   *  generic-headcam amplitude floor. ``make`` and ``model`` must be
   *  supplied together or both ``null``. */
  setCameraModel: (
    slug: string,
    stageNumber: number,
    videoId: string,
    make: string | null,
    model: string | null,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/camera-model`,
      {
        method: "PATCH",
        json: { make, model },
      },
    ),

  /** List the camera models calibrated in the shipped artifact. The SPA
   *  presents these as the camera-model dropdown options on Ingest. */
  getCalibratedCameraModels: () =>
    request<{ models: CalibratedCameraModel[] }>(
      "/api/calibrated-camera-models",
    ),

  /** Apply a camera mount and/or model override to every listed
   *  (stage_number, video_id) pair in a single round-trip.
   *  ``set_mount`` / ``set_model`` flags distinguish "set to null"
   *  from "leave unchanged". Returns the updated MatchProject, matching
   *  the shape of setCameraMount / setCameraModel. */
  bulkSetCamera: (
    slug: string,
    body: BulkCameraSetRequest,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/camera/bulk-set`,
      { method: "POST", json: body },
    ),

  swapPrimary: (
    slug: string,
    videoPath: string,
    stageNumber: number,
    confirm = false,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/assignments/swap-primary`,
      {
        method: "POST",
        json: {
          video_path: videoPath,
          stage_number: stageNumber,
          confirm,
        },
      },
    ),

  /** Toggle the ``skipped`` flag on a stage. Skipped stages don't block
   *  the "next step" gate even when they have no videos / no primary. */
  setStageSkipped: (slug: string, stageNumber: number, skipped: boolean) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/skip`,
      {
        method: "POST",
        json: { skipped },
      },
    ),

  /** Submit a beep-detection job for the stage's primary. Returns a Job
   *  snapshot; poll via {@link api.pollJob} (or read /api/me/jobs/{id})
   *  until the status flips out of "pending"/"running". On success the
   *  SPA should re-fetch /api/project to pick up the new beep_time +
   *  processed.trim. Backed by the per-video pipeline; identical to
   *  ``detectBeepForVideo(stage, primary.video_id)``. */
  detectBeep: (slug: string, stageNumber: number, force = false) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/detect-beep${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),

  /** Submit a beep-detection job for a specific video on a stage.
   *  Generic over role: each camera gets its own beep_time, its own
   *  audit-mode trim, and its own dedupe slot in the registry so
   *  primary + Cam 2 + Cam 3 can run in parallel. Shot detection
   *  auto-chains for primary results only. */
  detectBeepForVideo: (
    slug: string,
    stageNumber: number,
    videoId: string,
    force = false,
  ) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/detect-beep${
        force ? "?force=true" : ""
      }`,
      { method: "POST" },
    ),

  overrideBeep: (slug: string, stageNumber: number, beepTime: number | null) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/beep`,
      {
        method: "POST",
        json: { beep_time: beepTime },
      },
    ),

  /** Manually set or clear a stage's duration. Used for projects without
   *  scoreboard data, where ``time_seconds`` would otherwise stay 0 and
   *  block the trim / shot-detect gates. Pass ``null`` to clear back to
   *  placeholder. Does NOT chain a trim -- caller clicks Trim explicitly. */
  setStageTime: (
    slug: string,
    stageNumber: number,
    timeSeconds: number | null,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/time`,
      {
        method: "POST",
        json: { time_seconds: timeSeconds },
      },
    ),

  /** Manually set or clear ``video``'s beep timestamp. ``beepTime=null``
   *  clears back to "no beep yet"; otherwise the value (>= 0) is taken
   *  as authoritative with ``beep_source="manual"``. Same auto-trim
   *  chain as the legacy primary endpoint, just keyed per video. */
  overrideBeepForVideo: (
    slug: string,
    stageNumber: number,
    videoId: string,
    beepTime: number | null,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep`,
      { method: "POST", json: { beep_time: beepTime } },
    ),

  /** Promote one of the ranked auto-detected candidates as authoritative.
   *  ``time`` is matched against ``primary.beep_candidates`` within 1 ms,
   *  so the SPA can hold a slightly stale snapshot without breaking the
   *  click. The server keeps the candidate list intact so the user can
   *  switch again without re-running detection, and re-fires the trim job. */
  selectBeepCandidate: (slug: string, stageNumber: number, time: number) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/beep/select`,
      {
        method: "POST",
        json: { time },
      },
    ),

  /** Per-video candidate select. Same matching semantics as the primary
   *  endpoint (1 ms epsilon) but targets the video carrying the candidate
   *  list, so secondaries can pick from their own ranked alternatives. */
  selectBeepCandidateForVideo: (
    slug: string,
    stageNumber: number,
    videoId: string,
    time: number,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/select`,
      { method: "POST", json: { time } },
    ),

  /** Snap a user-placed beep marker to the strongest tone in a tight
   *  window around the hint. Stateless -- the caller decides whether to
   *  accept the proposal as a manual override. 404 means "no run met
   *  duration / amplitude in that window"; widen ``windowS`` or move
   *  the marker. */
  snapBeepForVideo: (
    slug: string,
    stageNumber: number,
    videoId: string,
    hintTime: number,
    windowS: number = 0.5,
  ) =>
    request<BeepSnapResult>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/snap`,
      { method: "POST", json: { hint_time: hintTime, window_s: windowS } },
    ),

  /** Flip ``beep_reviewed`` on a single video (issue #71). Pure UI-state
   *  endpoint -- no detection or trim chain runs. Setting ``true``
   *  requires ``beep_time`` to already be set; the server returns 400
   *  otherwise. */
  setBeepReviewed: (
    slug: string,
    stageNumber: number,
    videoId: string,
    reviewed: boolean,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/review`,
      { method: "POST", json: { reviewed } },
    ),

  /** Submit an audit-mode short-GOP trim job. Returns a Job snapshot;
   *  idempotent on the worker side -- when the cached MP4 is fresh the
   *  job completes near-instantly without re-encoding. */
  trimStage: (slug: string, stageNumber: number) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/trim`,
      { method: "POST" },
    ),

  /** Per-video audit-mode trim. Mirrors ``trimStage`` for primaries but
   *  targets one specific camera, so multi-cam ingest can refresh a
   *  single secondary's scrub clip without retriggering the primary. */
  trimVideo: (slug: string, stageNumber: number, videoId: string) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/trim`,
      { method: "POST" },
    ),

  /** Submit a shot-detection job for the stage's audit clip. The job
   *  populates _candidates_pending_audit in the audit JSON; the audit
   *  screen renders markers from there. Auto-triggered after trim;
   *  this endpoint is for manual retrigger.
   *  Pass ``reset: true`` to wipe ``shots[]`` first, discarding the user's
   *  keep / reject decisions so the next pass starts fresh. */
  detectShots: (
    slug: string,
    stageNumber: number,
    opts: { reset?: boolean } = {},
  ) => {
    const qs = opts.reset ? "?reset=true" : "";
    return request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/shot-detect${qs}`,
      { method: "POST" },
    );
  },

  /** Submit shot detection on every eligible stage. A stage is eligible
   *  when it has a primary with confirmed beep + non-zero time_seconds.
   *  Returns the list of submitted (or already-active) jobs plus a
   *  ``skipped`` array describing why ineligible stages were left alone. */
  detectShotsAll: (slug: string, opts: { reset?: boolean } = {}) => {
    const qs = opts.reset ? "?reset=true" : "";
    return request<{
      jobs: Job[];
      skipped: { stage_number: number; reason: string }[];
    }>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/shot-detect${qs}`,
      { method: "POST" },
    );
  },

  /** Server-side feature flags (fetched once on app mount). Surfaces:
   *  - ``lab`` -- hide the Lab nav entry unless ``splitsmith ui --lab``.
   *  - ``mode`` -- ``"local"`` vs ``"hosted"``; SPA suppresses host
   *    filesystem pickers / project-folder inputs in hosted mode. */
  getServerFeatures: () =>
    request<{ lab: boolean; mode: "local" | "hosted" }>("/api/server/features"),

  /** The authenticated account, or a 401 (caught via ``isUnauthorized``)
   *  when signed out. Always 200 in local mode (loopback user). */
  getMe: () => request<AuthUser>("/api/me"),

  /** Hosted mode -- start a magic-link sign-in: the server e-mails a link
   *  to ``email``. Always 200 (never reveals whether the address has an
   *  account); the account is created when the link is redeemed. */
  authBegin: (email: string) =>
    request<{ ok: true }>("/api/v1/auth/begin", {
      method: "POST",
      json: { email },
    }),

  /** Hosted mode -- revoke the current session + clear the cookie. */
  authLogout: () =>
    request<{ ok: true }>("/api/v1/auth/logout", { method: "POST" }),

  /** Hosted-mode only -- list the operator's uploaded raw videos.
   *  Empty array (not 404) when nothing has been uploaded yet. The
   *  picker renders the empty state on length === 0. */
  listRawUploads: () =>
    request<{ uploads: RawUploadEntry[] }>("/api/me/raw/list"),

  /** Hosted-mode only -- remove an uploaded raw video. Idempotent
   *  (200 on already-gone); the SPA can retry without special-casing. */
  deleteRawUpload: (filename: string) =>
    request<{ ok: true; path: string }>(
      `/api/me/raw/${encodeURIComponent(filename)}`,
      { method: "DELETE" },
    ),

  /** Hosted-mode only -- upload one file via multipart/form-data to
   *  ``POST /api/me/raw/upload``. Returns the server's response
   *  (path/size/sha256/filename).
   *
   *  Uses ``XMLHttpRequest`` rather than ``fetch`` because ``fetch``
   *  exposes no upload progress events in any current browser. The
   *  ``onProgress`` callback fires whenever the browser flushes bytes
   *  to the network so the SPA can render a real progress bar (200-
   *  500 MB raw videos take long enough that no-progress feels
   *  broken).
   *
   *  ``signal`` is an ``AbortSignal`` from the caller so cancel
   *  buttons can yank an in-flight upload; the underlying
   *  ``xhr.abort()`` rolls the request back. The server's
   *  ``boto3.TransferManager`` aborts the multipart on its own when
   *  the connection drops, so a cancelled upload doesn't leak. */
  uploadRawFile: (
    file: File,
    opts: {
      sha256?: string | null;
      onProgress?: (bytesSent: number, totalBytes: number) => void;
      signal?: AbortSignal;
    } = {},
  ): Promise<RawUploadResult> => {
    // Large files go direct browser -> R2 via presigned multipart; the
    // serve-proxied single-shot below 502s on Railway past a few hundred
    // MB. Small files keep the simpler single-shot (one round-trip, and
    // serve can still compute the integrity sha256).
    if (file.size >= MULTIPART_THRESHOLD_BYTES) {
      return uploadRawMultipart(file, { onProgress: opts.onProgress, signal: opts.signal });
    }
    return new Promise<RawUploadResult>(
      (resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const url = "/api/me/raw/upload";
        xhr.open("POST", url, true);
        // Server-side multipart parser keys off ``file`` -- mirror
        // the ``files={"file": ...}`` shape the curl path uses.
        const form = new FormData();
        form.append("file", file, file.name);
        if (opts.sha256) {
          xhr.setRequestHeader("X-Content-SHA256", opts.sha256);
        }
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && opts.onProgress) {
            opts.onProgress(e.loaded, e.total);
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText));
            } catch (e) {
              reject(
                new ApiError(
                  xhr.status,
                  `invalid upload response: ${e instanceof Error ? e.message : String(e)}`,
                  null,
                ),
              );
            }
            return;
          }
          // Try to parse a structured FastAPI error body; fall back to
          // status text otherwise.
          let detail: unknown = xhr.statusText;
          try {
            const body = JSON.parse(xhr.responseText);
            if (body && typeof body === "object" && "detail" in body) {
              detail = body.detail;
            }
          } catch {
            // ignore -- detail stays as the status text
          }
          const msg = typeof detail === "string" ? detail : JSON.stringify(detail);
          reject(new ApiError(xhr.status, msg, detail));
        };
        xhr.onerror = () => {
          reject(new ApiError(0, "network error during upload", null));
        };
        xhr.onabort = () => {
          reject(new ApiError(0, "upload cancelled", null));
        };
        if (opts.signal) {
          if (opts.signal.aborted) {
            reject(new ApiError(0, "upload cancelled", null));
            return;
          }
          opts.signal.addEventListener("abort", () => xhr.abort());
        }
        xhr.send(form);
      },
    );
  },

  /** Hosted-mode only -- register an uploaded raw video on the
   *  shooter's project (``POST /api/shooters/{slug}/raw-videos/attach``).
   *
   *  Body shape mirrors what ``uploadRawFile`` echoed back:
   *  ``filename`` is required; ``sha256`` / ``size_bytes`` are
   *  optional (the server defers to S3's ContentLength either way).
   *  ``covers_stages`` is optional -- omit it for the
   *  attach-into-unassigned-tray flow, pass an explicit array when
   *  the caller already knows which stages the recording spans.
   *
   *  Returns the canonical ``RawVideo`` after dedup-merge: a re-attach
   *  with a different covers_stages set unions them server-side. */
  attachRawVideo: (
    slug: string,
    body: {
      filename: string;
      sha256?: string | null;
      size_bytes?: number | null;
      covers_stages?: number[] | null;
      /** Client-probed duration from the video element metadata. */
      duration_seconds?: number | null;
      /** Recording start derived from file.lastModified - duration_s (UTC ISO). */
      recorded_start?: string | null;
    },
  ) =>
    request<RawVideoManifestEntry>(
      `/api/shooters/${encodeURIComponent(slug)}/raw-videos/attach`,
      { method: "POST", json: body },
    ),

  /** Suggest which stages a file's span covers. The SPA probes the
   *  file duration client-side and passes the result here to avoid a
   *  server-side ffprobe on remote objects. ``recorded_start`` must be
   *  a timezone-aware ISO string (always UTC/Z from the browser). In
   *  local mode, passing ``path`` lets the server derive the span from
   *  disk metadata when recorded_start is null. */
  suggestCoverage: (
    slug: string,
    body: { recorded_start?: string | null; duration_s?: number | null; path?: string | null },
  ) =>
    request<CoverageSuggestion>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/suggest-coverage`,
      { method: "POST", json: body },
    ),

  /** Update the per-stage coverage list for a registered raw video.
   *  Declared order is preserved (shooting order for sequential-mode
   *  takes). Returns the updated RawVideoManifestEntry post-merge. */
  setRawVideoCoverage: (
    slug: string,
    body: { filename: string; covers_stages: number[] },
  ) =>
    request<RawVideoManifestEntry>(
      `/api/shooters/${encodeURIComponent(slug)}/raw-videos/coverage`,
      { method: "PATCH", json: body },
    ),

  /** Take-level overview: per-stage beep status + windows + conflict
   *  list for the named raw video file. */
  takeOverview: (slug: string, filename: string) =>
    request<TakeOverview>(
      `/api/shooters/${encodeURIComponent(slug)}/raw-videos/overview?filename=${encodeURIComponent(filename)}`,
    ),

  /** Whole-take envelope peaks. Hosted mode may return
   *  ``{ pending: true; active_job: boolean }`` (202) while the
   *  worker computes them; poll and render a spinner in that case. */
  takePeaks: (slug: string, filename: string, bins = 3000) =>
    request<PeaksResult | { pending: true; active_job: boolean }>(
      `/api/shooters/${encodeURIComponent(slug)}/raw-videos/peaks?filename=${encodeURIComponent(filename)}&bins=${bins}`,
    ),

  /** Persist a manual beep-search window for a stage-video and
   *  re-queue detection. Wipes the current beep and trim cache.
   *  Returns the newly queued Job snapshot. */
  setBeepWindow: (
    slug: string,
    stageNumber: number,
    videoId: string,
    body: { start_s: number; end_s: number },
  ) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-window`,
      { method: "PUT", json: body },
    ),

  /** Server health + bind state. The picker route polls this on mount
   *  to decide whether the user landed in unbound mode (boot with no
   *  ``--project``) or whether a project is already open. */
  getHealth: () => request<ServerHealth>("/api/health"),

  /** Saved SSI Scoreboard identity for the operator running this install,
   *  or null when nothing has been pinned yet. The header chrome uses
   *  this to render the user badge; null hides it. The server returns
   *  ``200 null`` for the "not pinned" case so it doesn't show up as a
   *  failed request in DevTools. */
  getScoreboardIdentity: () =>
    request<ScoreboardIdentity | null>("/api/me/scoreboard-identity"),

  /** Recent-projects list, most-recent first. Drives the picker. */
  getRecentProjects: () =>
    request<{ projects: RecentProject[] }>("/api/me/recent-projects").then(
      (r) => r.projects,
    ),

  /** Enriched recent-projects list with on-disk metadata (kind, shooters,
   *  stages, status). Used by the redesigned match picker (#322). */
  getRecentProjectsDetail: () =>
    request<{ projects: RecentProjectDetail[] }>(
      "/api/me/recent-projects?detail=true",
    ).then((r) => r.projects),

  /** Create a new match from the manual variant of the create-match form
   *  (#322). Scaffolds match.json + first shooter and binds. */
  createMatchManual: (body: CreateMatchManualBody) =>
    request<ServerHealth>("/api/match/create-manual", {
      method: "POST",
      json: body,
    }),

  /** List every shooter in the currently-bound match (#324). */
  listMatchShooters: () =>
    request<ShooterListResponse>("/api/match/shooters"),

  /** Queue trim-cache rebuild jobs for every missing-but-rebuildable stage
   *  in the named shooter's project. Works against the shooter's project
   *  root directly, so it does not change which shooter the server is
   *  currently bound to. (#351) */
  buildShooterTrimCaches: (slug: string) =>
    request<BuildTrimCachesResult>(
      `/api/match/shooters/${encodeURIComponent(slug)}/build-trim-caches`,
      { method: "POST" },
    ),

  /** Add a new shooter to the bound match (#324). */
  addMatchShooter: (body: { name: string; division?: string | null }) =>
    request<ShooterListResponse>("/api/match/shooters", {
      method: "POST",
      json: body,
    }),

  /** Remove a shooter from the bound match (#324). */
  removeMatchShooter: (slug: string) =>
    request<ShooterListResponse>(
      `/api/match/shooters/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    ),

  /** Per-shooter compare data for a stage (#328). */
  getStageCompare: (stageNumber: number) =>
    request<CompareStageResponse>(
      `/api/match/stage/${stageNumber}/compare`,
    ),

  /** Build a streaming URL for one shooter's lossless trim (#328). */
  shooterVideoStreamUrl: (slug: string, path: string): string =>
    scopeRequestPath(
      `/api/match/shooters/${encodeURIComponent(slug)}/videos/stream?path=${encodeURIComponent(path)}`,
    ),

  /** Cross-shooter beep review queue (#326). Pass
   *  ``includeConfirmed`` to also receive already-reviewed items so
   *  the operator can revisit a settled beep -- the SPA wires this
   *  to the "Show confirmed" toggle. */
  getBeepQueue: (includeConfirmed = false) =>
    request<BeepQueueResponse>(
      includeConfirmed
        ? "/api/match/beep-queue?include_confirmed=true"
        : "/api/match/beep-queue",
    ),

  /** Confirm one beep in the queue without changing the active
   *  shooter; writes through to the named shooter's project (#326). */
  confirmBeepInQueue: (body: {
    slug: string;
    stage_number: number;
    video_id: string;
    time?: number | null;
    source?: "detected" | "manual" | "alt";
  }) =>
    request<BeepQueueResponse>("/api/match/beep-queue/confirm", {
      method: "POST",
      json: body,
    }),

  /** Create a new match from a picked scoreboard match (#322). The SPA
   *  follows up with /api/scoreboard/fetch + /select-shooter to populate
   *  stages and pin the primary competitor. */
  createMatchFromScoreboard: (body: CreateMatchScoreboardBody) =>
    request<ServerHealth>("/api/match/create-from-scoreboard", {
      method: "POST",
      json: body,
    }),

  /** Delete a project/match and clean up every resource it owns (DB rows,
   *  object storage, in-flight jobs, and -- when opted in -- raw uploads or
   *  the on-disk folder). Returns the deletion summary plus the refreshed
   *  list so the picker can re-render without a follow-up GET. */
  deleteProject: (path: string, opts?: DeleteProjectOptions) =>
    request<DeleteProjectResponse>("/api/me/recent-projects/delete", {
      method: "POST",
      json: {
        path,
        delete_local_files: opts?.deleteLocalFiles ?? false,
        delete_raw_uploads: opts?.deleteRawUploads ?? false,
      },
    }),

  /** Switch the in-memory project. The picker calls this when the user
   *  selects an entry; the server updates ``last_opened_at`` and binds.
   */
  bindProject: (
    path: string,
    name?: string,
    opts?: { create?: boolean },
  ) =>
    request<ServerHealth>("/api/me/recent-projects/bind", {
      method: "POST",
      json: { path, name, create: opts?.create ?? false },
    }),

  /** Drop the bound project so the SPA returns to the picker (Cmd+P
   *  "Switch project..." path). */
  unbindProject: () =>
    request<ServerHealth>("/api/me/recent-projects/unbind", { method: "POST" }),

  listJobs: (opts?: { signal?: AbortSignal }) =>
    request<Job[]>("/api/me/jobs", { signal: opts?.signal }),
  getJob: (jobId: string, opts?: { signal?: AbortSignal }) =>
    request<Job>(`/api/me/jobs/${encodeURIComponent(jobId)}`, {
      signal: opts?.signal,
    }),

  /** Request cooperative cancellation. Idempotent: a finished job is returned
   *  as-is. For a running trim job the server terminates the underlying
   *  ffmpeg subprocess so the cancel takes effect immediately. */
  cancelJob: (jobId: string) =>
    request<Job>(`/api/me/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  /** Mark a single failed job as seen (issue #73). The badge stops
   *  counting it and the registry rolls it off ahead of unacknowledged
   *  failures. No-op for non-failed / already-acknowledged jobs. */
  acknowledgeJob: (jobId: string) =>
    request<Job>(`/api/me/jobs/${encodeURIComponent(jobId)}/acknowledge`, { method: "POST" }),

  /** Bulk-dismiss every currently-unacknowledged failure. Returns the
   *  jobs that actually flipped to acknowledged. */
  acknowledgeAllFailures: () =>
    request<Job[]>(`/api/me/jobs/acknowledge-failures`, { method: "POST" }),

  /** Poll a job until it leaves the running state. ``onUpdate`` fires on
   *  every snapshot (including the final one). Returns the terminal Job. */
  pollJob: async (
    jobId: string,
    onUpdate: (job: Job) => void,
    opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
  ): Promise<Job> => {
    const interval = opts.intervalMs ?? 750;
    const deadline = Date.now() + (opts.timeoutMs ?? 10 * 60 * 1000);
    const { signal } = opts;
    while (true) {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      const job = await request<Job>(
        `/api/me/jobs/${encodeURIComponent(jobId)}`,
        { signal },
      );
      onUpdate(job);
      if (
        job.status === "succeeded" ||
        job.status === "failed" ||
        job.status === "cancelled"
      ) return job;
      if (Date.now() > deadline) {
        throw new Error(`Timed out waiting for job ${jobId}`);
      }
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, interval);
        if (signal) {
          signal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              reject(new DOMException("aborted", "AbortError"));
            },
            { once: true },
          );
        }
      });
    }
  },

  stageAudioUrl: (slug: string, stageNumber: number) =>
    scopeRequestPath(`/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audio`),

  /** Per-video WAV URL. Primary forwards to the legacy stage audio
   *  endpoint (trimmed audit clip preferred); secondary serves the full
   *  per-cam WAV so the picker has the whole clip to scrub. */
  videoAudioUrl: (slug: string, stageNumber: number, videoId: string) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/audio`,
    ),

  /** URL for a tiny MP4 around a beep timestamp (#27, #22). ``t`` is
   *  passed to the server (which centres the clip there) AND ms-rounded
   *  into the cache key, so each distinct ``t`` gets its own MP4. The
   *  candidate picker uses this with arbitrary candidate times; the
   *  default flow passes ``primary.beep_time``. */
  stageBeepPreviewUrl: (slug: string, stageNumber: number, beepTime: number) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/beep-preview?t=${beepTime.toFixed(3)}`,
    ),

  /** Per-video beep preview URL. Same caching semantics as the primary
   *  endpoint (cached on source mtime/size + center time + duration). */
  videoBeepPreviewUrl: (
    slug: string,
    stageNumber: number,
    videoId: string,
    beepTime: number,
  ) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-preview?t=${beepTime.toFixed(3)}`,
    ),

  /** Stream URL for a registered video.
   *
   *  ``kind`` selects what the server serves and is encoded into the URL
   *  so the browser sees a different resource per kind. The audit screen
   *  passes ``trim`` or ``source`` explicitly so a background trim job
   *  completing mid-playback can't switch the file under an open
   *  ``<video>`` element (which fails its next Range request with
   *  "source not found"). Other callers default to ``auto``: trim if
   *  present, source otherwise. */
  videoStreamUrl: (
    slug: string,
    videoPath: string,
    kind: "auto" | "trim" | "source" = "auto",
  ) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/videos/stream?path=${encodeURIComponent(videoPath)}&kind=${kind}`,
    ),

  /** Build a download URL for one finished export deliverable (#447 part 2).
   *  ``filename`` is the basename under the project's ``exports/`` dir.
   *  Hosted mode pulls the file from object storage before serving; local
   *  mode reads it off disk. Used in place of "Reveal in Finder" in hosted
   *  mode, where the worker that produced the file ran in a separate
   *  container. Mirrors ``videoStreamUrl``: a bare path consumed as an
   *  ``<a href download>``, not a fetch. */
  exportFileUrl: (slug: string, filename: string) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/exports/file/${encodeURIComponent(filename)}`,
    ),

  getStagePeaks: (slug: string, stageNumber: number, bins = 1200) =>
    request<PeaksResult>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/peaks?bins=${bins}`,
    ),

  /** Per-video peaks. Same payload shape as the stage endpoint so the
   *  waveform picker takes the same code path for every role. */
  getVideoPeaks: (
    slug: string,
    stageNumber: number,
    videoId: string,
    bins = 1200,
  ) =>
    request<PeaksResult>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/peaks?bins=${bins}`,
    ),

  /** Returns the saved audit JSON for a stage, or null when none exists yet.
   *  Server returns ``200 null`` for the "no audit yet" state so this
   *  doesn't show up as a failed request in DevTools. */
  getStageAudit: (slug: string, stageNumber: number) =>
    request<StageAudit | null>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audit`,
    ),

  /** Atomically write the stage's audit JSON. The server keeps the prior
   *  version as ``stage<N>.json.bak`` so a bad save can be recovered. */
  saveStageAudit: (slug: string, stageNumber: number, payload: StageAudit) =>
    request<StageAudit>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audit`,
      {
        method: "PUT",
        json: payload,
      },
    ),

  /** Coach view: per-shot interval class + flags + notes (#161). The
   *  GET is read-only; ``stale=true`` means the rule disagrees with the
   *  stored class and the user can accept the recompute via reclassify.
   *  Returns null when the stage has no audit JSON yet. */
  getStageCoach: (slug: string, stageNumber: number) =>
    request<CoachStageResponse | null>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/coach`,
    ),

  reclassifyStageCoach: (slug: string, stageNumber: number) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/coach/reclassify`,
      {
        method: "POST",
      },
    ),

  patchStageShotCoach: (
    slug: string,
    stageNumber: number,
    shotNumber: number,
    patch: CoachShotPatch,
  ) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/shots/${shotNumber}/coach`,
      { method: "PATCH", json: patch },
    ),

  /** Per-stage histograms + summary stats for the Coach distributions
   *  panel (#163). Empty classes still appear with count=0 so the UI
   *  can render an empty histogram without a special case. */
  getStageCoachDistributions: (slug: string, stageNumber: number) =>
    request<CoachStageDistributions>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/coach/distributions`,
    ),

  /** Match-level histograms aggregated across every stage with an audit
   *  JSON. Stages without an audit are silently skipped server-side. */
  getMatchCoachDistributions: (slug: string) =>
    request<CoachMatchDistributions>(
      `/api/shooters/${encodeURIComponent(slug)}/coach/distributions`,
    ),

  /** Structured anomalies for the *saved* audit JSON (issue #42).
   *
   *  The audit screen does its own live recompute (see ``lib/anomalies``)
   *  so the panel updates without a network round-trip on every keep /
   *  reject. This endpoint exists for external consumers + integration
   *  tests that want the same flags ``report.txt`` will produce. */
  getStageAnomalies: (slug: string, stageNumber: number) =>
    request<{ anomalies: import("./anomalies").Anomaly[] }>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/anomalies`,
    ),

  /** Match-overview payload for the Analysis & Export screen. */
  getExportOverview: (slug: string) =>
    request<ExportOverview>(
      `/api/shooters/${encodeURIComponent(slug)}/exports/overview`,
    ),

  /** Submit a stage export job. Returns a Job snapshot; poll
   *  ``/api/me/jobs/{id}`` (or {@link api.pollJob}) until it leaves the
   *  running state, then re-fetch the export overview to see updated
   *  paths + ``last_export_at``. Idempotent on the worker side: the
   *  registry dedupes by (kind, stage_number) so double-clicking
   *  Generate returns the in-flight job instead of stacking. */
  exportStage: (
    slug: string,
    stageNumber: number,
    opts: ExportStageRequestPayload = {},
  ) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/export`,
      {
      method: "POST",
      json: {
        write_trim: opts.write_trim ?? true,
        write_csv: opts.write_csv ?? true,
        write_fcpxml: opts.write_fcpxml ?? true,
        write_report: opts.write_report ?? true,
        write_overlay: opts.write_overlay ?? false,
        overlay_codec: opts.overlay_codec ?? "auto",
        // Forward ``null`` to keep "match source" but only attach the key
        // when the caller set something explicit, so the server's defaults
        // remain authoritative when the UI doesn't care.
        ...(opts.overlay_max_height !== undefined
          ? { overlay_max_height: opts.overlay_max_height }
          : {}),
        ...(opts.overlay_max_fps !== undefined
          ? { overlay_max_fps: opts.overlay_max_fps }
          : {}),
        // ``undefined`` => omit (server picks the legacy "all cams with a
        // beep" default); ``null`` / ``[]`` are forwarded as-is so the
        // caller can explicitly exclude all secondaries.
        ...(opts.secondary_video_ids !== undefined
          ? { secondary_video_ids: opts.secondary_video_ids }
          : {}),
      },
    }),

  /** Stitch N stages into one FCPXML (issue #171, #172). Job-queued: the
   *  worker re-runs any missing per-stage exports (trim + optional
   *  overlay) before stitching, so a fresh project goes from "audit done"
   *  to "match FCPXML on disk" in one click. Returns a Job snapshot;
   *  poll via {@link api.pollJob} until terminal, then read the
   *  {@link MatchExportResult} from ``Job.result``. */
  exportMatch: (slug: string, payload: MatchExportRequestPayload) =>
    request<Job>(`/api/shooters/${encodeURIComponent(slug)}/export/match`, {
      method: "POST",
      json: {
        stage_numbers: payload.stage_numbers,
        head_pad_seconds: payload.head_pad_seconds ?? 5.0,
        tail_pad_seconds: payload.tail_pad_seconds ?? 5.0,
        include_secondaries: payload.include_secondaries ?? true,
        include_overlay: payload.include_overlay ?? true,
        overlay_codec: payload.overlay_codec ?? "auto",
        ...(payload.overlay_max_height !== undefined
          ? { overlay_max_height: payload.overlay_max_height }
          : {}),
        ...(payload.overlay_max_fps !== undefined
          ? { overlay_max_fps: payload.overlay_max_fps }
          : {}),
        ...(payload.project_name !== undefined
          ? { project_name: payload.project_name }
          : {}),
        ...(payload.pip_layout !== undefined
          ? { pip_layout: payload.pip_layout }
          : {}),
        ...(payload.output_format !== undefined
          ? { output_format: payload.output_format }
          : {}),
        ...(payload.transition_kind !== undefined
          ? { transition_kind: payload.transition_kind }
          : {}),
        ...(payload.transition_duration_seconds !== undefined
          ? { transition_duration_seconds: payload.transition_duration_seconds }
          : {}),
        ...(payload.title_kind !== undefined
          ? { title_kind: payload.title_kind }
          : {}),
        ...(payload.title_duration_seconds !== undefined
          ? { title_duration_seconds: payload.title_duration_seconds }
          : {}),
        ...(payload.intro_path !== undefined
          ? { intro_path: payload.intro_path }
          : {}),
        ...(payload.outro_path !== undefined
          ? { outro_path: payload.outro_path }
          : {}),
        ...(payload.youtube_sidecar !== undefined
          ? { youtube_sidecar: payload.youtube_sidecar }
          : {}),
        ...(payload.youtube_preset !== undefined
          ? { youtube_preset: payload.youtube_preset }
          : {}),
      },
    }),

  /** List export templates (issue #198). Used by the export dialog
   *  to populate a "Template" dropdown; selecting a template
   *  pre-fills the dialog state with its values. User templates
   *  ship under ``~/.splitsmith/templates/`` and override built-ins
   *  by ``id``. */
  getMatchTemplates: () =>
    request<{ templates: MatchExportTemplateEntry[] }>(
      "/api/match/templates",
    ),

  /** Open the OS file manager at ``path`` (selecting the file on macOS /
   *  Windows; opening the parent dir on Linux). The backend rejects paths
   *  outside the project root. */
  revealFile: (path: string) =>
    request<{ revealed: string }>("/api/files/reveal", {
      method: "POST",
      json: { path },
    }),

  /** Reveal a registered project video by following its symlink to the
   *  original source location (USB / Downloads / wherever the user picked
   *  it from). Use this for the per-video "open containing folder" button
   *  -- ``revealFile`` would refuse because the resolved target lives
   *  outside the project root. */
  revealVideo: (slug: string, videoPath: string) =>
    request<{ revealed: string }>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/reveal`,
      {
        method: "POST",
        json: { path: videoPath },
      },
    ),

  // -----------------------------------------------------------------------
  // Fixture mode (closes #19): the /review SPA route reads + writes a single
  // audit fixture (JSON + sibling WAV + optional video) without project
  // context. Folds the old splitsmith.review_server standalone into this
  // build so the audit primitives are shared.
  // -----------------------------------------------------------------------

  getFixtureAudit: (fixturePath: string) =>
    request<StageAudit>(`/api/fixture/audit?path=${encodeURIComponent(fixturePath)}`),

  saveFixtureAudit: (fixturePath: string, payload: StageAudit) =>
    request<StageAudit>(`/api/fixture/audit?path=${encodeURIComponent(fixturePath)}`, {
      method: "PUT",
      json: payload,
    }),

  getFixturePeaks: (fixturePath: string, bins = 1200) =>
    request<PeaksResult>(
      `/api/fixture/peaks?path=${encodeURIComponent(fixturePath)}&bins=${bins}`,
    ),

  fixtureAudioUrl: (fixturePath: string) =>
    `/api/fixture/audio?path=${encodeURIComponent(fixturePath)}`,

  fixtureVideoUrl: (videoPath: string) =>
    `/api/fixture/video?path=${encodeURIComponent(videoPath)}`,

  // -----------------------------------------------------------------------
  // Lab: fixture management + ensemble eval + tuning. Mirrors splitsmith.lab
  // (the Python module) -- see src/splitsmith/lab/core.py for shapes.
  // -----------------------------------------------------------------------

  listLabFixtures: () => request<LabFixtureRecord[]>("/api/lab/fixtures"),

  /** Hydrate the Lab page after a navigation: returns the most recent
   *  eval/rescore in this server session, or 404 when none has run. */
  getLastLabRun: () => request<LabEvalRun>("/api/lab/last-run"),

  /** Submit the eval and return the Job snapshot. The SPA polls via
   *  {@link api.pollJob} and then fetches the result with
   *  {@link api.getLastLabRun} once the job succeeds. */
  runLabEval: (payload: { slugs?: string[]; config?: Partial<LabEvalConfig>; persist?: boolean }) =>
    request<Job>("/api/lab/eval", { method: "POST", json: payload }),

  rescoreLabUniverse: (config: Partial<LabEvalConfig>) =>
    request<LabEvalRun>("/api/lab/rescore", { method: "POST", json: { config } }),

  promoteFixture: (payload: { stage_number: number; slug: string; overwrite?: boolean }) =>
    request<LabFixtureRecord>("/api/lab/promote", { method: "POST", json: payload }),

  saveLabConfig: (payload: { name: string; note?: string; overwrite?: boolean }) =>
    request<{ path: string }>("/api/lab/save-config", { method: "POST", json: payload }),

  applyLabLabels: (payload: {
    audit_path: string;
    labels: {
      candidate_number: number;
      time: number;
      reason?: string | null;
      subclass?: string | null;
    }[];
  }) =>
    request<{
      path: string;
      counts: Record<string, number>;
      run: LabEvalRun | null;
    }>("/api/lab/labels", {
      method: "POST",
      json: payload,
    }),

  rebuildLabCalibration: (payload: { target_recall?: number; tolerance_ms?: number; fixtures?: string[] } = {}) =>
    request<Job>("/api/lab/rebuild-calibration", { method: "POST", json: payload }),

  /** Plan a merge of N legacy single-shooter projects into one match
   *  folder. Returns the reconciled stages + shooter assignments. The
   *  server returns 409 with the conflict message when stage definitions
   *  or names disagree -- catch ApiError and surface .detail to the user. */
  planMatchMerge: (payload: {
    inputs: string[];
    output?: string;
    name?: string;
  }) => request<MergePlanResponse>("/api/match/merge/plan", { method: "POST", json: payload }),

  /** Execute the merge. Writes the new match folder, registers it in
   *  recent-projects, binds the first shooter as active. Returns a
   *  ServerHealth pointing at the bound shooter. */
  executeMatchMerge: (payload: {
    inputs: string[];
    output: string;
    name?: string;
    move?: boolean;
  }) => request<ServerHealth>("/api/match/merge/execute", { method: "POST", json: payload }),

  /** Developer-mode model info: active version, recall, fixture count,
   *  + step-counter map used to colour the workflow stepper. */
  getDeveloperModel: () => request<DeveloperModelInfo>("/api/dev/model"),

  /** Review-queue rollup: promoted-from-match items, GitHub submissions,
   *  ad-hoc fixture review tasks. Drives /dev/review. */
  getDevReviewQueue: () => request<DevReviewQueueResponse>("/api/dev/review-queue"),

  promoteFromAnchor: (payload: {
    anchor_path: string;
    secondary_wav_path: string;
    slug: string;
    camera_id: string;
    mount: string;
    position: string;
    audio_source?: string;
    agc_state?: string;
    snap_window_ms?: number;
    overwrite?: boolean;
  }) =>
    request<{ job: Job; fixture_path: string; anchor_path: string; slug: string }>(
      "/api/lab/promote-from-anchor",
      { method: "POST", json: payload },
    ),

  getPromoteReport: (slug: string) =>
    request<PromoteReport>(`/api/lab/promote-report?slug=${encodeURIComponent(slug)}`),

  deleteFixture: (slug: string) =>
    request<{ removed: string[] }>(
      `/api/lab/fixture?slug=${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    ),

  /** Ensemble parameter-sweep dashboard (read-only). Backed by
   *  ``build/sweeps/runs.parquet`` written by ``scripts/run_sweep.py``. */
  listSweepRuns: () => request<SweepRunSummary[]>("/api/lab/sweeps"),

  /** Full per-combo + per-fixture detail for one sweep run. */
  getSweepRun: (runId: string) =>
    request<SweepRunDetail>(`/api/lab/sweeps/${encodeURIComponent(runId)}`),

  /** Build a URL the SPA can drop into an ``<img src>`` -- request goes
   *  through FastAPI so the gitignored build/ tree is never directly
   *  exposed. ``plotName`` is the file stem without ``.png``. */
  sweepPlotUrl: (runId: string, plotName: string): string =>
    `/api/lab/sweeps/${encodeURIComponent(runId)}/plot/${encodeURIComponent(plotName)}.png`,

  promoteSecondary: (
    shooterSlug: string,
    stageNumber: number,
    videoId: string,
    payload: {
      mount: string;
      position: string;
      audio_source?: string;
      agc_state?: string;
      snap_window_ms?: number;
      slug?: string;
      camera_id?: string;
      overwrite?: boolean;
    },
  ) =>
    request<{
      job: Job;
      fixture_path: string;
      anchor_path: string;
      slug: string;
      camera_id: string;
      anchor_slug: string;
    }>(
      `/api/shooters/${encodeURIComponent(shooterSlug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/promote-secondary`,
      { method: "POST", json: payload },
    ),

  /** Anchor a project video against an arbitrary fixture (issue #149
   *  follow-up). Lab-only. Used when the headcam ground truth lives
   *  as a fixture in ``tests/fixtures/`` and the project has a phone-cam
   *  primary that should be aligned against it. */
  promoteAgainstFixture: (
    shooterSlug: string,
    stageNumber: number,
    videoId: string,
    payload: {
      anchor_slug: string;
      mount: string;
      position: string;
      audio_source?: string;
      agc_state?: string;
      snap_window_ms?: number;
      slug?: string;
      camera_id?: string;
      overwrite?: boolean;
    },
  ) =>
    request<{
      job: Job;
      fixture_path: string;
      anchor_path: string;
      slug: string;
      camera_id: string;
      anchor_slug: string;
    }>(
      `/api/lab/projects/${encodeURIComponent(shooterSlug)}/${stageNumber}/videos/${encodeURIComponent(videoId)}/promote-against-fixture`,
      { method: "POST", json: payload },
    ),

  /** Build the export URL for the currently bound project. Open in a new
   *  tab / window to trigger a download; using a real URL (not fetch)
   *  lets the browser write to disk without buffering the whole archive
   *  in memory. */
  exportProjectUrl: (
    slug: string,
    opts?: {
      includeTrimmed?: boolean;
      includeExports?: boolean;
      includeRaw?: boolean;
      includeAudio?: boolean;
    },
  ) => {
    const params = new URLSearchParams();
    if (opts?.includeTrimmed) params.set("include_trimmed", "true");
    if (opts?.includeExports) params.set("include_exports", "true");
    if (opts?.includeRaw) params.set("include_raw", "true");
    if (opts?.includeAudio) params.set("include_audio", "true");
    const qs = params.toString();
    return scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/project/export${qs ? `?${qs}` : ""}`,
    );
  },

  /** Restore a previously exported project. The server extracts the
   *  archive under ``destRoot``. When ``bind`` is true the new project
   *  is bound + recorded so the SPA can navigate straight into it. */
  importProject: (
    archive: File,
    destRoot: string,
    opts?: { overwrite?: boolean; bind?: boolean },
  ) => {
    const form = new FormData();
    form.append("archive", archive);
    form.append("dest_root", destRoot);
    if (opts?.overwrite) form.append("overwrite", "true");
    if (opts?.bind) form.append("bind", "true");
    return request<{
      project_root: string;
      project_name: string;
      manifest: Record<string, unknown> | null;
    }>("/api/me/projects/import", { method: "POST", body: form });
  },

  /** Move one or more videos from one shooter to another in the same match
   *  (#509). The server carries all per-video state (StageVideo record,
   *  audit JSON, raw symlink) across the relocation atomically. */
  moveShooter: (sourceSlug: string, targetSlug: string, videoPaths: string[]) =>
    request<MoveShooterResponse>("/api/match/videos/move-shooter", {
      method: "POST",
      json: {
        source_slug: sourceSlug,
        target_slug: targetSlug,
        video_paths: videoPaths,
      },
    }),
};

export interface PromoteSnapResult {
  shot_number: number;
  anchor_time: number;
  predicted_time: number;
  snapped_time: number | null;
  displacement_ms: number | null;
  snap_confidence: number | null;
  time_since_beep_s: number;
  sanity_flag: "" | "no-candidate" | "monotonicity" | "min-spacing";
}

export interface PromoteReport {
  slug: string;
  secondary_source: string;
  anchor_slug: string;
  cross_align: {
    secondary_beep_time: number;
    offset_seconds: number;
    /** Null when ``method == "known_beeps"`` -- arithmetic, no
     *  correlation involved. Numeric only on the cross-correlation
     *  fallback. */
    confidence: number | null;
    peak_correlation: number;
    method?: "cross_correlation" | "known_beeps";
  };
  snap_window_ms: number;
  drift_ms_per_minute: number | null;
  counts: {
    anchor_shots: number;
    snapped: number;
    missed: number;
    monotonicity_flagged: number;
    min_spacing_flagged: number;
    voter_a_candidates: number;
    total_candidates: number;
  };
  displacement_stats: {
    mean_ms: number | null;
    stdev_ms: number | null;
    p95_ms?: number | null;
    min_ms: number | null;
    max_ms: number | null;
  };
  amplitude_stats?: {
    median: number | null;
    p10: number | null;
    low_amplitude_shots: number;
  };
  quality?: {
    wrong_clip_suspected: boolean;
    snap_rate: number;
    warnings: string[];
  };
  per_shot: PromoteSnapResult[];
  warnings: string[];
}

export interface LabFixtureRecord {
  slug: string;
  audit_path: string;
  audio_path: string;
  has_audio: boolean;
  n_shots: number;
  expected_rounds: number | null;
  stage_time_seconds: number | null;
  beep_time: number | null;
  source: string | null;
  source_video: string | null;
  audit_mtime: number;
  audio_mtime: number | null;
  /** Set on derived (promoted-from-anchor) fixtures; the SPA uses this
   *  to surface a "re-review" link back to /promote-review. */
  anchor_slug: string | null;
  /** Event grouping key (e.g., ``"blacksmith-2026:6"``). Multi-camera
   *  siblings of the same shooter-stage event share this id; the Lab
   *  table groups by it. ``null`` for fixtures whose slug doesn't match
   *  the standard pattern and which have no explicit ``event_id`` on
   *  disk -- those rows render ungrouped. */
  event_id: string | null;
}

export interface LabEvalConfig {
  consensus: number;
  apriori_boost: number;
  tolerance_ms: number;
  use_expected_rounds: boolean;
  voter_a_floor_override: number | null;
  voter_b_threshold_override: number | null;
  voter_c_threshold_override: number | null;
}

export interface LabEvalCandidate {
  candidate_number: number;
  time: number;
  ms_after_beep: number;
  confidence: number;
  peak_amplitude: number;
  score_c: number;
  clap_diff: number;
  gunshot_prob: number;
  vote_a: number;
  vote_b: number;
  vote_c: number;
  vote_total: number;
  apriori_boost: number;
  ensemble_score: number;
  kept: boolean;
  truth: number;
  matched_shot_number: number | null;
  reason: string | null;
  subclass: string | null;
}

export const LAB_REASONS = [
  "cross_bay",
  "echo",
  "barrel_echo",
  "wind",
  "movement",
  "steel_ring",
  "speech",
  "handling",
  "agc_artifact",
  "other",
  "unknown",
] as const;
export type LabReason = (typeof LAB_REASONS)[number];

export const LAB_SUBCLASSES = ["paper", "steel", "barrel", "unknown"] as const;
export type LabSubclass = (typeof LAB_SUBCLASSES)[number];

export interface LabEvalFixtureMetrics {
  n_truth: number;
  n_kept: number;
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number;
  recall: number;
  f1: number;
  voter_recall: Record<string, number>;
  fp_by_reason: Record<string, number>;
  positives_by_subclass: Record<string, number>;
}

export interface LabEvalFixture {
  slug: string;
  audit_path: string;
  audio_path: string;
  source_video: string | null;
  expected_rounds: number | null;
  candidates: LabEvalCandidate[];
  truth_times: number[];
  metrics: LabEvalFixtureMetrics;
  audit_mtime: number;
  audio_mtime: number | null;
}

export interface LabRunSummary {
  n_fixtures: number;
  n_truth: number;
  n_kept: number;
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number;
  recall: number;
  f1: number;
  fp_by_reason: Record<string, number>;
  positives_by_subclass: Record<string, number>;
}

export interface LabEvalUniverse {
  fixtures: LabEvalFixture[];
  voter_a_floor: number;
  voter_b_threshold: number;
  voter_c_threshold: number;
  tolerance_ms: number;
}

export interface LabEvalRun {
  config: LabEvalConfig;
  summary: LabRunSummary;
  universe: LabEvalUniverse;
  config_hash: string;
  built_at: string;
}

/**
 * Ensemble parameter-sweep types. Mirrors ``src/splitsmith/lab/sweeps.py``.
 * Sweeps are produced by ``scripts/run_sweep.py`` and lurk in
 * ``build/sweeps/runs.parquet``; the Lab tab reads them through
 * ``/api/lab/sweeps``.
 */
export interface SweepFixtureRow {
  fixture: string;
  camera_class: string;
  n_candidates: number;
  n_positives: number;
  n_kept: number;
  true_pos: number;
  false_pos: number;
  false_neg: number;
  precision: number;
  recall: number;
  f1: number;
}

export interface SweepComboRow {
  combo_idx: number;
  params: Record<string, unknown>;
  aggregate: SweepFixtureRow;
  per_class: SweepFixtureRow[];
  per_fixture: SweepFixtureRow[];
}

export interface SweepRunSummary {
  run_id: string;
  signals_build_id: string;
  swept_keys: string[];
  n_combos: number;
  n_fixtures: number;
  best_f1: number;
  best_precision: number;
  best_recall: number;
  best_kept: number;
  best_true_pos: number;
  best_false_pos: number;
  best_false_neg: number;
  best_combo_idx: number;
}

export interface SweepRunDetail {
  summary: SweepRunSummary;
  combos: SweepComboRow[];
  available_plots: string[];
}

/** One successfully moved video in a moveShooter call. Mirrors the backend
 *  ``MoveShooterResultItem`` Pydantic model. */
export interface MoveShooterResultItem {
  video_path: string;
  stage_number: number | null;
  demoted_to_secondary: boolean;
}

/** One video that could not be moved (occupied-stage rule). Mirrors
 *  ``MoveShooterBlocked`` on the backend. */
export interface MoveShooterBlocked {
  video_path: string;
  stage_number: number | null;
  reason: string;
  code: "occupied_stage";
}

/** Outcome of a moveShooter call (backend ``MoveShooterOutcome``). */
export interface MoveShooterOutcome {
  moved: MoveShooterResultItem[];
  blocked: MoveShooterBlocked[];
}

/** Full response from POST /api/match/videos/move-shooter. */
export interface MoveShooterResponse {
  outcome: MoveShooterOutcome;
  source_project: MatchProject;
}

export { ApiError };
