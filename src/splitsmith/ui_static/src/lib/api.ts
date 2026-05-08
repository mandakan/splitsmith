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

export interface StageEntry {
  stage_number: number;
  stage_name: string;
  time_seconds: number;
  scorecard_updated_at: string | null;
  videos: StageVideo[];
  skipped: boolean;
  placeholder: boolean;
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
  transition_kind?: "none" | "cross-dissolve" | "dip-to-color";
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
  transition_kind?: "none" | "cross-dissolve" | "dip-to-color";
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
 *  the SPA polls /api/jobs/{id} until status leaves "pending" / "running". */
export interface Job {
  id: string;
  kind: string;
  stage_number: number | null;
  /** Targets a specific StageVideo when the operation is per-camera
   *  (multi-cam beep / trim). Null for stage-level jobs (shot_detect,
   *  export). The SPA disambiguates concurrent per-camera jobs in
   *  JobsPanel by this id. */
  video_id: string | null;
  status: JobStatus;
  progress: number | null;
  message: string | null;
  error: string | null;
  /** True after the SPA POSTed /api/jobs/{id}/cancel for this job. The flag
   *  stays True on the terminal snapshot so the row can be labelled
   *  "Cancelled by user" instead of "Aborted". */
  cancel_requested: boolean;
  /** True once the user has dismissed this failure via
   *  /api/jobs/{id}/acknowledge (issue #73). Meaningful only on FAILED
   *  jobs; the JobsPanel badge counts failures with acknowledged=false
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
  bound: boolean;
  project_name: string | null;
  project_root: string | null;
  schema_version: number | null;
}

/** One entry from ``GET /api/user/recent-projects``. ``last_opened_at``
 *  is an ISO-8601 UTC timestamp the picker uses to sort. ``path`` is
 *  resolved server-side; we don't normalise it client-side. */
export interface RecentProject {
  path: string;
  name: string;
  last_opened_at: string;
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
  const resp = await fetch(path, {
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
    throw new ApiError(resp.status, detail, rawDetail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  getProject: () => request<MatchProject>("/api/project"),

  /** Fetch the canonical match-window analysis (per-stage windows +
   *  per-video classification). Drives the ingest screen's timeline; SPA
   *  carries no policy of its own beyond rendering. */
  getMatchAnalysis: () => request<MatchAnalysis>("/api/project/match-analysis"),

  listFolder: (path?: string, opts?: { probe?: boolean }) => {
    const params = new URLSearchParams();
    if (path) params.set("path", path);
    if (opts?.probe) params.set("probe", "true");
    const qs = params.toString();
    return request<FsListing>(`/api/fs/list${qs ? `?${qs}` : ""}`);
  },

  probeFile: (path: string) =>
    request<FsProbeResponse>(`/api/fs/probe?path=${encodeURIComponent(path)}`),

  removeVideo: (videoPath: string, resetAudit = false) =>
    request<RemoveVideoResponse>("/api/videos/remove", {
      method: "POST",
      json: { video_path: videoPath, reset_audit: resetAudit },
    }),

  getCleanupPlan: (categories: CleanupCategory[]) => {
    const qs = categories.length
      ? `?categories=${encodeURIComponent(categories.join(","))}`
      : "";
    return request<CleanupPlan>(`/api/project/cleanup/plan${qs}`);
  },

  applyCleanup: (categories: CleanupCategory[]) =>
    request<CleanupApplyResponse>("/api/project/cleanup", {
      method: "POST",
      json: { categories },
    }),

  importScoreboard: (data: unknown, overwrite = false) =>
    request<MatchProject>("/api/scoreboard/import", {
      method: "POST",
      json: { data, overwrite },
    }),

  // SSI Scoreboard v1 wiring (#50). The UI calls these without picking the
  // backend implementation -- the server resolves Local vs Http per request
  // based on whether ``<project>/scoreboard/match.json`` exists.

  getScoreboardSource: () => request<ScoreboardSource>("/api/scoreboard/source"),

  /** Drop-and-populate: the SPA reads the file as text, parses JSON, and
   *  posts it here. Backend writes to ``<project>/scoreboard/match.json``
   *  and uses LocalJsonScoreboard so subsequent requests stay fully offline. */
  uploadScoreboard: (data: unknown, overwrite = false) =>
    request<MatchProject>("/api/scoreboard/upload", {
      method: "POST",
      json: { data, overwrite },
    }),

  /** Free-text match search. In offline mode this hits the dropped match
   *  only; in online mode it goes to ``GET /api/v1/events?q=``. */
  searchScoreboardMatches: (q: string) =>
    request<ScoreboardMatchRef[]>(
      `/api/scoreboard/search?q=${encodeURIComponent(q)}`,
    ),

  /** Cache-first full match fetch -> populate project. */
  fetchScoreboardMatch: (
    contentType: number,
    matchId: number,
    overwrite = false,
  ) =>
    request<MatchProject>("/api/scoreboard/fetch", {
      method: "POST",
      json: { content_type: contentType, match_id: matchId, overwrite },
    }),

  /** Resolve the current source's ``MatchData``. The SPA uses this to map
   *  a picked ``shooterId`` to a per-match ``competitor_id`` before
   *  pinning. 404 when the project has no match loaded. */
  getScoreboardMatchData: () =>
    request<ScoreboardMatchData>("/api/scoreboard/match-data"),

  /** Find shooters by name. Offline mode searches this match's competitor
   *  list only; online mode hits the live shooter index. */
  searchScoreboardShooters: (q: string) =>
    request<ScoreboardShooterRef[]>(
      `/api/scoreboard/shooter/search?q=${encodeURIComponent(q)}`,
    ),

  /** Pin (shooter, competitor) and merge stage times. The competitor id is
   *  resolved client-side from ``getScoreboardMatchData``; the server
   *  refuses to guess. The response carries the updated project plus a
   *  ``stage_times_merged`` count for the SPA to confirm. */
  selectScoreboardShooter: (shooterId: number, competitorId: number) =>
    request<MatchProject & { stage_times_merged: number }>(
      "/api/scoreboard/select-shooter",
      {
        method: "POST",
        json: { shooter_id: shooterId, competitor_id: competitorId },
      },
    ),

  /** Re-pull and re-merge stage times for the pinned competitor. Used for
   *  in-progress matches where new scorecards land while the user is
   *  ingesting; clears the project-local cache for every cid in the match. */
  refreshScoreboardTimes: () =>
    request<MatchProject & { stage_times_merged: number }>(
      "/api/scoreboard/refresh-times",
      { method: "POST" },
    ),


  createPlaceholderStages: (req: PlaceholderStagesRequest) =>
    request<MatchProject>("/api/project/placeholder-stages", {
      method: "POST",
      json: req,
    }),

  scanVideos: (sourceDir: string, autoAssignPrimary = true) =>
    request<ScanResponse>("/api/videos/scan", {
      method: "POST",
      json: { source_dir: sourceDir, auto_assign_primary: autoAssignPrimary },
    }),

  scanFiles: (sourcePaths: string[], autoAssignPrimary = true) =>
    request<ScanResponse>("/api/videos/scan", {
      method: "POST",
      json: { source_paths: sourcePaths, auto_assign_primary: autoAssignPrimary },
    }),

  updateSettings: (patch: ProjectSettingsPatch) =>
    request<MatchProject>("/api/project/settings", {
      method: "POST",
      json: patch,
    }),

  getAutomation: () =>
    request<ResolvedAutomationResponse>("/api/automation"),

  /** Project-level work queue: beeps the auto-trust gate (#219) didn't
   *  clear -- either missing (auto-detect found nothing) or below the
   *  ``beep_low_confidence_threshold``. The HITL panel polls this on
   *  the Ingest page; the MCP wrapper exposes it as a resource so an
   *  agent can drive the picks. */
  getHitlQueue: () => request<HitlQueueResponse>("/api/hitl-queue"),

  dismissNudge: (stageNumber: number, dismissed: boolean) =>
    request<MatchProject>("/api/project/nudges/dismiss", {
      method: "POST",
      json: { stage_number: stageNumber, dismissed },
    }),

  moveAssignment: (
    videoPath: string,
    toStageNumber: number | null,
    role: VideoRole = "secondary",
  ) =>
    request<MatchProject>("/api/assignments/move", {
      method: "POST",
      json: {
        video_path: videoPath,
        to_stage_number: toStageNumber,
        role,
      },
    }),

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
    stageNumber: number,
    videoId: string,
    mount: CameraMount | null,
  ) =>
    request<MatchProject>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/camera-mount`,
      {
        method: "PATCH",
        json: { mount },
      },
    ),

  swapPrimary: (videoPath: string, stageNumber: number, confirm = false) =>
    request<MatchProject>("/api/assignments/swap-primary", {
      method: "POST",
      json: {
        video_path: videoPath,
        stage_number: stageNumber,
        confirm,
      },
    }),

  /** Toggle the ``skipped`` flag on a stage. Skipped stages don't block
   *  the "next step" gate even when they have no videos / no primary. */
  setStageSkipped: (stageNumber: number, skipped: boolean) =>
    request<MatchProject>(`/api/stages/${stageNumber}/skip`, {
      method: "POST",
      json: { skipped },
    }),

  /** Submit a beep-detection job for the stage's primary. Returns a Job
   *  snapshot; poll via {@link api.pollJob} (or read /api/jobs/{id})
   *  until the status flips out of "pending"/"running". On success the
   *  SPA should re-fetch /api/project to pick up the new beep_time +
   *  processed.trim. Backed by the per-video pipeline; identical to
   *  ``detectBeepForVideo(stage, primary.video_id)``. */
  detectBeep: (stageNumber: number, force = false) =>
    request<Job>(
      `/api/stages/${stageNumber}/detect-beep${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),

  /** Submit a beep-detection job for a specific video on a stage.
   *  Generic over role: each camera gets its own beep_time, its own
   *  audit-mode trim, and its own dedupe slot in the registry so
   *  primary + Cam 2 + Cam 3 can run in parallel. Shot detection
   *  auto-chains for primary results only. */
  detectBeepForVideo: (stageNumber: number, videoId: string, force = false) =>
    request<Job>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/detect-beep${
        force ? "?force=true" : ""
      }`,
      { method: "POST" },
    ),

  overrideBeep: (stageNumber: number, beepTime: number | null) =>
    request<MatchProject>(`/api/stages/${stageNumber}/beep`, {
      method: "POST",
      json: { beep_time: beepTime },
    }),

  /** Manually set or clear ``video``'s beep timestamp. ``beepTime=null``
   *  clears back to "no beep yet"; otherwise the value (>= 0) is taken
   *  as authoritative with ``beep_source="manual"``. Same auto-trim
   *  chain as the legacy primary endpoint, just keyed per video. */
  overrideBeepForVideo: (
    stageNumber: number,
    videoId: string,
    beepTime: number | null,
  ) =>
    request<MatchProject>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep`,
      { method: "POST", json: { beep_time: beepTime } },
    ),

  /** Promote one of the ranked auto-detected candidates as authoritative.
   *  ``time`` is matched against ``primary.beep_candidates`` within 1 ms,
   *  so the SPA can hold a slightly stale snapshot without breaking the
   *  click. The server keeps the candidate list intact so the user can
   *  switch again without re-running detection, and re-fires the trim job. */
  selectBeepCandidate: (stageNumber: number, time: number) =>
    request<MatchProject>(`/api/stages/${stageNumber}/beep/select`, {
      method: "POST",
      json: { time },
    }),

  /** Per-video candidate select. Same matching semantics as the primary
   *  endpoint (1 ms epsilon) but targets the video carrying the candidate
   *  list, so secondaries can pick from their own ranked alternatives. */
  selectBeepCandidateForVideo: (
    stageNumber: number,
    videoId: string,
    time: number,
  ) =>
    request<MatchProject>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/select`,
      { method: "POST", json: { time } },
    ),

  /** Snap a user-placed beep marker to the strongest tone in a tight
   *  window around the hint. Stateless -- the caller decides whether to
   *  accept the proposal as a manual override. 404 means "no run met
   *  duration / amplitude in that window"; widen ``windowS`` or move
   *  the marker. */
  snapBeepForVideo: (
    stageNumber: number,
    videoId: string,
    hintTime: number,
    windowS: number = 0.5,
  ) =>
    request<BeepSnapResult>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/snap`,
      { method: "POST", json: { hint_time: hintTime, window_s: windowS } },
    ),

  /** Flip ``beep_reviewed`` on a single video (issue #71). Pure UI-state
   *  endpoint -- no detection or trim chain runs. Setting ``true``
   *  requires ``beep_time`` to already be set; the server returns 400
   *  otherwise. */
  setBeepReviewed: (stageNumber: number, videoId: string, reviewed: boolean) =>
    request<MatchProject>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/review`,
      { method: "POST", json: { reviewed } },
    ),

  /** Submit an audit-mode short-GOP trim job. Returns a Job snapshot;
   *  idempotent on the worker side -- when the cached MP4 is fresh the
   *  job completes near-instantly without re-encoding. */
  trimStage: (stageNumber: number) =>
    request<Job>(`/api/stages/${stageNumber}/trim`, { method: "POST" }),

  /** Per-video audit-mode trim. Mirrors ``trimStage`` for primaries but
   *  targets one specific camera, so multi-cam ingest can refresh a
   *  single secondary's scrub clip without retriggering the primary. */
  trimVideo: (stageNumber: number, videoId: string) =>
    request<Job>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/trim`,
      { method: "POST" },
    ),

  /** Submit a shot-detection job for the stage's audit clip. The job
   *  populates _candidates_pending_audit in the audit JSON; the audit
   *  screen renders markers from there. Auto-triggered after trim;
   *  this endpoint is for manual retrigger.
   *  Pass ``reset: true`` to wipe ``shots[]`` first, discarding the user's
   *  keep / reject decisions so the next pass starts fresh. */
  detectShots: (stageNumber: number, opts: { reset?: boolean } = {}) => {
    const qs = opts.reset ? "?reset=true" : "";
    return request<Job>(`/api/stages/${stageNumber}/shot-detect${qs}`, { method: "POST" });
  },

  /** Submit shot detection on every eligible stage. A stage is eligible
   *  when it has a primary with confirmed beep + non-zero time_seconds.
   *  Returns the list of submitted (or already-active) jobs plus a
   *  ``skipped`` array describing why ineligible stages were left alone. */
  detectShotsAll: (opts: { reset?: boolean } = {}) => {
    const qs = opts.reset ? "?reset=true" : "";
    return request<{
      jobs: Job[];
      skipped: { stage_number: number; reason: string }[];
    }>(`/api/stages/shot-detect${qs}`, { method: "POST" });
  },

  /** Server-side feature flags (fetched once on app mount). Today this
   *  surfaces the ``lab`` flag so the SPA can hide the Lab nav entry
   *  unless ``splitsmith ui --lab`` was passed. */
  getServerFeatures: () => request<{ lab: boolean }>("/api/server/features"),

  /** Server health + bind state. The picker route polls this on mount
   *  to decide whether the user landed in unbound mode (boot with no
   *  ``--project``) or whether a project is already open. */
  getHealth: () => request<ServerHealth>("/api/health"),

  /** Recent-projects list, most-recent first. Drives the picker. */
  getRecentProjects: () =>
    request<{ projects: RecentProject[] }>("/api/user/recent-projects").then(
      (r) => r.projects,
    ),

  /** Drop one entry from the recent list (forget). Returns the updated
   *  list so the picker can re-render without a follow-up GET. */
  forgetRecentProject: (path: string) =>
    request<{ removed: boolean; projects: RecentProject[] }>(
      "/api/user/recent-projects/forget",
      { method: "POST", json: { path } },
    ),

  /** Switch the in-memory project. The picker calls this when the user
   *  selects an entry; the server updates ``last_opened_at`` and binds.
   */
  bindProject: (path: string, name?: string) =>
    request<ServerHealth>("/api/user/recent-projects/bind", {
      method: "POST",
      json: { path, name },
    }),

  /** Drop the bound project so the SPA returns to the picker (Cmd+P
   *  "Switch project..." path). */
  unbindProject: () =>
    request<ServerHealth>("/api/user/recent-projects/unbind", { method: "POST" }),

  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (jobId: string) => request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),

  /** Request cooperative cancellation. Idempotent: a finished job is returned
   *  as-is. For a running trim job the server terminates the underlying
   *  ffmpeg subprocess so the cancel takes effect immediately. */
  cancelJob: (jobId: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  /** Mark a single failed job as seen (issue #73). The badge stops
   *  counting it and the registry rolls it off ahead of unacknowledged
   *  failures. No-op for non-failed / already-acknowledged jobs. */
  acknowledgeJob: (jobId: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(jobId)}/acknowledge`, { method: "POST" }),

  /** Bulk-dismiss every currently-unacknowledged failure. Returns the
   *  jobs that actually flipped to acknowledged. */
  acknowledgeAllFailures: () =>
    request<Job[]>(`/api/jobs/acknowledge-failures`, { method: "POST" }),

  /** Poll a job until it leaves the running state. ``onUpdate`` fires on
   *  every snapshot (including the final one). Returns the terminal Job. */
  pollJob: async (
    jobId: string,
    onUpdate: (job: Job) => void,
    opts: { intervalMs?: number; timeoutMs?: number } = {},
  ): Promise<Job> => {
    const interval = opts.intervalMs ?? 750;
    const deadline = Date.now() + (opts.timeoutMs ?? 10 * 60 * 1000);
    while (true) {
      const job = await request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
      onUpdate(job);
      if (
        job.status === "succeeded" ||
        job.status === "failed" ||
        job.status === "cancelled"
      ) return job;
      if (Date.now() > deadline) {
        throw new Error(`Timed out waiting for job ${jobId}`);
      }
      await new Promise((r) => setTimeout(r, interval));
    }
  },

  stageAudioUrl: (stageNumber: number) => `/api/stages/${stageNumber}/audio`,

  /** Per-video WAV URL. Primary forwards to the legacy stage audio
   *  endpoint (trimmed audit clip preferred); secondary serves the full
   *  per-cam WAV so the picker has the whole clip to scrub. */
  videoAudioUrl: (stageNumber: number, videoId: string) =>
    `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/audio`,

  /** URL for a tiny MP4 around a beep timestamp (#27, #22). ``t`` is
   *  passed to the server (which centres the clip there) AND ms-rounded
   *  into the cache key, so each distinct ``t`` gets its own MP4. The
   *  candidate picker uses this with arbitrary candidate times; the
   *  default flow passes ``primary.beep_time``. */
  stageBeepPreviewUrl: (stageNumber: number, beepTime: number) =>
    `/api/stages/${stageNumber}/beep-preview?t=${beepTime.toFixed(3)}`,

  /** Per-video beep preview URL. Same caching semantics as the primary
   *  endpoint (cached on source mtime/size + center time + duration). */
  videoBeepPreviewUrl: (stageNumber: number, videoId: string, beepTime: number) =>
    `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-preview?t=${beepTime.toFixed(3)}`,

  videoStreamUrl: (videoPath: string) =>
    `/api/videos/stream?path=${encodeURIComponent(videoPath)}`,

  getStagePeaks: (stageNumber: number, bins = 1200) =>
    request<PeaksResult>(`/api/stages/${stageNumber}/peaks?bins=${bins}`),

  /** Per-video peaks. Same payload shape as the stage endpoint so the
   *  waveform picker takes the same code path for every role. */
  getVideoPeaks: (stageNumber: number, videoId: string, bins = 1200) =>
    request<PeaksResult>(
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/peaks?bins=${bins}`,
    ),

  /** Returns the saved audit JSON for a stage, or null when none exists yet. */
  getStageAudit: async (stageNumber: number): Promise<StageAudit | null> => {
    try {
      return await request<StageAudit>(`/api/stages/${stageNumber}/audit`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  },

  /** Atomically write the stage's audit JSON. The server keeps the prior
   *  version as ``stage<N>.json.bak`` so a bad save can be recovered. */
  saveStageAudit: (stageNumber: number, payload: StageAudit) =>
    request<StageAudit>(`/api/stages/${stageNumber}/audit`, {
      method: "PUT",
      json: payload,
    }),

  /** Coach view: per-shot interval class + flags + notes (#161). The
   *  GET is read-only; ``stale=true`` means the rule disagrees with the
   *  stored class and the user can accept the recompute via reclassify. */
  getStageCoach: async (stageNumber: number): Promise<CoachStageResponse | null> => {
    try {
      return await request<CoachStageResponse>(`/api/stages/${stageNumber}/coach`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  },

  reclassifyStageCoach: (stageNumber: number) =>
    request<CoachStageResponse>(`/api/stages/${stageNumber}/coach/reclassify`, {
      method: "POST",
    }),

  patchStageShotCoach: (
    stageNumber: number,
    shotNumber: number,
    patch: CoachShotPatch,
  ) =>
    request<CoachStageResponse>(
      `/api/stages/${stageNumber}/shots/${shotNumber}/coach`,
      { method: "PATCH", json: patch },
    ),

  /** Per-stage histograms + summary stats for the Coach distributions
   *  panel (#163). Empty classes still appear with count=0 so the UI
   *  can render an empty histogram without a special case. */
  getStageCoachDistributions: (stageNumber: number) =>
    request<CoachStageDistributions>(
      `/api/stages/${stageNumber}/coach/distributions`,
    ),

  /** Match-level histograms aggregated across every stage with an audit
   *  JSON. Stages without an audit are silently skipped server-side. */
  getMatchCoachDistributions: () =>
    request<CoachMatchDistributions>("/api/coach/distributions"),

  /** Structured anomalies for the *saved* audit JSON (issue #42).
   *
   *  The audit screen does its own live recompute (see ``lib/anomalies``)
   *  so the panel updates without a network round-trip on every keep /
   *  reject. This endpoint exists for external consumers + integration
   *  tests that want the same flags ``report.txt`` will produce. */
  getStageAnomalies: (stageNumber: number) =>
    request<{ anomalies: import("./anomalies").Anomaly[] }>(
      `/api/stages/${stageNumber}/anomalies`,
    ),

  /** Match-overview payload for the Analysis & Export screen. */
  getExportOverview: () => request<ExportOverview>("/api/exports/overview"),

  /** Submit a stage export job. Returns a Job snapshot; poll
   *  ``/api/jobs/{id}`` (or {@link api.pollJob}) until it leaves the
   *  running state, then re-fetch the export overview to see updated
   *  paths + ``last_export_at``. Idempotent on the worker side: the
   *  registry dedupes by (kind, stage_number) so double-clicking
   *  Generate returns the in-flight job instead of stacking. */
  exportStage: (stageNumber: number, opts: ExportStageRequestPayload = {}) =>
    request<Job>(`/api/stages/${stageNumber}/export`, {
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
  exportMatch: (payload: MatchExportRequestPayload) =>
    request<Job>("/api/match/export", {
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
  revealVideo: (videoPath: string) =>
    request<{ revealed: string }>("/api/videos/reveal", {
      method: "POST",
      json: { path: videoPath },
    }),

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

  promoteSecondary: (
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
      `/api/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/promote-secondary`,
      { method: "POST", json: payload },
    ),

  /** Anchor a project video against an arbitrary fixture (issue #149
   *  follow-up). Lab-only. Used when the headcam ground truth lives
   *  as a fixture in ``tests/fixtures/`` and the project has a phone-cam
   *  primary that should be aligned against it. */
  promoteAgainstFixture: (
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
      `/api/lab/projects/${stageNumber}/videos/${encodeURIComponent(videoId)}/promote-against-fixture`,
      { method: "POST", json: payload },
    ),
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
  voter_d_threshold_override: number | null;
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
  vote_d: number;
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
  voter_d_threshold: number;
  tolerance_ms: number;
}

export interface LabEvalRun {
  config: LabEvalConfig;
  summary: LabRunSummary;
  universe: LabEvalUniverse;
  config_hash: string;
  built_at: string;
}

export { ApiError };
