import type { Feature } from "@/lib/access";
import type { LogoSpot } from "@/lib/logoPlan";
import type { components } from "./api/types.gen";
import { stagesApi } from "./api/stages";
import { shootersApi } from "./api/shooters";
import { matchApi } from "./api/match";
import { meApi } from "./api/me";
import { labApi } from "./api/lab";
import { miscApi } from "./api/misc";
import { ApiError, scopeRequestPath } from "./api/client";
/** The server's own schemas (``pnpm gen:api`` over ``openapi.json``); a hand-written
 *  type below is an alias into this wherever the two shapes already agree. */
type Schema = components["schemas"];

export { ApiError, HOSTED_ACCOUNT_CHANGED_EVENT, currentMatchIdFromLocation, currentShareTokenFromLocation, isUnauthorized, notifyHostedAccountChanged, scopeRequestPath } from "./api/client";
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
export type BeepCandidate = Schema["BeepCandidate"];

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

/** ``GET /api/fixture/review-signals`` (spec 2026-10-14): where the Audit
 *  page's fixture review stops, and what a snap's sign-off counts against. */
export interface FixtureReviewSignals {
  moves: Array<{ time: number; suggested: number; move_ms: number }>;
  edges: Array<{ time: number; displacement_ms: number }>;
  unmarked: Array<{ time: number; confidence: number | null }>;
  /** A shot on a louder echo, with the real blast's foot ``earlier``. */
  echoes: Array<{ time: number; earlier: number }>;
  reference: { kind: "snap"; times: number[] } | null;
  rounds: number | null;
}

export type StageVideo = Schema["StageVideoView"];

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
export type StageStatus = Schema["StageStatus"];

/** Round count + target breakdown for a stage, mirrored from the backend
 *  ``config.StageRounds``. Sourced from SSI Scoreboard on import; any field
 *  may be null for manually-created stages or older imports. */
export interface StageRounds {
  expected: number | null;
  paper_targets: number | null;
  steel_targets: number | null;
}

/** Per-shooter, per-stage scoring pulled from the SSI Scoreboard and
 *  persisted on the project so hosted share viewers (who cannot fetch
 *  live) still see scores. Null until a scoreboard sync populates it. */
export type StageScorecard = Schema["StageScorecard"];

export type StageEntry = Schema["StageEntryView"];

/** Per-stage splits summary on the project payload. The split rule is
 *  the server's ``coach.statistic_splits`` (mirrored by ``statisticSplits``
 *  in ``lib/splits``); ``avg_split`` and ``fastest_split`` are null when
 *  the stage has no split-classed interval. */
export type StageFigures = Schema["StageFiguresView"];

/** A shooter's identity (#1243): accent, logo file name (content-named,
 *  under the shooter's ``identity/``), club line. ``null`` means "the
 *  Look's default", never blank. */
export type ShooterIdentity = Schema["ShooterIdentity"];

export type MatchProject = Schema["ProjectView"];

/** GET /api/scoreboard/source response. ``mode === "local"`` means the
 *  offline JSON path will serve every scoreboard request -- no network is
 *  used. ``http_token_set`` reflects whether ``SPLITSMITH_SSI_TOKEN`` is
 *  set on the server process. Informational: v1 reads are anonymous, a
 *  token only raises the rate limit. */
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

/** Your brand (spec 2026-10-08): ``GET /api/me/profile``. */
export interface AccountProfileView {
  brand: { logo: string | null; line: string };
}

/** One shooter in your shooter book, keyed by SSI shooter id. */
export interface ShooterBookEntryView {
  shooter_id: number;
  label: string | null;
  identity: ShooterIdentity;
  updated_at: string;
}

/** Where a shooter's look comes from: ``GET /api/shooters/{slug}/identity``. */
export type IdentitySource = "match" | "book" | "none";

export interface ShooterIdentityView {
  source: IdentitySource;
  identity: ShooterIdentity;
  shooter_id: number | null;
  /** The book holds a look for this shooter ("Use shooter book" has one). */
  book_entry: boolean;
  /** This server keeps a shooter book (hosted does not yet). */
  book_available: boolean;
}

/** ``scope`` on an identity edit: ``book`` also saves it to the shooter
 *  book, ``match`` keeps it to this match. */
export type IdentityScope = "book" | "match";

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

/** A name-based proposal binding one local shooter (by ``slug``) to a
 *  scoreboard competitor, returned by the connect-match endpoint. A null
 *  ``competitor_id`` means no confident match (leave unlinked); ``ambiguous``
 *  flags a near-tie the operator should double-check before applying. */
export interface LinkProposal {
  slug: string;
  competitor_id: number | null;
  shooter_id: number | null;
  competitor_name: string | null;
  score: number;
  ambiguous: boolean;
}

/** Response from POST /api/match/scoreboard/connect. */
export interface ConnectMatchResponse {
  stage_mismatch: boolean;
  local_stage_count: number;
  scoreboard_stage_count: number;
  proposals: LinkProposal[];
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

/** One stage as a property of the MATCH, mirrors
 *  ``match_model.MatchStageDefinition`` (#521).
 *
 *  Not interchangeable with ``StageEntry``, which is one shooter's copy
 *  plus their own time / videos / audit. The two documents diverge and
 *  stay diverged: linking a scoreboard rewrites ``Match.stages`` with the
 *  scoreboard's names and ``stage_rounds`` while leaving every shooter
 *  project on its placeholder names, and no later sync closes the gap.
 *  ``PUT /api/match/stages`` diffs a submission against ``Match.stages``,
 *  so the stage editor must read and submit THIS shape -- feeding it a
 *  shooter's list on a diverged match reports every untouched stage as
 *  renamed and wipes ``stage_rounds`` off the match. */
export interface MatchStageDefinition {
  stage_number: number;
  stage_name: string;
  stage_rounds: StageRounds | null;
  placeholder: boolean;
  /** ``standard`` (holstered and loaded) or ``non_standard`` (#1528).
   *  Optional: an older server omits it, which reads as standard. */
  start?: string;
}

/** Response from GET /api/match/stages, mirrors ``MatchStagesResponse``. */
export interface MatchStagesResponse {
  stages: MatchStageDefinition[];
}

/** One row of the SPA's stage-list editor, sent as the full desired list
 *  to ``editMatchStages`` (#521). Mirrors ``stage_edit.SubmittedStage``. */
export interface StageEditRow {
  /** null marks a row the user added; the server allocates the number. */
  stage_number: number | null;
  stage_name: string;
  stage_rounds?: StageRounds | null;
  /** Omitted keeps the stage's start (#1528). */
  start?: "standard" | "non_standard";
}

/** What a stage-list edit did to one shooter, mirrors
 *  ``stage_edit.ShooterStageEditResult``. ``error`` covers two outcomes
 *  that are not equally bad -- read ``saved``, not the message, to tell
 *  them apart: ``saved=true`` with an ``error`` means the shooter's
 *  project WAS written and only a cleanup step (video release, audit
 *  delete, or the artifact purge raising) failed; ``saved=false`` means the
 *  project doc was never written and this shooter's stage list is unchanged
 *  on disk. Individual files or storage objects the purge could not delete
 *  are best-effort and land in ``StageEditSummary.errors`` alone, so a
 *  shooter with orphaned cache files can still report ``error: null``. */
export type ShooterStageEditResult = Schema["ShooterStageEditResult"];

/** Response from PUT /api/match/stages, mirrors ``stage_edit.StageEditSummary``.
 *  ``jobs_cancelled`` is always 0 -- cancellation was deliberately dropped
 *  (#645) but the field remains in the wire shape. A 200 with a non-empty
 *  ``errors`` means the edit committed but something did not get cleaned up. */
export type StageEditSummary = Schema["StageEditSummary"];

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
export type AutomationOverride = Schema["AutomationOverride"];

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

export type ScanResponse = Schema["ScanResponse"];

/** Footage sort (spec 2026-10-01): one folder sorted across the
 *  match's shooters. Shapes mirror ``ui/footage_sort_api.py``. */
export type SortCameraClock = "trusted" | "fitted" | "anchored" | "needs_anchor" | "no_timestamps";
export type SortConfidence = "high" | "medium" | "needs_you" | "skipped";
export type SortIssue =
  | "ambiguous"
  | "conflict"
  | "no_candidate"
  | "no_timestamp"
  | "needs_anchor"
  | "outside_match";

export type SortCamera = Schema["SortCamera"];

export interface SortClipProposal {
  clip_id: string;
  camera_key: string;
  shooter: string | null;
  stage: number | null;
  confidence: SortConfidence;
  run_id: string | null;
  role: "primary" | "secondary" | null;
  decided_by: "engine" | "user";
  reason: {
    scorecard_at: string | null;
    lead_seconds: number | null;
    offset_seconds: number;
    issue: SortIssue | null;
    rival: string | null;
    rival_stage: number | null;
    run_size: number;
  };
}

export interface SortClipView {
  clip_id: string;
  index: number;
  folder: string;
  filename: string;
  start: string | null;
  duration: number | null;
  model: string | null;
  imported_by: string | null;
  /** The shooter whose unassigned list holds the file; import moves it when
   *  the proposal names someone else. */
  unassigned_in: string | null;
  thumbnail: boolean;
  /** A hover-scrub strip exists (built after the scan). */
  strip: boolean;
  checked: boolean;
  proposal: SortClipProposal;
}

export interface SortAnchor {
  clip_id: string;
  shooter: string;
  stage: number;
}

export interface SortOverride {
  clip_id: string;
  shooter?: string | null;
  stage?: number | null;
  skip: boolean;
}

export interface SortDecisions {
  anchors: SortAnchor[];
  overrides: SortOverride[];
  checked: Record<string, boolean>;
}

export type SortView = Schema["SortView"];
export type SortShooterRef = Schema["ShooterRef"];
export type SortReference = Schema["SortReference"];
/** GET/PUT /api/shooters/{slug}/stage-details (spec 2026-10-22). */
export type StageDetailsView = Schema["StageDetailsView"];
export type StageDetailsStage = Schema["StageDetailsStage"];
export type StageDetailsRow = Schema["StageDetailsRow"];
export type StageDetailsRequest = Schema["StageDetailsRequest"];

/** An unfinished sort, offered on Footage to continue. */
export type SortSummary = Schema["SortSummary"];

export interface SortImportResult {
  imported: { clip_id: string; shooter: string; stage: number; role: string; path: string }[];
  not_imported: Record<string, string>;
  report: string;
  /** Clips still wanting a decision; while above 0 the sort stays open. */
  remaining: number;
}

/** Filesystem state of a registered ``raw/<name>`` symlink. ``ok`` =
 *  resolves to a present file; ``broken`` = symlink with missing
 *  target; ``missing_link`` = the symlink itself is gone;
 *  ``not_a_symlink`` = a regular file (link_mode="copy") that the
 *  relink flow can't help with. */
export type LinkStatus = "ok" | "broken" | "missing_link" | "not_a_symlink";

export type LinkStatusEntry = Schema["LinkStatusEntry"];

export type LinkStatusResponse = Schema["LinkStatusResponse"];

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

export type RelinkScanResponse = Schema["RelinkScanResponse"];

export type RelinkAppliedEntry = Schema["RelinkAppliedEntry"];

export type RelinkApplyResponse = Schema["RelinkApplyResponse"];

export type FsEntryKind = "dir" | "video" | "file";

export type FsEntry = Schema["FsEntry"];

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
  /** Whether a bare lossless trim can be cut: a beep plus a stage time, no
   *  audit and no shots (#613). Strictly weaker than ``ready_to_export``;
   *  the trims-only export mode reads this instead of re-deriving the rule
   *  here, which is what let the TS copy drift from the server's. */
  ready_to_trim: boolean;
  /** The bundle gate for a stage without audited shots: the trim rule plus
   *  a reviewed beep. Between ``ready_to_trim`` and ``ready_to_export``. */
  ready_to_export_bare: boolean;
  source_reachable: boolean | null;
  /** Multi-cam roster (issue #54). One entry per secondary on the stage,
   *  including cams without a beep / unreachable cams (the SPA renders
   *  those as disabled rows). Empty when the stage is single-cam. */
  secondaries: SecondaryExportStatus[];
}

/** One match-level deliverable in the project's ``exports/``.
 *
 *  Separate from {@link StageExportStatus} because it answers a listing
 *  question ("what can I download now"), not a status one. Persistent:
 *  it comes from the exports directory (object storage in hosted mode),
 *  so unlike the export job's ``result`` it survives a reload. */
export interface MatchExportFile {
  filename: string;
  last_export_at: string | null;
}

export interface ExportOverview {
  stages: StageExportStatus[];
  match_exports: MatchExportFile[];
}

/** One artefact a recorded export run produced. ``filename`` is a
 *  basename under the shooter's ``exports/`` dir -- the same key
 *  {@link api.exportFileUrl} takes. */
export interface ExportArtifact {
  filename: string;
  kind: string;
  /** Whether the bytes are still there, as of this response.
   *
   *  Derived per request by the server from the same presence source the
   *  export overview reads; it is not part of the stored record, which
   *  says what a run produced and never changes. Cleanup deletes export
   *  files and leaves the history alone, so ``false`` is a normal state
   *  and not an error -- render the name, not a link. */
  available: boolean;
}

/** One export invocation, as recorded at export time (#629).
 *
 *  ``formats`` is what the user *asked* for; ``artifacts`` is what got
 *  written. Both are kept so "asked for an overlay, got none" is visible
 *  in the history rather than inferred from an absence.
 *
 *  ``duration_seconds`` is wall-clock time for the run, not the length of
 *  any rendered timeline. */
export interface ExportRun {
  run_id: string;
  kind: "stage" | "match";
  finished_at: string;
  duration_seconds: number;
  stage_numbers: number[];
  formats: string[];
  anomaly_count: number;
  artifacts: ExportArtifact[];
  /** The sidecar's upload record, read per request; null when the run
   *  has no ``-youtube.json`` artifact or it was never uploaded. */
  youtube?: ExportRunYouTube | null;
}

export type YouTubePrivacy = "unlisted" | "private" | "public";

/** What a direct YouTube upload left in the run's sidecar. */
export interface ExportRunYouTube {
  video_id: string;
  url: string;
  privacy: string;
  uploaded_at: string;
  playlist_title?: string | null;
  publish_at?: string | null;
}

/** GET /api/settings/youtube: the install's channel locally, the account's hosted. */
export type YouTubeSettings = Schema["YouTubeSettings"];

/** GET /api/settings/youtube/connect/status. */
export interface YouTubeConnectStatus {
  state: "idle" | "pending" | "connected" | "failed";
  channel_title: string | null;
  error: string | null;
}

export interface YouTubePlaylist {
  id: string;
  title: string;
}

export interface YouTubeConnectStart {
  auth_url: string;
  expires_at: string;
}

/** The convenience options an upload carries: a playlist by title
 *  (created if missing), a scheduled publish time as ISO 8601 (the video
 *  stays private until then), subscriber notification. */
export interface YouTubeUploadOptions {
  privacy: YouTubePrivacy;
  playlist: string | null;
  /** An existing playlist's id; wins over ``playlist``, which is then the title recorded. */
  playlist_id: string | null;
  publish_at: string | null;
  notify_subscribers: boolean;
}

export interface YouTubeUploadPayload extends YouTubeUploadOptions {
  filename: string;
  again: boolean;
}

/** Encoder for the alpha overlay MOV.
 *  - ``"auto"``: ``hevc-alpha`` on macOS w/ VideoToolbox, otherwise
 *    ``prores-4444``. Default; produces the smallest file the host can
 *    write without losing alpha.
 *  - ``"hevc-alpha"``: ~10-20x smaller than ProRes 4444 for sparse-text
 *    overlays. macOS only; FCP imports natively.
 *  - ``"prores-4444"``: cross-platform / archival. Largest files. */
export type OverlayCodec = "auto" | "hevc-alpha" | "prores-4444";

/** The recurring half of the Export form (spec 2026-09-15 s1). Mirrors
 *  ``export_presets.ExportPresetBody``; every field has a server default
 *  and unknown fields are dropped there, so the SPA never needs to
 *  migrate a stored body. */
/** Stage-to-stage transition kinds: ``"none"``, the two FCP effects the
 *  FCPXML emits natively, an ffmpeg xfade name from the server's list
 *  (``GET /api/looks`` ``transitions``, #1259) or a Look sting,
 *  ``sting:<name>`` (#1245). Open on purpose: the server owns the list and
 *  validates it; the gallery admits only what the catalog offers. */
export type TransitionKind = string;

/** The overlay style (template HUD, spec 2026-10-08) as every body that
 *  draws an overlay carries it: the match export, the preview, a preset.
 *  Unset is Classic with every toggle on; ``lib/overlayStyle`` sends the
 *  fields only when a style is chosen. */
export interface OverlayStyleBody {
  overlay_variant?: string;
  overlay_speed_colors?: boolean;
  overlay_class_labels?: boolean;
  overlay_landing?: boolean;
  overlay_reload_chip?: boolean;
  overlay_stage_bar?: boolean;
  overlay_position?: string | null;
}

export interface ExportPresetBody extends OverlayStyleBody {
  schema_version?: number;
  mode: "single" | "trims" | "compare" | "reel";
  output_format: "fcpxml" | "fcp7xml" | "mp4";
  overlay_codec: OverlayCodec;
  canvas: "uhd" | "hd";
  include_secondaries: boolean;
  pip_layout: "stacked" | "pip-corners";
  main_camera: string;
  inset_camera: string | null;
  inset_corner: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  inset_size: "small" | "medium" | "large";
  youtube_preset: boolean;
  padding_preset: "full" | "action" | "highlight" | "custom";
  head_pad_seconds: number;
  tail_pad_seconds: number;
  transition_kind: TransitionKind;
  transition_seconds: number;
  /** The Look and each card slot's template variant (#1246). */
  look: string;
  title_page_variant: string;
  stage_card_variant: string;
  closing_card_variant: string;
  /** The summaries' Look styles (spec 2026-10-16); absent on an older body. */
  summary_variant?: string;
  match_summary_variant?: string;
  title_page: boolean;
  title_page_seconds: number;
  title_division: boolean;
  closing_card: boolean;
  made_with: boolean;
  /** The Look's sounds in a rendered MP4; a preset saved before it has none: on. */
  look_sounds?: boolean;
  /** The colour grade (``lib/grades``) and its strength; absent is the Look's. */
  grade?: string;
  grade_strength?: number | null;
  /** A preset saved before the switch has none: on. */
  breakdown?: boolean;
  account_brand?: boolean;
  /** Where logos go beyond the cards; a preset saved before it has none. */
  logo_spots?: LogoSpot[];
  stage_card_style: "none" | "slate" | "lower-third";
  stage_card_seconds: number;
  summary_hold_seconds: number;
  match_summary: boolean;
  match_summary_seconds: number;
  overlay: boolean;
  grid_overlay: boolean;
  grid_hold_seconds: number;
  grid_free_cell: GridFreeCell;
  upload_after_render: boolean;
  upload_privacy: YouTubePrivacy;
  upload_playlist: string | null;
  upload_playlist_id: string | null;
  upload_notify: boolean;
}

export interface ExportPreset {
  preset_id: string;
  name: string;
  builtin: boolean;
  updated_at: string;
  body: ExportPresetBody;
}

/** One template variant of a Look slot as ``GET /api/looks`` lists it (#1246). */
export interface LookVariantInfo {
  name: string;
  /** ``/api/looks/<owner>/preview/<file>`` or null when no Look has a picture. */
  preview: string | null;
  /** An overlay style's positions, its default first; empty or absent for a
   *  style that places itself and every other slot. */
  positions?: string[];
}

/** One installed Look (#1246). ``slots`` has every slot name; a card slot
 *  lists ``default`` first; ``transition`` lists the stings. */
export interface LookInfo {
  name: string;
  label: string;
  source: "shipped" | "user";
  /** The caller may change or delete it (#1263): every user Look, never a shipped one. */
  editable?: boolean;
  accent_series: string[];
  preview: string | null;
  slots: Record<string, LookVariantInfo[]>;
  /** It carries sounds the export can mix into a rendered MP4. */
  sounds?: boolean;
  /** It grades the footage (``look.json``'s ``grade``). */
  grade?: boolean;
}

/** A colour grade the gallery offers (``GET /api/grades``). */
export interface GradeInfo {
  id: string;
  label: string;
  preview: string;
}

/** An RGB triple, as ``look.json`` stores a colour. */
export type Rgb = [number, number, number];

/** What an account edits of a Look without code (``look_store.StoredLookBody``, #1263). */
export interface StoredLookBody {
  label: string;
  base: string | null;
  colors: Record<string, Rgb>;
  accent_series: string[];
  /** Card slot -> the variant its ``default`` draws; absent is ``default``. */
  styles: Record<string, string>;
  /** Font role -> a bundled face id (#1272); absent is the role's default. */
  fonts?: Record<string, string>;
  /** Your brand on the title page and the closing card; absent or null is none. */
  brand?: { logo: string | null; line: string } | null;
}

/** One bundled face a Look may choose (#1272). */
export interface FontInfo {
  id: string;
  label: string;
  role: "display" | "mono";
  help: string;
  /** ``/api/looks/fonts/<id>``: the face's file, for a sample. */
  url: string;
}

/** A font file of the Look's own (#1272), desktop only. */
export type OwnFontInfo = Schema["OwnFontInfo"];

export interface StoredLook {
  name: string;
  updated_at: string;
  body: StoredLookBody;
}

/** One template of a Look as the template editor lists it (#1265). */
export type TemplateInfo = Schema["TemplateInfo"];

/** Unsaved template text for a slot and variant. */
export type TemplateEdit = Schema["TemplateEdit"];

/** One ``looks check`` finding. */
export interface CheckFinding {
  subject: string;
  level: "ok" | "warn" | "error";
  message: string;
}

/** One What's new entry (``data/whats_new.json``). */
export type WhatsNewEntry = Schema["WhatsNewEntry"];

/** A What's new entry's video link. */
export type WhatsNewVideo = Schema["WhatsNewVideo"];

/** ``GET /api/whats-new``: the entries and what this user has seen. */
export type WhatsNewPayload = Schema["WhatsNewPayload"];

/** One ffmpeg xfade family as the gallery shows it (#1259): a tile, its
 *  directions (the first is what picking the tile selects) and a looping
 *  preview. */
export interface TransitionFamilyInfo {
  id: string;
  label: string;
  help: string;
  preview: string | null;
  directions: { name: string; kind: string }[];
}

export type PreviewCard =
  | "frame"
  | "title"
  | "slate"
  | "lower-third"
  | "summary"
  | "match_summary"
  | "closing"
  | "overlay"
  | "sting";

/** Body of ``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15
 *  s3). The server ignores unknown fields, so the mapper output may ride
 *  along; only these move the picture. */
export interface ExportPreviewBody extends OverlayStyleBody {
  card: PreviewCard;
  stage_number: number;
  width?: number;
  title_info?: string | null;
  title_division?: boolean;
  /** "Made with splitsmith" on the closing card. Server default on. */
  made_with?: boolean;
  /** The export's colour grade and strength; the preview's frame takes it. */
  grade?: string;
  grade_strength?: number;
  /** "Use Breakdown": the stages' confirmed regions in the export. Server
   *  default on; off exports every stage as one nobody broke down. */
  breakdown?: boolean;
  /** Your brand from the You page on the cards. Server default on. */
  account_brand?: boolean;
  /** Where logos go beyond the cards. Server default: Polished. */
  logo_spots?: LogoSpot[];
  /** The export's stage selection (the match summary card only). */
  stage_numbers?: number[];
  head_pad_seconds?: number;
  tail_pad_seconds?: number;
  /** The bundle name, as the match export's ``project_name``. */
  project_name?: string | null;
  /** The Look and the card's template variant (#1246); a sting's name for ``sting``. */
  look?: string;
  variant?: string;
  /** The Look editor (#1264): an unsaved draft drawn in place of ``look``. */
  draft?: StoredLookBody;
  /** Seconds into the template instead of its poster. */
  at?: number;
  /** The template editor's unsaved text (#1265); local only. */
  templates?: TemplateEdit[];
  /** The Look editor's backdrop switch: the demo range scene instead of this stage's footage. */
  backdrop?: "footage" | "demo";
  /** An animated template as a looping WebP (#1249); a still stays a PNG. */
  motion?: boolean;
  /** A labelled placeholder in every logo spot no logo fills (previews only). */
  logo_placeholders?: boolean;
}

export interface ExportStageRequestPayload {
  write_trim?: boolean;
  write_csv?: boolean;
  write_fcpxml?: boolean;
  write_report?: boolean;
  /** Render the alpha overlay MOV (issue #45). Defaults off because it's
   *  the slowest writer; opt in per stage. */
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
  /** Issue #972. Also write the stage's result screen beside the
   *  overlay MOV: ``<base>_summary.png`` plus a held ProRes
   *  ``<base>_summary.mov`` an editor drops after the stage. Off by
   *  default. */
  write_summary_card?: boolean;
  /** Seconds the summary MOV holds for. Server default 3.0. */
  summary_hold_seconds?: number;
  /** "Use Breakdown": the stages' confirmed regions in the export. Server
   *  default on; off exports every stage as one nobody broke down. */
  breakdown?: boolean;
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
export interface MatchExportRequestPayload extends OverlayStyleBody {
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
   *  in a rotating corner (TR -> TL -> BR -> BL) at 25% scale.
   *  Superseded by the inset below; ``pip-corners`` maps to it. */
  pip_layout?: "stacked" | "pip-corners";
  /** The camera each stage shows: ``"default"`` (the shooter's saved
   *  camera), ``"primary"``, or a mount; resolved per stage, falling back
   *  to the primary. The sound is the main camera's. */
  main_camera?: string;
  /** A second camera in a corner, shown only when it is not the main one;
   *  null for none. */
  inset_camera?: string | null;
  inset_corner?: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  inset_size?: "small" | "medium" | "large";
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
  transition_kind?: TransitionKind;
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
   *  the export plus a per-shot ``.srt``, and chapters in the output
   *  itself: markers on the FCPXML timeline, chapter atoms in the
   *  MP4. */
  youtube_sidecar?: boolean;
  /** Text the sidecar's description opens with, above the chapter list. */
  description_lead?: string | null;
  /** Issue #204 layer 2. Encode the MP4 with YouTube's recommended
   *  H.264 profile / GOP / colour / audio params. Only meaningful
   *  when ``output_format == "mp4"``. */
  youtube_preset?: boolean;
  /** Chain a ``youtube_upload`` job onto the render. The server refuses
   *  it without ``youtube_sidecar`` and an mp4 output (422). */
  youtube_upload?: boolean;
  youtube_privacy?: YouTubePrivacy;
  youtube_playlist?: string | null;
  youtube_playlist_id?: string | null;
  youtube_publish_at?: string | null;
  youtube_notify_subscribers?: boolean;
  /** Issue #973. Open the rendered MP4 with a generated match title
   *  card (match name, date, shooter, plus ``title_info`` as a free-text
   *  line). Other renderers surface an "ignored" anomaly. */
  title_page?: boolean;
  title_info?: string | null;
  /** The shooter's scoreboard division ("Classic Major") on its own
   *  line under the name. Server default on. */
  title_division?: boolean;
  /** Seconds the title page (and the closing card) holds for. Server
   *  default 3.0. */
  title_page_duration_seconds?: number;
  /** Close the rendered MP4 with a generated card. */
  closing_card?: boolean;
  /** "Made with splitsmith" on the closing card. Server default on. */
  made_with?: boolean;
  /** The Look's sounds mixed into the rendered MP4. Server default on; a
   *  Look without sounds is untouched either way. */
  look_sounds?: boolean;
  /** The colour grade: ``look`` (server default), ``none``, ``preset:<id>``
   *  or ``user:<file>``; MP4 only. */
  grade?: string;
  /** 0..1; absent is the grade's own (the Look's, else full). */
  grade_strength?: number;
  /** "Use Breakdown": the stages' confirmed regions in the export. Server
   *  default on; off exports every stage as one nobody broke down. */
  breakdown?: boolean;
  /** Your brand from the You page on the cards. Server default on. */
  account_brand?: boolean;
  /** Where logos go beyond the cards. Server default: Polished. */
  logo_spots?: LogoSpot[];
  /** The Look (#1246): any installed Look name; the server default. */
  overlay_theme?: string;
  /** Per-slot template variants (#1246); unset means the server's ``card_variant`` knob. */
  title_page_variant?: string | null;
  stage_card_variant?: string | null;
  closing_card_variant?: string | null;
  /** The summaries' Look styles (spec 2026-10-16); unset is the built-in card. */
  summary_variant?: string | null;
  match_summary_variant?: string | null;
  /** Issue #972. Hold each stage's summary -- name, scoring, splits over
   *  the blurred last frame -- for this many seconds after its action in
   *  the rendered MP4. 0 (the server default) is off. */
  summary_hold_seconds?: number;
  /** The match summary card after the last stage, and its hold (MP4
   *  only). Server default off, 6 s. */
  match_summary?: boolean;
  match_summary_seconds?: number;
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
  transition_kind?: TransitionKind;
  transition_duration_seconds?: number;
  title_kind?: "none" | "slate" | "lower-third";
  title_duration_seconds?: number;
  intro_path?: string;
  outro_path?: string;
  /** Issues #973 / #972: the generated-card and summary knobs. */
  title_page?: boolean;
  title_info?: string;
  title_page_duration_seconds?: number;
  closing_card?: boolean;
  summary_hold_seconds?: number;
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

/** Request payload for POST /api/match/compare-export (phase 0), mirrors
 *  ``server.CompareGridRequest``. ``cameras`` keys match either a
 *  shooter's slug or its display name, mirroring ``compare export``'s
 *  ``--camera SHOOTER=VALUE`` flag. ``canvas_width`` / ``canvas_height``
 *  default to 4K server-side; the frame rate is never taken from the
 *  request -- it always derives from the audio-source shooter's footage. */
export type GridFreeCell = "blank" | "stage" | "splits" | "match" | "race";

export interface CompareGridRequestPayload {
  stage_numbers: number[];
  audio_from: string;
  /** #1244: one transition between every pair of stages, MP4 only. */
  transition_kind?: TransitionKind;
  transition_duration_seconds?: number;
  cameras?: Record<string, string>;
  canvas_width?: number;
  canvas_height?: number;
  output_name?: string;
  /** Issue #973. Generated cards on the rendered grid: a match title
   *  page (name, date, plus ``title_info``), a closing card, and a card
   *  per stage (``slate`` before it, ``lower-third`` over its head).
   *  All off by default. */
  title_page?: boolean;
  title_info?: string | null;
  /** One "Name · Division" line per shooter. Server default on. */
  title_division?: boolean;
  title_page_duration_seconds?: number;
  closing_card?: boolean;
  /** "Made with splitsmith" on the closing card. Server default on. */
  made_with?: boolean;
  /** "Use Breakdown": the stages' confirmed regions in the export. Server
   *  default on; off exports every stage as one nobody broke down. */
  breakdown?: boolean;
  /** Your brand from the You page on the cards. Server default on. */
  account_brand?: boolean;
  /** Where logos go beyond the cards. Server default: Polished. */
  logo_spots?: LogoSpot[];
  /** Every shooter's match figures in their own tile before the closing card. */
  match_summary?: boolean;
  match_summary_seconds?: number;
  stage_titles?: "none" | "slate" | "lower-third";
  title_duration_seconds?: number;
  /** Issue #705. The splits overlay (per-tile counter and split, the
   *  running clock) and the end-of-stage summary hold in seconds. The
   *  hold needs the overlay on; the server refuses the pair otherwise
   *  with a 400. */
  overlay?: boolean;
  /** The Look (#1246): any installed Look name. */
  overlay_theme?: string;
  /** Per-slot template variants (#1246); unset means the ``card_variant`` knob. */
  title_page_variant?: string | null;
  stage_card_variant?: string | null;
  closing_card_variant?: string | null;
  summary_hold_seconds?: number;
  /** Another of each shooter's cameras small in a corner of their tile:
   *  one selector (a mount or role) for the whole grid. */
  inset_camera?: string | null;
  inset_corner?: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  inset_size?: "small" | "medium" | "large";
  /** What a cell no shooter fills shows (3 shooters on a 2x2): the stage
   *  card, the live race, the stage's splits, the match card, or black. */
  free_cell?: GridFreeCell;
  /** The YouTube sidecar (title, chapters per stage, tags, thumbnail)
   *  and, with it, the chained upload of the match-level file. */
  youtube_sidecar?: boolean;
  description_lead?: string | null;
  youtube_upload?: boolean;
  youtube_privacy?: "unlisted" | "private" | "public";
  youtube_playlist?: string | null;
  youtube_playlist_id?: string | null;
  youtube_publish_at?: string | null;
  youtube_notify_subscribers?: boolean;
}

/** ``Job.result`` shape for a ``"compare-grid"`` job, mirrors the dict
 *  built by ``server._run_compare_grid``. A partially-successful render
 *  (some stages failed, others didn't) is reported via ``failed`` rather
 *  than treated as an all-or-nothing failure.
 *
 *  ``stages_total`` counts the stages the *request* asked for. A stage
 *  nobody has a trim for never gets planned, so it renders neither
 *  successfully nor as a failure: it lands in ``skipped_stages``, and
 *  the shooter/stage pairs behind it in ``missing_trims``. Both are
 *  optional so a snapshot from a job queued before they existed still
 *  parses. */
export interface CompareGridResult {
  /** Basename of the finished grid, resolvable in either mode through
   *  ``GET /api/match/exports/file/{output_name}`` (#755). Optional so a
   *  snapshot from a job queued before it existed still parses. */
  output_name?: string;
  output_path: string;
  stages_rendered: number;
  stages_total: number;
  failed: Array<{ stage_number: number; stage_name: string; error: string }>;
  skipped_stages?: number[];
  missing_trims?: Array<{
    shooter: string;
    stage_number: number;
    stage_name: string;
    expected_path: string;
    camera: string | null;
  }>;
  /** Issue #705. What the render did *not* do -- a browserless host or a
   *  drawtext-less ffmpeg still renders, and this is how a viewer learns
   *  the summary hold or the clock was skipped. ``summary`` is the short
   *  clause for a status line, ``detail`` the whole story. Optional so a
   *  snapshot from a job queued before it existed still parses. */
  degradations?: Array<{ summary: string; detail: string }>;
}

export interface RemovalPlan {
  video_path: string;
  /** Null while another stage still registers the file (a single take). */
  raw_link_path: string | null;
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

export type CleanupCategory = Schema["CleanupCategory"];

export interface CleanupItem {
  path: string;
  size_bytes: number;
  category: CleanupCategory;
  /** Set when the durable bytes live in object storage (hosted). */
  storage_key: string | null;
  /** False when this artefact's own input is already gone, so deleting
   *  it costs data rather than recompute time. Such items are kept out
   *  of "select all" and need an explicit opt-in. */
  reconstructable: boolean;
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
export type SuggestedStart = Schema["SuggestedStart"];

export type FsListing = Schema["FsListing"];

/** One window of a video's spectrogram (spec 2026-10-17): ``data`` is base64
 *  uint8, column-major, ``hz.length`` bytes per column, low to high. */
export interface Spectrogram {
  start: number;
  duration: number;
  hz: number[];
  columns: number;
  data: string;
  /** The timer's own band around the video's beep, Hz. */
  tone_band: [number, number];
}

export interface PeaksResult {
  duration: number;
  sample_rate: number;
  bins: number;
  peaks: number[];
  /** Seconds into the WAV where these peaks begin: 0 for the whole file,
   *  the window's start when one was asked for. */
  start?: number;
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
  /** Stable identity, stamped server-side at the save boundary. Absent on
   *  documents saved before shot ids shipped. */
  id?: string;
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

/** Stage events (spec 2026-10-08): regions on the stage timeline in
 *  seconds from the beep, one lane per kind. A reload's handles mean hand
 *  off the grip -> gun back on target. */
export type StageEventKind = "draw" | "movement" | "reload" | "activation";
export interface StageEvent {
  id: string;
  kind: StageEventKind;
  start: number;
  end: number;
  source: CoachIntervalClassSource;
  note?: string | null;
}
export interface StageEventSummary {
  movement_s: number;
  moving_shots: number;
  reloads: number;
  reload_avg_s: number | null;
  exposed_reload_s: number;
  capacity_warning: string | null;
}

export interface CoachShot {
  /** Stable shot id (#844) -- what ``patchStageShotCoach`` addresses the
   *  by-id route with. ``null`` when the stored doc has no usable id for
   *  this shot (a legacy doc no save boundary has stamped, or a
   *  hand-edited non-string id); the patch then falls back to the
   *  positional route guarded by ``expected_version``. */
  id: string | null;
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
  /** Inside a movement region; absent from servers before 0.6x. */
  moving?: boolean;
}

export interface CoachVideoEntry {
  path: string;
  role: VideoRole;
  /** Where the beep falls in the clip the SPA actually receives from
   *  /api/videos/stream for this video -- the trimmed clip when one
   *  exists, the source otherwise. ``null`` for cameras without a beep
   *  yet; those are unsyncable and the SPA leaves them disabled. */
  beep_in_clip: number | null;
  /** Which clip ``beep_in_clip`` was measured against - pin it as the
   *  stream kind so a trim job completing mid-session cannot shift the
   *  served bytes under a stale anchor (same hazard the audit screen
   *  avoids with explicit trim/proxy kinds). ``web`` is the trim's
   *  720p faststart rendition, reported in hosted mode when the object
   *  exists; it shares the trim's anchor. */
  kind: "trim" | "source" | "web";
  /** What a viewer calls this camera ("Insta360 GO 3S", "Handheld",
   *  "Camera 2"); absent from servers older than 0.49.2. */
  label?: string;
  /** The camera's mount ("head", "hand", ...); with the role it is what a
   *  camera choice keys on across stages (``camera_select``). */
  mount?: string | null;
  /** The trim's frames per second from its own MP4 boxes; null when the
   *  trim is not on the server's disk. Read through ``clipFps``. */
  fps?: number | null;
  /** Identity of the local audit trim (mtime + size), null when none is on
   *  disk. Goes into a pinned ``kind=trim`` URL so a re-cut trim is a new
   *  URL and remounts the player instead of leaving it on a dead stream. */
  trim_version?: string | null;
  /** Identity of the trim's fresh 720p rendition on local disk, null when
   *  there is none. The Audit players stream ``kind=web`` with it instead
   *  of the full-resolution trim (scrubbing a 4K trim stalls). */
  scrub_version?: string | null;
}

export interface CoachStageResponse {
  stage_number: number;
  stage_name: string;
  /** Where the beep falls in the served primary clip; same coordinate
   *  system as ``shots[i].time_absolute``. */
  beep_time: number;
  /** Version of the audit doc this response was built from (#844). Feed it
   *  back as ``expected_version`` on a positional shot PATCH. Every coach
   *  response carries the version *after* whatever save produced it, so
   *  replacing coach state with a PATCH's response keeps the value fresh
   *  for the next patch. Always 0 in local mode, where files do not lock. */
  version: number;
  videos: CoachVideoEntry[];
  /** The shooter's saved camera for comparisons (a mount or role); the
   *  stage and Compare pages start on it. Absent from older servers. */
  compare_camera?: string | null;
  shots: CoachShot[];
  events?: StageEvent[];
  event_summary?: StageEventSummary;
  /** The stage note (#1376); null when there is none or on a share read.
   *  Absent from older servers. */
  stage_note?: string | null;
  /** audit_revision of the stored doc; what putStageEvents sends back */
  _version?: string;
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
  /** Quartiles backing the self-relative gap tiers; null below 2 samples. */
  p25_s: number | null;
  p75_s: number | null;
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
  /** Set by ``patchStageShotCoach`` on the positional fallback only --
   *  callers pass the version separately rather than composing it in. */
  expected_version?: number;
}

/** A row of the Shooters page (``GET /api/me/shooters``): the look the
 *  videos draw for one shooter, from the book or their newest match. */
export interface ShooterRosterRow {
  shooter_id: number | null;
  name: string;
  club: string | null;
  accent: string | null;
  logo_url: string | null;
  match_count: number;
  last_match_at: string;
  last_match_name: string;
  you: boolean;
  source: "book" | "match" | "none";
  /** For a shooter without an SSI id: the one match the row stands for. */
  match_id: string | null;
  slug: string | null;
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
  /** Triage flag (#823) - present when a mobile operator flagged this
   *  stage for desktop follow-up; cleared server-side on the next
   *  desktop save. */
  needs_attention?: TriageAttention | null;
}

export type JobStatus = Schema["JobStatus"];

export interface JobPhase {
  name: string;
  ms: number;
}

/** Per-job observability persisted on completion (observability.py). */
export interface JobTimings {
  queue_wait_ms: number | null;
  total_ms: number;
  phases: JobPhase[];
  meta?: Record<string, unknown>;
}

/** Mirror of splitsmith.ui.jobs.Job. Long-running endpoints (detect-beep,
 *  trim, future shot-detect/export) submit a job and return a snapshot;
 *  the SPA polls /api/me/jobs/{id} until status leaves "pending" / "running". */
export type Job = Schema["Job"];

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

/** The 409 body ``cleanup_apply`` returns while a job is in flight. */
export interface JobsActiveDetail {
  code: "jobs_active";
  message: string;
  job_id: string;
  kind: string;
}

/** Pull a jobs-active refusal out of an ApiError, or null if the body
 *  doesn't match. The cleanup dialog names the blocking job rather than
 *  showing a generic failure -- "trim is still running" is actionable and
 *  "cleanup failed" is not. */
export function asJobsActiveError(err: unknown): JobsActiveDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (!body || typeof body !== "object") return null;
  if ((body as { code?: unknown }).code !== "jobs_active") return null;
  return body as JobsActiveDetail;
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

/** Shown wherever a desktop-origin mirror match blocks a mutation -
 *  both the persistent MatchShell banner (#631 Task 10) and, via
 *  ``apiErrorText`` below, any 403 ``read_only_mirror`` response that
 *  reaches a generic error path instead of the banner. Keeping one
 *  string means the two surfaces can never say something different. */
export const READ_ONLY_MIRROR_MESSAGE =
  "Synced from a desktop install - review actions sync back, editing stays on desktop.";

/** True when ``err`` is the 403 the server raises for a mutation against
 *  a desktop-origin mirror match (#631 Task 6 - every alias-routed
 *  write except share-link management). The server sends a plain-string
 *  ``detail`` (not the structured ``{code, ...}`` shape ``isNoProjectError``
 *  matches), so ``err.body`` is compared directly. */
export function isReadOnlyMirrorError(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    err.status === 403 &&
    err.body === "read_only_mirror"
  );
}

/** What each account feature lets a user do, as the subject of a refusal
 *  sentence. Keyed by ``lib/access``'s ``Feature``. */
const FEATURE_WHAT: Record<Feature, string> = {
  sync: "Syncing from the desktop app",
  share: "Sharing",
  create_match: "Creating matches here",
  raw_upload: "Uploading footage here",
  hosted_compute: "Running detection and renders here",
};

/** One line for an account refusal (spec 2026-10-03), or null when
 *  ``err`` is not one.
 *
 *  ``ApiError.body`` holds the response's raw ``detail`` (see
 *  ``request()``), so the refusal's ``{code, feature}`` is the body
 *  itself, not nested under a ``detail`` key. */
export function featureRefusal(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null;
  const detail = err.body;
  if (!detail || typeof detail !== "object") return null;
  const { code, feature } = detail as { code?: unknown; feature?: unknown };
  if (code === "account_disabled") return "This account is disabled.";
  if (code === "feature_required" && typeof feature === "string" && Object.hasOwn(FEATURE_WHAT, feature)) {
    return `${FEATURE_WHAT[feature as Feature]} is not included in this account's access.`;
  }
  return null;
}

/** :func:`apiErrorText` plus whether the line is an account refusal, for a
 *  page that renders a refusal as a muted line rather than an error. */
export interface ErrorLine {
  text: string;
  refusal: boolean;
}

export function errorLine(err: unknown, fallback: string): ErrorLine {
  return { text: apiErrorText(err, fallback), refusal: featureRefusal(err) !== null };
}

/** A sentence fit to render inline in the UI for an arbitrary thrown value.
 *
 *  ``ApiError.message`` is ```${status}: ${detail}` `` and ``detail`` is
 *  ``JSON.stringify(rawDetail)`` whenever the server sent a structured
 *  ``detail`` (the ``{code, ...}`` 409s / 424s). Rendering ``e.message``
 *  therefore drops a raw JSON blob into the page. This helper renders the
 *  detail only when it is genuinely prose, and ``fallback`` otherwise.
 *
 *  The discriminator is ``ApiError.body``, which holds the raw ``detail``
 *  before stringification:
 *
 *  - ``string`` -- the server sent ``{"detail": "some sentence"}``, or an
 *    XHR path fell back to ``xhr.statusText``. Prose either way; show it.
 *  - ``null`` -- either :func:`request` never found a ``detail`` key and
 *    fell back to ``resp.statusText``, or a client-side construction site
 *    (network error, cancelled upload, missing ETag) passed a hand-written
 *    English message. Both are prose; show it. Suppressing them would
 *    replace "network error during upload" with a vaguer fallback, and
 *    would make the two status-text paths disagree -- :func:`request`
 *    leaves ``body`` null where the XHR upload path passes the status text
 *    through as ``body``, for the same "Internal Server Error" string.
 *  - anything else (object, array, number, boolean) -- ``detail`` was
 *    stringified from a structure. Never show it; use ``fallback``.
 *
 *  Non-``ApiError`` values (including plain ``Error``) get ``fallback``:
 *  their messages are internal-facing, not written for an operator.
 *  The ``instanceof`` test lives here so callers need not import the class.
 */
export function apiErrorText(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  // An account refusal is a structured detail, which the discriminator
  // below would replace with ``fallback``; it has a sentence of its own.
  const refusal = featureRefusal(err);
  if (refusal) return refusal;
  // read_only_mirror's raw detail is a machine-readable code, not prose
  // ("Synced from..." is what the caller should see, not the literal
  // string "read_only_mirror") - map it before the general string/object
  // discriminator below, which would otherwise pass it through verbatim.
  if (isReadOnlyMirrorError(err)) return READ_ONLY_MIRROR_MESSAGE;
  if (err.body !== null && typeof err.body !== "string") return fallback;
  const detail = err.detail?.trim();
  return detail ? detail : fallback;
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
  is_admin: boolean;
  /** Hosted access tier name, for display only; code checks ``features``
   *  (lib/access.ts). Null for the local loopback user. */
  access_tier: string | null;
  /** Sorted feature names this account holds. */
  features: string[];
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

/** ``PUT /api/me/scoreboard-identity``: replaces the pin whole (build it
 *  with ``lib/you.pinBody``). */
export type ScoreboardPin = Omit<ScoreboardIdentity, "display_name" | "division" | "club" | "base_url"> &
  Partial<Pick<ScoreboardIdentity, "display_name" | "division" | "club" | "base_url">>;

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
  /** Stable match identifier (#353 Phase 3). Populated for
   *  ``kind="match"`` entries; what dev-mode surfaces plug into the
   *  ``/api/matches/{id}/...`` alias to address a match without a
   *  process-level bind. */
  match_id?: string | null;
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

/** A request the phone made for the desktop to run (#1100). */
export type DesktopCommandStatus = "pending" | "claimed" | "succeeded" | "failed" | "cancelled";

export interface DesktopCommand {
  id: string;
  match_id: string;
  kind: string;
  slug: string | null;
  stage_number: number | null;
  args: Record<string, unknown>;
  expected_revision: string | null;
  status: DesktopCommandStatus;
  cancel_requested: boolean;
  progress_message: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  requested_at: string;
  claimed_at: string | null;
  lease_expires_at: string | null;
  finished_at: string | null;
}

/** Whether a linked desktop is around to pick a request up (#1100):
 *  ``around`` = seen within five minutes. */
export interface DesktopPresence {
  linked: boolean;
  last_seen_at: string | null;
  around: boolean;
}

export interface DesktopCommandList {
  commands: DesktopCommand[];
  presence: DesktopPresence;
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

/** Where a match's canonical data lives (#631). A "desktop" match is a
 *  read-only mirror synced down from the desktop app - every mutation
 *  except share-link management and match deletion 403s
 *  ``read_only_mirror`` (see :func:`isReadOnlyMirrorError`). "hosted"
 *  matches are hosted-native; "local" is the desktop app's own view of
 *  its own matches (the field is always "local" there). */
export type MatchOrigin = "hosted" | "desktop" | "local";

/** Server-derived per-match capability set (#756), computed next to the
 *  403 guard so payload and enforcement can never disagree. Gate write
 *  affordances on these, never on `origin` - origin is provenance
 *  (picker flag) and media-surface behavior (#821), not writability.
 *  `comment_write` joined the table in the timestamped-comments Task 5
 *  work, granted by the same hosted/desktop origin sets as the others.
 *  `breakdown` is the region edit: always on a local or hosted match, on a
 *  desktop mirror once its desktop merges regions (spec 2026-10-18). */
export type MatchCapability = "edit" | "review" | "breakdown" | "share_manage" | "comment_write";

/** True when the capability set is KNOWN and lacks `cap`. Null or
 *  undefined means "not loaded yet" and denies nothing - pages keep
 *  their current optimistic render until the shell's first fetch
 *  resolves, exactly as the origin-based gates behaved. */
export function capabilityDenied(
  caps: MatchCapability[] | null | undefined,
  cap: MatchCapability,
): boolean {
  return Array.isArray(caps) && !caps.includes(cap);
}

/** Detailed RecentProject with on-disk metadata (`?detail=true`, #322). */
/** The picker's Continue card target (UX PR 8): the first open stage in
 *  shooter then stage order, or the phase the match is in. */
export interface NextStep {
  kind: "details" | "footage" | "audit" | "export";
  shooter_slug: string | null;
  stage_number: number | null;
  stage_name: string | null;
}

export interface RecentProjectDetail {
  path: string;
  name: string;
  last_opened_at: string;
  kind: "match" | "legacy" | "missing" | "unknown";
  /** Stable match identifier (#353 Phase 3). Populated for
   *  ``kind="match"``; ``null`` otherwise. What dev-mode surfaces plug
   *  into the ``/api/matches/{id}/...`` alias to address the match
   *  without a process-level bind. */
  match_id: string | null;
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
  /** See :type:`MatchOrigin` (#631). */
  origin: MatchOrigin;
  /** Desktop only: synced to hosted at least once, so a hosted copy
   *  exists that a desktop delete does not touch. Optional for an older
   *  server. */
  synced?: boolean;
  /** ``null`` for unresolved kinds. */
  next_step: NextStep | null;
}

export type CreateMatchStageDraft = Schema["CreateMatchStageDraft"];

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
export type CreateMatchCompetitorPick = Schema["CreateMatchCompetitorPick"];

export interface CreateMatchScoreboardBody {
  /** Optional in hosted mode (see :type:`CreateMatchManualBody`). */
  project_folder?: string | null;
  name: string;
  match_id: number;
  content_type: number;
  competitors: CreateMatchCompetitorPick[];
}

/** One camera inside a shooter (#324). */
export type ShooterCameraInfo = Schema["ShooterCameraInfo"];

/** One stage's lifecycle status for a single shooter, mirrored from the
 *  backend ``StageStatusEntry``. The match Overview pivots these across
 *  shooters into an aggregate per-stage grid. */
export type StageStatusEntry = Schema["StageStatusEntry"];

/** Triage attention marker for a single stage. */
export interface TriageAttention {
  flagged: boolean;
  flagged_at: string | null;
  note: string | null;
  updated_at: string;
}

/** One cell in the triage grid - a shooter's stage with its audit
 *  status, beep confidence, anomalies, and attention marker. */
export type TriageCell = Schema["TriageCell"];

/** Where one stage stands for the highlight reel
 *  (``GET /api/shooters/{slug}/highlights``, ``highlights.stage_readiness``). */
export type HighlightReadinessState =
  | "ready"
  | "not_audited"
  | "breakdown_not_opened"
  | "proposals_left"
  | "trim_missing"
  | "no_video"
  | "skipped";

export interface HighlightStageReadiness {
  stage_number: number;
  state: HighlightReadinessState;
  proposals: number;
}

/** One moment on the reel's menu, in play order. */
export interface HighlightMenuItem {
  kind: string;
  label: string;
  group: "default" | "stat" | "lolz";
  default: boolean;
}

export interface HighlightReadiness {
  unlocked: boolean;
  ready: number;
  total: number;
  stages: HighlightStageReadiness[];
  menu: HighlightMenuItem[];
  cuts: string[];
}

/** ``GET /api/shooters/{slug}/highlights/latest``. */
export interface LatestReel {
  filename: string;
  short: boolean;
  finished_at: string;
}

/** ``POST /api/shooters/{slug}/highlights``: the reel's render request. */
export interface HighlightReelBody {
  moments: string[];
  short: boolean;
  upright: boolean;
  force: boolean;
  youtube_upload: boolean;
  youtube_privacy: "unlisted" | "private" | "public";
  /** "default" is the shooter's saved camera; else a mount or a role. */
  main_camera: string;
}

/** Response from GET /api/match/triage and POST accept/attention mutations.
 *  Returns the current triage list and the count of flagged cells. */
export type TriageResponse = Schema["TriageResponse"];

/** Response from GET /api/match/triage/summary - the cheap poll behind
 *  the sidebar/drawer flagged-count badge. Avoids paying for the full
 *  triage cell list on every shell load and job settlement. */
export type TriageSummaryResponse = Schema["TriageSummaryResponse"];

/** One shooter row in the /shooters page (#324). */
export type ShooterListEntry = Schema["ShooterListEntry"];

/** Response payload for POST /api/match/shooters/{slug}/build-trim-caches (#351). */
export interface BuildTrimCachesResult {
  shooter_slug: string;
  shooter_name: string | null;
  jobs_submitted: Job[];
  /** Angles considered and passed over. ``video_id`` is absent when the
   *  whole stage was rejected (no primary, no primary beep, no stage
   *  time) rather than one of its angles. */
  skipped: { stage: number; video_id?: string; reason: string }[];
}

export type ShooterListResponse = Schema["ShooterListResponse"];

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
  /** Slug of the shooter that has already attached this object in the
   *  current match, or null if unattached. A raw object belongs to one
   *  shooter, so the ingest UI hides uploads owned by a different shooter
   *  (attach also rejects them server-side with 409). Null outside a match
   *  context. (#562) */
  attached_to?: string | null;
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
export type CompareShotPoint = Schema["CompareShotPoint"];

/** Per-shooter data for a stage in /compare (#328). Field is a logical
 *  ref (``exports/<name>`` | ``trimmed/<name>``), not a filesystem path
 *  (#700) - resolved server-side against local dirs or storage. */
export type CompareShooterRecord = Schema["CompareShooterRecord"];

export type CompareStageResponse = Schema["CompareStageResponse"];

/** One pending beep review item in the cross-shooter queue (#326). */
export type BeepQueueAltCandidate = Schema["BeepQueueAltCandidate"];

export type BeepQueueItem = Schema["BeepQueueItem"];

export type BeepQueueStageGroup = Schema["BeepQueueStageGroup"];

export type BeepQueueResponse = Schema["BeepQueueResponse"];

/** Desktop-pushed beep review snippet peaks for a mirror video (slice 3).
 *  Same waveform-picker payload shape as {@link PeaksResult} plus the
 *  fields the mirror needs since it has no live detector: where the
 *  snippet starts in the source timeline, the ranked candidates, and the
 *  input hash desktop uses to skip regenerating an unchanged snippet. */
export interface BeepSnippetPeaks {
  snippet_start: number;
  duration: number;
  sample_rate: number;
  bins: number;
  peaks: number[];
  beep_time: number | null;
  candidates: { time: number; confidence: number | null }[];
  input_hash: string;
}

/** Merge wizard types (#332). plan_merge dry-runs the merge; the SPA
 *  shows the user the per-shooter slug assignments + reconciled stages +
 *  detected conflicts before they commit. execute_merge actually writes
 *  the new match folder and binds the first shooter as active. */
export type MergePlanStage = Schema["MergePlanStage"];
export type MergePlanShooterMove = Schema["MergePlanShooterMove"];
export type MergePlanResponse = Schema["MergePlanResponse"];

/** Developer-mode model chip + workflow-stepper counts. Built once by the
 *  /api/dev/model endpoint from the shipped ensemble artifacts plus the
 *  review-queue tally on disk. */
export type DeveloperModelInfo = Schema["DeveloperModelInfo"];

export type DevReviewQueueItem = Schema["DevReviewQueueItem"];

export type DevReviewQueueResponse = Schema["DevReviewQueueResponse"];

/** One (stage_number, video_id) pair in a bulk camera-set call.
 *  Mirrors ``BulkCameraSetItem`` in server.py. */
export type BulkCameraSetItem = Schema["BulkCameraSetItem"];

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

/** The event logo's URL for an ``<img>`` (the branding work); ``version``
 *  busts the browser cache after a change. */
export function eventLogoUrl(version: number): string {
  return `${scopeRequestPath("/api/match/branding/event-logo")}?v=${version}`;
}

export interface RawUploadResult {
  path: string;
  size: number;
  sha256: string | null;
  filename: string;
}

/** A timestamped comment on a stage, anchored either to a wall-clock
 *  time in the stage video or to a specific shot. */
export interface Comment {
  id: string;
  anchor_t: number;
  anchor_kind: "time" | "shot";
  anchor_shot_id: string | null;
  author_kind: "handle" | "account";
  author_handle: string;
  /** Stable public identifier for the author -- an HMAC of their account
   *  id or browser key, never either one. Two authors posting under the
   *  same name are told apart by this. See lib/authorAmbiguity.ts. */
  author_code: string;
  body: string;
  created_at: string;
  mine: boolean;
  /** Owner view only; absent for anonymous callers. */
  share_token_id?: string | null;
  author_key_hash?: string | null;
}

export interface CommentListResponse {
  comments: Comment[];
}

export interface CommentCreateInput {
  body: string;
  anchor_t: number;
  anchor_kind: "time" | "shot";
  anchor_shot_id?: string | null;
}

/** One author's footprint on a match. Owner-only: the route is not on
 *  the anonymous share surface. */
export interface CommentAuthor {
  author_code: string;
  author_kind: "handle" | "account";
  first_comment_at: string;
  comment_count: number;
  /** Every distinct name this code posted under, oldest first. Two
   *  entries means the author renamed themselves mid-thread. */
  handles: string[];
}

export type CommentAuthorListResponse = Schema["CommentAuthorListResponse"];

export const api = {
  ...stagesApi,
  ...shootersApi,
  ...matchApi,
  ...meApi,
  ...labApi,
  ...miscApi,
};

/** Verified host capabilities a worker advertised at registration (#796). */
export interface WorkerCapabilities {
  /** ffmpeg h264_nvenc usable (gated on a real trial encode). */
  nvenc_h264?: boolean;
  /** onnxruntime CUDA execution provider available. */
  cuda_ep?: boolean;
  /** GPU model string, best-effort; null on CPU-only boxes. */
  gpu_name?: string | null;
}

/** Advisory box metadata a worker reported at registration. All optional. */
export interface WorkerInfo {
  agent_version?: string;
  hostname?: string;
  concurrency?: number;
  capabilities?: WorkerCapabilities;
}

/** One compute worker row, mirrored from the backend WorkerView. */
export type WorkerView = Schema["WorkerView"];

/** One access request, mirrored from the hosted admin API. */
export interface AccessRequest {
  id: string;
  email: string;
  note: string | null;
  source: string;
  status: "pending" | "approved" | "declined";
  requested_at: string;
  last_requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  tier_granted: string | null;
  email_sent_at: string | null;
}

/** One hosted account as the admin user list shows it. */
export interface AdminAccount {
  id: string;
  email: string;
  display_name: string | null;
  access_tier: string;
  created_at: string;
  is_admin: boolean;
}

export interface AccessTiers {
  tiers: { name: string; features: string[] }[];
  default_tier: string;
}

export interface WorkerListResponse {
  workers: WorkerView[];
  /** This server's release; the UI flags workers running behind it. */
  server_version: string;
}

export interface CreateWorkerResponse {
  worker: WorkerView;
  registration_token: string;
  expires_at: string;
  docker_command: string;
}

export interface WorkerPatch {
  enabled?: boolean;
  priority?: number;
  name?: string;
}

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
  /** ISO timestamp stamped by both promotion paths (anchor and batch
   *  promote-stages). Null only for hand-dropped fixtures. */
  promoted_at: string | null;
  /** Kept shots carrying a human ``subclass`` label (paper/steel/...). */
  n_labeled_shots: number;
  /** Rejected candidates carrying a human ``reason`` label. */
  n_labeled_rejects: number;
  /** True when the slug is in the active calibration artifact's
   *  ``calibration_fixtures`` -- i.e. the shipped model was trained on
   *  this fixture. False means it's waiting on a retrain. */
  in_calibration: boolean;
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
export type MoveShooterResultItem = Schema["MoveShooterResultItem"];

/** One video that could not be moved (occupied-stage rule). Mirrors
 *  ``MoveShooterBlocked`` on the backend. */
export type MoveShooterBlocked = Schema["MoveShooterBlocked"];

/** Outcome of a moveShooter call (backend ``MoveShooterOutcome``). */
export type MoveShooterOutcome = Schema["MoveShooterOutcome"];

/** Full response from POST /api/match/videos/move-shooter. */
// Hand-written until the project view model lands (#1591 group C): ``source_project``
export type MoveShooterResponse = Schema["MoveShooterResponse"];

/** One share link for a match. Mirrors the backend ShareInfo Pydantic model.
 *  ``revoked_at`` is null while the link is live. */
export type ShareInfo = Schema["ShareInfo"];

/** Response from GET /api/match/shares. */
export type ShareListResponse = Schema["ShareListResponse"];

/** One desktop sync token as the owner-management routes return it
 *  (#631 Task 3). Mirrors the backend ``DesktopTokenInfo``. Never
 *  carries the token hash or the raw value. */
export type DesktopTokenInfo = Schema["DesktopTokenInfo"];

/** Response from GET /api/me/desktop-tokens. */
export type DesktopTokenListResponse = Schema["DesktopTokenListResponse"];

/** Response from POST /api/me/desktop-tokens. ``token`` is the raw
 *  bearer value - it appears here and only here; every other response
 *  (including this token's own future GET/DELETE entries) carries just
 *  the ``DesktopTokenInfo`` record. */
export type DesktopTokenCreateResponse = Schema["DesktopTokenCreateResponse"];

/** Response from DELETE /api/me/desktop-tokens/{token_id}. */
export type DesktopTokenRevokeResponse = Schema["DesktopTokenRevokeResponse"];

/** Response from GET/PUT /api/settings/hosted-sync (#631 Task 9). Local
 *  install's hosted-sync target: where the desktop client pushes a
 *  match and which desktop token it authenticates with. ``token_set``
 *  is a boolean, never the raw token - the server never echoes a
 *  bearer credential back over localhost HTTP. */
export type HostedSyncSettings = Schema["HostedSyncSettings"];

/** The hosted account this install is linked to (#719). Cached from the
 *  device-flow poll on the server side, never a live lookup -- the
 *  sync-scoped token cannot read /api/me. */
export type HostedAccountInfo = Schema["HostedAccountInfo"];

/** Response from POST /api/settings/hosted-sync/device/start (#719).
 *  Carries no device_code: the secret stays on the local server.
 *  ``resumed`` means a login was already in flight on this install and
 *  this call handed that one back rather than starting a new one -- same
 *  user_code, same links, ``expires_in`` counting down the remainder of
 *  the original window. */
export type DeviceStartResponse = Schema["DeviceStartResponse"];

/** Response from GET /api/settings/hosted-sync/device/status (#719).
 *  ``denied`` and ``expired`` are distinct terminal states on purpose. */
export type DeviceStatusResponse = Schema["DeviceStatusResponse"];

/** Response from DELETE /api/settings/hosted-sync/session (#719, #737).
 *  ``hosted_revoked`` is tri-state: true - the hosted side confirmed the
 *  revoke; false - a revoke was attempted and failed (warn); null -
 *  there was nothing to revoke (do not warn). */
export type DeviceUnlinkResponse = Schema["DeviceUnlinkResponse"];

/** The device authorization awaiting approval (#719). */
export interface DevicePendingInfo {
  user_code: string;
  device_name: string;
  scope: string;
  created_at: string;
  expires_at: string;
}

/** GET/PUT /api/match/sync/auto (spec 2026-09-27). ``setting`` null means
 *  the default, which is on once the match has synced. */
export interface AutoSyncStatus {
  enabled: boolean;
  setting: boolean | null;
  global_enabled: boolean;
  paused_reason: string | null;
  /** Upload full-resolution trims too (v1.1); off = hosted plays the
   *  720p rendition. */
  full_media: boolean;
  /** Full trims still on hosted; with full_media off the next sync
   *  removes those whose rendition is there. */
  full_trims_on_hosted: number;
  last_auto: {
    at: string;
    ok: boolean;
    message: string;
    conflicts: number;
    notes: number;
  } | null;
  /** Parked after a failed run: no retry until a local edit, a hosted
   *  change, or Sync. Optional so an older sidecar's payload still types. */
  waiting_for_change?: boolean;
  /** Synced before, then deleted on hosted: sync is off until
   *  ``republishSync``. Optional so an older sidecar's payload types. */
  hosted_deleted?: boolean;
}

/** Response from GET /api/match/sync/status (#631 Task 9). Cheap
 *  size/mtime-only planning, no hashing - safe to poll. ``stale`` is
 *  true whenever a push would have real work to do (never synced,
 *  unpushed media, or the plan itself can't run). ``errors`` lists
 *  the reasons a push would refuse to run right now. ``remote_changes``
 *  is a best-effort staleness hint (count of hosted docs newer than
 *  this desktop's last sync); null means unknown - sync unconfigured
 *  or the hosted side unreachable right now, not an error. */
export type SyncStatusResponse = Schema["SyncStatusResponse"];

