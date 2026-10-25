/** The account-level calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import { ApiError, MULTIPART_THRESHOLD_BYTES, request, uploadRawMultipart } from "./client";
import type {
  AccessTiers,
  ActiveExports,
  AccountProfileView,
  AuthUser,
  DeleteProjectOptions,
  DeleteProjectResponse,
  DesktopTokenCreateResponse,
  DesktopTokenListResponse,
  ExportView,
  Job,
  RawUploadEntry,
  RawUploadResult,
  RecentProject,
  RecentProjectDetail,
  ScoreboardIdentity,
  ScoreboardPin,
  ScoreboardShooterRef,
  ServerHealth,
  ShooterBookEntryView,
  ShooterRosterRow,
} from "../api";

export const meApi = {
  /** The authenticated account, or a 401 (caught via ``isUnauthorized``)
   *  when signed out. Always 200 in local mode (loopback user). */
  getMe: () => request<AuthUser>("/api/me"),

  /** Hosted mode -- set or clear the account display name. 404s in local
   *  mode. A blank name stores null server-side, never an empty string. */
  updateMe: (displayName: string | null) =>
    request<AuthUser>("/api/me", {
      method: "PATCH",
      json: { display_name: displayName },
    }),

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
    // Large files go direct browser -> R2 via presigned multipart; Cloud
    // Run refuses a serve-proxied body over 32 MiB. Small files keep the
    // simpler single-shot (one round-trip, and serve can still compute the
    // integrity sha256).
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

  /** Saved SSI Scoreboard identity for the operator running this install,
   *  or null when nothing has been pinned yet. The header chrome uses
   *  this to render the user badge; null hides it. The server returns
   *  ``200 null`` for the "not pinned" case so it doesn't show up as a
   *  failed request in DevTools. */
  getScoreboardIdentity: () =>
    request<ScoreboardIdentity | null>("/api/me/scoreboard-identity"),

  /** Pin yourself: your SSI shooter id (and the name it was found under). */
  putScoreboardIdentity: (body: ScoreboardPin) =>
    request<ScoreboardIdentity>("/api/me/scoreboard-identity", { method: "PUT", json: body }),

  clearScoreboardIdentity: () => request<{ ok: boolean }>("/api/me/scoreboard-identity", { method: "DELETE" }),

  /** Find yourself in the live shooter index, no match needed. */
  searchShooterIndex: (q: string) =>
    request<ScoreboardShooterRef[]>(`/api/me/shooter-search?q=${encodeURIComponent(q)}`),

  getAccountProfile: () => request<AccountProfileView>("/api/me/profile"),

  putAccountProfile: (body: { brand_line: string }) =>
    request<AccountProfileView>("/api/me/profile", { method: "PUT", json: body }),

  uploadAccountBrandLogo: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<AccountProfileView>("/api/me/profile/brand-logo", { method: "POST", body: form });
  },

  removeAccountBrandLogo: () => request<AccountProfileView>("/api/me/profile/brand-logo", { method: "DELETE" }),

  getShooterBook: () => request<{ entries: ShooterBookEntryView[] }>("/api/me/shooter-book"),

  /** Everyone you have filmed (the Shooters page): one row per SSI id. */
  listShooters: () => request<{ rows: ShooterRosterRow[] }>("/api/me/shooters"),

  putShooterBookEntry: (shooterId: number, body: { accent?: string | null; club?: string | null; label?: string | null }) =>
    request<ShooterBookEntryView>(`/api/me/shooter-book/${shooterId}`, { method: "PUT", json: body }),

  deleteShooterBookEntry: (shooterId: number) =>
    request<{ ok: boolean }>(`/api/me/shooter-book/${shooterId}`, { method: "DELETE" }),

  uploadShooterBookLogo: (shooterId: number, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<ShooterBookEntryView>(`/api/me/shooter-book/${shooterId}/logo`, { method: "POST", body: form });
  },

  removeShooterBookLogo: (shooterId: number) =>
    request<ShooterBookEntryView>(`/api/me/shooter-book/${shooterId}/logo`, { method: "DELETE" }),

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

  /** Re-enqueue a failed job with its original args. Callable from any
   *  route, match or otherwise - the server rebinds the resubmit to the
   *  job's own original match server-side, not to whatever match the
   *  calling request happens to be scoped to. */
  retryJob: (jobId: string) =>
    request<Job>(`/api/me/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),

  /** The account's running exports, every match, each with its parts and
   *  time left, plus the account's export limit (``null`` on desktop). */
  activeExports: (opts?: { signal?: AbortSignal }) =>
    request<ActiveExports>("/api/me/exports/active", { signal: opts?.signal }),

  /** Cancel every part of an export still waiting or running. */
  cancelExport: (exportId: string) =>
    request<ExportView>(`/api/me/exports/${encodeURIComponent(exportId)}/cancel`, { method: "POST" }),

  /** Run a failed part of an export again, inside the same export. */
  retryExportPart: (exportId: string, jobId: string) =>
    request<ExportView>(`/api/me/exports/${encodeURIComponent(exportId)}/retry`, {
      method: "POST",
      json: { job_id: jobId },
    }),

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

  /** The tier registry and the tier a new account gets. */
  adminAccessTiers: () => request<AccessTiers>("/api/admin/access-tiers"),

  // Desktop-token management (desktop-to-hosted sync MVP, #631 Task 3).
  // Operator-global (/api/me/...), hosted-only - 404s in local mode.

  /** List this account's desktop sync tokens. Never includes the raw
   *  value or its hash. */
  listDesktopTokens: () =>
    request<DesktopTokenListResponse>("/api/me/desktop-tokens"),

  /** Mint a new desktop sync token. The raw bearer value is returned
   *  once, in this response only - store it or lose it. */
  createDesktopToken: (name: string) =>
    request<DesktopTokenCreateResponse>("/api/me/desktop-tokens", {
      method: "POST",
      json: { name },
    }),

};
