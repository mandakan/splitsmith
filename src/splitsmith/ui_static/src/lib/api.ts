/**
 * Typed client for the splitsmith UI backend.
 *
 * Mirrors the Pydantic models in src/splitsmith/ui/project.py and the
 * endpoint surface in src/splitsmith/ui/server.py. When the backend grows,
 * extend this file rather than scattering fetch() calls across the SPA.
 */

export type VideoRole = "primary" | "secondary" | "ignored";
export type BeepSource = "auto" | "manual";

/** One ranked beep candidate emitted by ``detect-beep`` (issue #22).
 *  ``score`` is silence-preference (run_peak / pre-window mean); higher =
 *  more confident. ``beep_candidates[0]`` matches the promoted ``beep_time``
 *  on the parent ``StageVideo``. */
export interface BeepCandidate {
  time: number;
  score: number;
  peak_amplitude: number;
  duration_ms: number;
}

export interface StageVideo {
  path: string;
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
  beep_peak_amplitude: number | null;
  beep_duration_ms: number | null;
  /** Ranked alternative candidates from the most recent auto-detection run.
   *  Empty when the project predates issue #22 or after a manual override. */
  beep_candidates: BeepCandidate[];
  notes: string;
}

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
  confirm?: boolean;
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

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  suggested_starts: string[];
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
}

export interface AuditEvent {
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
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
  status: JobStatus;
  progress: number | null;
  message: string | null;
  error: string | null;
  /** True after the SPA POSTed /api/jobs/{id}/cancel for this job. The flag
   *  stays True on the terminal snapshot so the row can be labelled
   *  "Cancelled by user" instead of "Aborted". */
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
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

  importScoreboard: (data: unknown, overwrite = false) =>
    request<MatchProject>("/api/scoreboard/import", {
      method: "POST",
      json: { data, overwrite },
    }),

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

  /** Submit a beep-detection job. Returns a Job snapshot; poll the SPA
   *  via {@link api.pollJob} (or read /api/jobs/{id} directly) until the
   *  status flips out of "pending"/"running". On success, the SPA should
   *  re-fetch /api/project to pick up the new beep_time + processed.trim. */
  detectBeep: (stageNumber: number, force = false) =>
    request<Job>(
      `/api/stages/${stageNumber}/detect-beep${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),

  overrideBeep: (stageNumber: number, beepTime: number | null) =>
    request<MatchProject>(`/api/stages/${stageNumber}/beep`, {
      method: "POST",
      json: { beep_time: beepTime },
    }),

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

  /** Submit an audit-mode short-GOP trim job. Returns a Job snapshot;
   *  idempotent on the worker side -- when the cached MP4 is fresh the
   *  job completes near-instantly without re-encoding. */
  trimStage: (stageNumber: number) =>
    request<Job>(`/api/stages/${stageNumber}/trim`, { method: "POST" }),

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

  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (jobId: string) => request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),

  /** Request cooperative cancellation. Idempotent: a finished job is returned
   *  as-is. For a running trim job the server terminates the underlying
   *  ffmpeg subprocess so the cancel takes effect immediately. */
  cancelJob: (jobId: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

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

  /** URL for a tiny MP4 around a beep timestamp (#27, #22). ``t`` is
   *  passed to the server (which centres the clip there) AND ms-rounded
   *  into the cache key, so each distinct ``t`` gets its own MP4. The
   *  candidate picker uses this with arbitrary candidate times; the
   *  default flow passes ``primary.beep_time``. */
  stageBeepPreviewUrl: (stageNumber: number, beepTime: number) =>
    `/api/stages/${stageNumber}/beep-preview?t=${beepTime.toFixed(3)}`,

  videoStreamUrl: (videoPath: string) =>
    `/api/videos/stream?path=${encodeURIComponent(videoPath)}`,

  getStagePeaks: (stageNumber: number, bins = 1200) =>
    request<PeaksResult>(`/api/stages/${stageNumber}/peaks?bins=${bins}`),

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
};

export { ApiError };
