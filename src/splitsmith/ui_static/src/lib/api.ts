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
}

export interface MatchProject {
  schema_version: number;
  name: string;
  created_at: string;
  updated_at: string;
  competitor_name: string | null;
  scoreboard_match_id: string | null;
  stages: StageEntry[];
  unassigned_videos: StageVideo[];
  last_scanned_dir: string | null;
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
}

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  suggested_starts: string[];
}

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
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
    try {
      const body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  getProject: () => request<MatchProject>("/api/project"),

  listFolder: (path?: string) => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<FsListing>(`/api/fs/list${qs}`);
  },

  importScoreboard: (data: unknown, overwrite = false) =>
    request<MatchProject>("/api/scoreboard/import", {
      method: "POST",
      json: { data, overwrite },
    }),

  scanVideos: (sourceDir: string, autoAssignPrimary = true) =>
    request<ScanResponse>("/api/videos/scan", {
      method: "POST",
      json: { source_dir: sourceDir, auto_assign_primary: autoAssignPrimary },
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

  detectBeep: (stageNumber: number, force = false) =>
    request<MatchProject>(
      `/api/stages/${stageNumber}/detect-beep${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),

  overrideBeep: (stageNumber: number, beepTime: number | null) =>
    request<MatchProject>(`/api/stages/${stageNumber}/beep`, {
      method: "POST",
      json: { beep_time: beepTime },
    }),

  stageAudioUrl: (stageNumber: number) => `/api/stages/${stageNumber}/audio`,
};

export { ApiError };
