/**
 * Typed client for the splitsmith UI backend.
 *
 * Mirrors the Pydantic models in src/splitsmith/ui/project.py and the
 * endpoint surface in src/splitsmith/ui/server.py. When the backend grows,
 * extend this file rather than scattering fetch() calls across the SPA.
 */

export type VideoRole = "primary" | "secondary" | "ignored";
export type BeepSource = "auto" | "manual";

export interface StageVideo {
  path: string;
  role: VideoRole;
  added_at: string;
  processed: { beep: boolean; shot_detect: boolean; trim: boolean };
  beep_time: number | null;
  beep_source: BeepSource | null;
  beep_peak_amplitude: number | null;
  beep_duration_ms: number | null;
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

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

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

  /** Submit an audit-mode short-GOP trim job. Returns a Job snapshot;
   *  idempotent on the worker side -- when the cached MP4 is fresh the
   *  job completes near-instantly without re-encoding. */
  trimStage: (stageNumber: number) =>
    request<Job>(`/api/stages/${stageNumber}/trim`, { method: "POST" }),

  /** Submit a shot-detection job for the stage's audit clip. The job
   *  populates _candidates_pending_audit in the audit JSON; the audit
   *  screen renders markers from there. Auto-triggered after trim;
   *  this endpoint is for manual retrigger. */
  detectShots: (stageNumber: number) =>
    request<Job>(`/api/stages/${stageNumber}/shot-detect`, { method: "POST" }),

  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (jobId: string) => request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),

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
      if (job.status === "succeeded" || job.status === "failed") return job;
      if (Date.now() > deadline) {
        throw new Error(`Timed out waiting for job ${jobId}`);
      }
      await new Promise((r) => setTimeout(r, interval));
    }
  },

  stageAudioUrl: (stageNumber: number) => `/api/stages/${stageNumber}/audio`,

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
};

export { ApiError };
