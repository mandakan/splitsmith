/** The per-shooter calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import { ApiError, request, scopeRequestPath } from "./client";
import type {
  CleanupApplyResponse,
  CleanupCategory,
  CleanupPlan,
  CoachMatchDistributions,
  CoverageSuggestion,
  ExportOverview,
  ExportPreviewBody,
  ExportRun,
  FsListing,
  FsProbeResponse,
  HighlightReadiness,
  HighlightReelBody,
  HitlQueueResponse,
  Job,
  LatestReel,
  LinkStatusResponse,
  MatchExportRequestPayload,
  MatchProject,
  PeaksResult,
  RawVideoManifestEntry,
  RelinkApplyResponse,
  RelinkScanResponse,
  RemoveVideoResponse,
  ResolvedAutomationResponse,
  Rgb,
  ScanResponse,
  ScoreboardMatchData,
  ShooterListResponse,
  TakeOverview,
  VideoRole,
  YouTubeUploadPayload,
} from "../api";

export const shootersApi = {
  getProject: (slug: string) =>
    request<MatchProject>(`/api/shooters/${encodeURIComponent(slug)}/project`),

  listFolder: (slug: string, path?: string, opts?: { probe?: boolean }) => {
    const params = new URLSearchParams();
    if (path) params.set("path", path);
    if (opts?.probe) params.set("probe", "true");
    const qs = params.toString();
    return request<FsListing>(
      `/api/shooters/${encodeURIComponent(slug)}/fs/list${qs ? `?${qs}` : ""}`,
    );
  },

  probeFile: (slug: string, path: string) =>
    request<FsProbeResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/fs/probe?path=${encodeURIComponent(path)}`,
    ),

  /** Repair a single take damaged by the old stage-1 bug (see
   *  ``lib/takeDamage``): its damaged stages are re-registered and their
   *  beeps re-detected; undamaged stages are untouched. */
  repairTake: (slug: string, filename: string) =>
    request<{ repaired_stages: number[]; project: MatchProject }>(
      `/api/shooters/${encodeURIComponent(slug)}/raw-videos/repair`,
      { method: "POST", json: { filename } },
    ),

  removeVideo: (slug: string, videoPath: string, resetAudit = false, stageNumber?: number | null) =>
    request<RemoveVideoResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/videos/remove`,
      {
        method: "POST",
        json: { video_path: videoPath, reset_audit: resetAudit, stage_number: stageNumber ?? null },
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

  /** ``includeUnrebuildable`` carries the dialog's per-item consent to the
   *  server, which otherwise holds back every item whose own input is
   *  already gone (#926). It defaults to false so the safe direction is
   *  what a caller gets by saying nothing. */
  applyCleanup: (
    slug: string,
    categories: CleanupCategory[],
    includeUnrebuildable = false,
  ) =>
    request<CleanupApplyResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/project/cleanup`,
      {
        method: "POST",
        json: { categories, include_unrebuildable: includeUnrebuildable },
      },
    ),

  /** Resolve the current source's ``MatchData``. The SPA uses this to map
   *  a picked ``shooterId`` to a per-match ``competitor_id`` before
   *  pinning. 404 when the project has no match loaded. */
  getScoreboardMatchData: (slug: string) =>
    request<ScoreboardMatchData>(
      `/api/shooters/${encodeURIComponent(slug)}/scoreboard/match-data`,
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

  /** ``fromStageNumber`` names the registration that moves: a multi-stage
   *  single take registers one file on several stages. Leave it off (or
   *  null) for a tray item. */
  moveAssignment: (
    slug: string,
    videoPath: string,
    toStageNumber: number | null,
    role: VideoRole = "secondary",
    fromStageNumber?: number | null,
  ) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/assignments/move`,
      {
        method: "POST",
        json: {
          video_path: videoPath,
          to_stage_number: toStageNumber,
          role,
          from_stage_number: fromStageNumber ?? null,
        },
      },
    ),

  /** Promote ``videoPath`` to primary on ``stageNumber``. The server
   *  refuses with a 409 (``code: "audit_exists"``) when the stage has
   *  shots in its audit JSON and ``confirm`` is false; the SPA should
   *  prompt then re-call with ``confirm=true``. On confirm, the existing
   *  audit JSON is kept as ``stage<N>.pre-swap-<time>.json`` and the
   *  stage's trim and shots are redone on the new primary (a confirmed
   *  beep on it is kept). */
  /** Persist which camera this shooter contributes to a compare grid and
   *  to trim-only exports. Accepts a ``camera_mount`` ("chest") or a role
   *  ("primary" / "secondary"); ``null`` clears back to the primary. The
   *  server rejects a selector that resolves on no stage with a 400, so a
   *  typo surfaces here rather than in a finished grid. */
  setCompareCamera: (slug: string, camera: string | null) =>
    request<MatchProject>(
      `/api/shooters/${encodeURIComponent(slug)}/compare-camera`,
      {
        method: "PATCH",
        json: { camera },
      },
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

  /** List every shooter in the currently-bound match (#324). */
  listMatchShooters: () =>
    request<ShooterListResponse>("/api/match/shooters"),

  /* Explicitly match-addressed variants for surfaces outside the
   * ``/match/:matchId/`` URL space (the dev-mode Lab). ``scopeRequestPath``
   * only rewrites bare ``/api/match/`` + ``/api/shooters/`` paths when the
   * browser URL carries a match prefix, and since #353 Tier 1 the server
   * has no process-level bind fallback -- so a dev-mode caller must name
   * the match in the request path itself. */
  /** The highlight reel's readiness and its moment menu for one shooter. */
  getHighlights: (slug: string) =>
    request<HighlightReadiness>(`/api/shooters/${encodeURIComponent(slug)}/highlights`),

  /** The newest highlight reel whose file is still there; rejects (404)
   *  without one. Reachable on a share link. */
  getLatestReel: (slug: string) =>
    request<LatestReel>(`/api/shooters/${encodeURIComponent(slug)}/highlights/latest`),

  /** Queue the highlight reel's render; the Job to poll. The body is spread,
   *  never listed (``api.exportBodies.test.ts``). */
  submitHighlightReel: (slug: string, body: HighlightReelBody) =>
    request<Job>(`/api/shooters/${encodeURIComponent(slug)}/highlights`, {
      method: "POST",
      json: { ...body },
    }),

  /** Stream URL for a registered video.
   *
   *  ``kind`` selects what the server serves and is encoded into the URL
   *  so the browser sees a different resource per kind. The audit screen
   *  passes ``trim`` or ``proxy`` explicitly so a background trim job
   *  completing mid-playback can't switch the file under an open
   *  ``<video>`` element (which fails its next Range request with
   *  "source not found"). Other callers default to ``auto``: trim if
   *  present, source otherwise. ``proxy`` serves the low-res fast-scrub
   *  proxy (or source on local mode); in hosted mode the server returns
   *  HTTP 425 when the proxy object is absent. ``web`` is the trim's
   *  streaming rendition, falling back to trim then source. ``scrub`` is
   *  the Audit players' pin: the fresh rendition, else the trim, else 404.
   *  ``stage`` names the stage the video belongs to: a multi-stage single
   *  take registers one source path on several stages, each with its own
   *  trim, and without it the server answers with the first stage's. Every
   *  per-stage caller passes it. */
  videoStreamUrl: (
    slug: string,
    videoPath: string,
    kind: "auto" | "trim" | "source" | "proxy" | "web" | "scrub" = "auto",
    version?: string | null,
    stage?: number | null,
  ) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/videos/stream?path=${encodeURIComponent(videoPath)}&kind=${kind}` +
        (version ? `&v=${encodeURIComponent(version)}` : "") +
        (stage != null && Number.isFinite(stage) ? `&stage=${stage}` : ""),
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

  /** Match-level histograms aggregated across every stage with an audit
   *  JSON. Stages without an audit are silently skipped server-side. */
  getMatchCoachDistributions: (slug: string) =>
    request<CoachMatchDistributions>(
      `/api/shooters/${encodeURIComponent(slug)}/coach/distributions`,
    ),

  /** Match-overview payload for the Analysis & Export screen. */
  getExportOverview: (slug: string) =>
    request<ExportOverview>(
      `/api/shooters/${encodeURIComponent(slug)}/exports/overview`,
    ),

  /** The shooter's export history, newest first (#629). Persistent: a
   *  record written at export time, not the in-session job result. */
  getExportRuns: (slug: string) =>
    request<{ runs: ExportRun[] }>(
      `/api/shooters/${encodeURIComponent(slug)}/exports/runs`,
    ),

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
        // Spread, never a field list: the hand-written list this replaced
        // silently dropped the title page, closing card and summary hold
        // (api.exportBodies.test.ts). ``undefined`` fields are omitted by
        // JSON.stringify, so the server's defaults hold for them.
        ...payload,
        head_pad_seconds: payload.head_pad_seconds ?? 5.0,
        tail_pad_seconds: payload.tail_pad_seconds ?? 5.0,
        include_secondaries: payload.include_secondaries ?? true,
        include_overlay: payload.include_overlay ?? true,
        overlay_codec: payload.overlay_codec ?? "auto",
      },
    }),

  /** Queue a ``youtube_upload`` job for one rendered MP4 in the shooter's
   *  exports. 409 when no channel is connected, 400 when
   *  the file is not an MP4 with its sidecar. Poll via {@link api.pollJob}. */
  uploadToYouTube: (slug: string, payload: YouTubeUploadPayload) =>
    request<Job>(`/api/shooters/${encodeURIComponent(slug)}/exports/youtube-upload`, {
      method: "POST",
      json: payload,
    }),

  /** The colours the palette suggestions are chosen against (#1273). */
  paletteSources: (slug: string, stageNumbers: number[]) =>
    request<{ footage: { rgb: Rgb; share: number }[]; average: Rgb | null; logo: { rgb: Rgb; share: number }[] }>(
      `/api/shooters/${encodeURIComponent(slug)}/palette-sources`,
      { method: "POST", json: { stage_numbers: stageNumbers } },
    ),
  /** The PNG for one card on one stage; rejects with an ApiError whose
   *  status the rail maps to a line (503 no browser, 409 no shots). */
  exportPreview: async (slug: string, body: ExportPreviewBody, signal?: AbortSignal): Promise<Blob> => {
    const resp = await fetch(scopeRequestPath(`/api/shooters/${encodeURIComponent(slug)}/export-preview`), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "image/png" },
      body: JSON.stringify(body),
      signal,
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const parsed = (await resp.json()) as { detail?: unknown };
        if (typeof parsed.detail === "string") detail = parsed.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(resp.status, detail);
    }
    return resp.blob();
  },

};
