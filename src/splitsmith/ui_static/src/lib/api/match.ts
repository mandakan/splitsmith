/** The match-level calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import { request, scopeRequestPath } from "./client";
import type {
  AutoSyncStatus,
  BeepQueueResponse,
  BuildTrimCachesResult,
  CommentAuthorListResponse,
  CompareGridRequestPayload,
  CompareStageResponse,
  ConnectMatchResponse,
  CreateMatchManualBody,
  CreateMatchScoreboardBody,
  DesktopCommand,
  DesktopCommandList,
  DesktopTokenRevokeResponse,
  ExportOverview,
  Job,
  LabFixtureRecord,
  MatchExportRequestPayload,
  MatchProject,
  MatchStagesResponse,
  MergePlanResponse,
  MoveShooterResponse,
  ServerHealth,
  ShareInfo,
  ShareListResponse,
  ShooterListResponse,
  SortDecisions,
  SortImportResult,
  SortSummary,
  SortView,
  StageEditRow,
  StageEditSummary,
  SyncStatusResponse,
  TriageResponse,
  TriageSummaryResponse,
} from "../api";

export const matchApi = {
  /** Attach the bound match to a scoreboard match after creation (#598).
   *  Sets the link on the match + every shooter, then returns name-based
   *  proposals for the operator to confirm via ``reconcileScoreboardLinks``. */
  connectScoreboardMatch: (matchId: number, contentType: number) =>
    request<ConnectMatchResponse>("/api/match/scoreboard/connect", {
      method: "POST",
      json: { match_id: matchId, content_type: contentType },
    }),

  /** Apply confirmed shooter -> competitor links for the bound match (#598).
   *  Pins each shooter's ids and merges stage times; returns the refreshed
   *  roster. Rows the operator left unlinked should be omitted. */
  reconcileScoreboardLinks: (
    links: { slug: string; shooter_id: number; competitor_id: number }[],
  ) =>
    request<ShooterListResponse>("/api/match/scoreboard/reconcile", {
      method: "POST",
      json: { links },
    }),


  /** The bound match's canonical stage list (#521) -- the same document
   *  ``editMatchStages`` diffs a submission against. Load this before
   *  opening the stage editor; a shooter's ``project.stages`` is a
   *  different document (see ``MatchStageDefinition``). */
  getMatchStages: () => request<MatchStagesResponse>("/api/match/stages"),

  /** Add, remove, and rename stages on the bound match (#521). Send the
   *  full desired list; the server diffs it. Removing a stage releases its
   *  videos to unassigned and deletes its audit + derived caches on every
   *  shooter. Stage numbers are never reused, so removing one leaves a gap. */
  editMatchStages: (stages: StageEditRow[]) =>
    request<StageEditSummary>("/api/match/stages", {
      method: "PUT",
      json: { stages },
    }),

  startFootageSort: (sourceDir: string) =>
    request<{ scan_id: string }>("/api/match/footage-sort/scan", {
      method: "POST",
      json: { source_dir: sourceDir },
    }),

  /** Sort the videos the user picked one by one in the picker. */
  startFootageSortFiles: (paths: string[]) =>
    request<{ scan_id: string }>("/api/match/footage-sort/scan", {
      method: "POST",
      json: { source_paths: paths },
    }),

  listFootageSorts: () => request<SortSummary[]>("/api/match/footage-sort"),

  discardFootageSort: (scanId: string) =>
    request<SortSummary>(`/api/match/footage-sort/${encodeURIComponent(scanId)}`, { method: "DELETE" }),

  /** Sort every video that sits unassigned in any shooter's project; no
   *  folder (the Footage page's "Sort across shooters"). */
  startFootageSortUnassigned: () =>
    request<{ scan_id: string }>("/api/match/footage-sort/scan", {
      method: "POST",
      json: { unassigned: true },
    }),

  getFootageSort: (scanId: string) =>
    request<SortView>(`/api/match/footage-sort/${encodeURIComponent(scanId)}`),

  putFootageSortDecisions: (scanId: string, decisions: SortDecisions) =>
    request<SortView>(`/api/match/footage-sort/${encodeURIComponent(scanId)}/decisions`, {
      method: "PUT",
      json: decisions,
    }),

  /** Import the checked clips; with ``shooters`` only theirs, and the
   *  review stays open for the rest. */
  importFootageSort: (scanId: string, linkMode: "symlink" | "copy", shooters?: string[]) =>
    request<SortImportResult>(`/api/match/footage-sort/${encodeURIComponent(scanId)}/import`, {
      method: "POST",
      json: { link_mode: linkMode, shooters: shooters ?? null },
    }),

  footageSortStripUrl: (scanId: string, index: number) =>
    scopeRequestPath(`/api/match/footage-sort/${encodeURIComponent(scanId)}/thumbs/${index}/strip.jpg`),

  footageSortVideoUrl: (scanId: string, index: number) =>
    scopeRequestPath(`/api/match/footage-sort/${encodeURIComponent(scanId)}/clips/${index}/video`),

  footageSortThumbUrl: (scanId: string, index: number) =>
    scopeRequestPath(`/api/match/footage-sort/${encodeURIComponent(scanId)}/thumbs/${index}.jpg`),

  /** The event's own logo (the branding work): PNG, JPEG, WebP or SVG, 2 MB. */
  uploadEventLogo: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<{ event_logo: string | null }>("/api/match/branding/event-logo", { method: "POST", body: form });
  },
  removeEventLogo: () =>
    request<{ event_logo: string | null }>("/api/match/branding/event-logo", { method: "DELETE" }),

  /** Create a new match from the manual variant of the create-match form
   *  (#322). Scaffolds match.json + first shooter and binds. */
  createMatchManual: (body: CreateMatchManualBody) =>
    request<ServerHealth>("/api/match/create-manual", {
      method: "POST",
      json: body,
    }),

  listMatchShootersIn: (matchId: string) =>
    request<ShooterListResponse>(
      `/api/matches/${encodeURIComponent(matchId)}/match/shooters`,
    ),

  getProjectIn: (matchId: string, slug: string) =>
    request<MatchProject>(
      `/api/matches/${encodeURIComponent(matchId)}/shooters/${encodeURIComponent(slug)}/project`,
    ),

  getExportOverviewIn: (matchId: string, slug: string) =>
    request<ExportOverview>(
      `/api/matches/${encodeURIComponent(matchId)}/shooters/${encodeURIComponent(slug)}/exports/overview`,
    ),

  /** How each row's existing fixture compares with its stage's audit now
   *  (lib/promoteDrift); ``null`` where there is nothing to compare. */
  promoteDriftIn: (
    matchId: string,
    rows: Array<{ shooter_slug: string; stage_number: number; slug: string }>,
  ) =>
    request<{ drift: Array<import("@/lib/promoteDrift").AuditDrift | null> }>(
      `/api/matches/${encodeURIComponent(matchId)}/lab/promote-drift`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) },
    ),
  promoteFixtureIn: (
    matchId: string,
    payload: {
      stage_number: number;
      /** Fixture slug -- names the promoted files (``stage-shots-...``). */
      slug: string;
      /** Registry slug of the shooter whose project the stage lives in.
       *  Distinct from ``slug`` since the match/shooter split. */
      shooter_slug: string;
      overwrite?: boolean;
    },
  ) =>
    request<LabFixtureRecord>(
      `/api/matches/${encodeURIComponent(matchId)}/lab/promote`,
      { method: "POST", json: payload },
    ),

  /** Queue trim-cache rebuild jobs for every missing-but-rebuildable stage
   *  in the named shooter's project. Works against the shooter's project
   *  root directly, so it does not change which shooter the server is
   *  currently bound to. (#351) */
  buildShooterTrimCaches: (slug: string) =>
    request<BuildTrimCachesResult>(
      `/api/match/shooters/${encodeURIComponent(slug)}/build-trim-caches`,
      { method: "POST" },
    ),

  /** Add a new shooter to the bound match (#324). When the match is
   *  scoreboard-linked, pass ``selected_shooter_id`` + ``selected_competitor_id``
   *  to bind the roster pick in one step (#598). */
  addMatchShooter: (body: {
    name: string;
    division?: string | null;
    selected_shooter_id?: number | null;
    selected_competitor_id?: number | null;
  }) =>
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

  /** Build a streaming URL for one shooter's lossless trim (#328).
   *  Pass ``kind="proxy"`` to request the fast-scrub proxy variant. */
  shooterVideoStreamUrl: (slug: string, path: string, kind?: "auto" | "proxy"): string => {
    const base = `/api/match/shooters/${encodeURIComponent(slug)}/videos/stream?path=${encodeURIComponent(path)}`;
    const url = kind && kind !== "auto" ? `${base}&kind=${kind}` : base;
    return scopeRequestPath(url);
  },

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

  /** Fetch the triage grid for the current match. Returns the list of
   *  cells and the count of flagged stages. */
  getTriage: () =>
    request<TriageResponse>("/api/match/triage"),

  /** Cheap poll for the flagged-count badge alone, without the full
   *  triage cell list ``getTriage`` returns. Use this for the sidebar /
   *  mobile-drawer badge; use ``getTriage`` only where the cells
   *  themselves are rendered (the Triage page). */
  getTriageSummary: () =>
    request<TriageSummaryResponse>("/api/match/triage/summary"),

  /** Create a new match from a picked scoreboard match (#322). The SPA
   *  follows up with /api/scoreboard/fetch + /select-shooter to populate
   *  stages and pin the primary competitor. */
  createMatchFromScoreboard: (body: CreateMatchScoreboardBody) =>
    request<ServerHealth>("/api/match/create-from-scoreboard", {
      method: "POST",
      json: body,
    }),

  /** Download URL for a match-level deliverable -- the compare grid MP4
   *  (#755). ``filename`` is ``CompareGridResult.output_name``. */
  matchExportFileUrl: (filename: string) =>
    scopeRequestPath(`/api/match/exports/file/${encodeURIComponent(filename)}`),

  /** Per-author detail across the whole match. Owner-only -- an
   *  anonymous share caller gets a 404. */
  listCommentAuthors(): Promise<CommentAuthorListResponse> {
    return request<CommentAuthorListResponse>("/api/match/comment-authors");
  },

  /** Render every shooter on the bound match into one grid MP4 (phase 0).
   *  Local mode only -- no Storage writes, no download deliverable, no
   *  export history. Job-queued: a full-match grid re-encode runs for
   *  minutes. Returns a Job snapshot; poll via {@link api.pollJob} until
   *  terminal, then read the {@link CompareGridResult} from
   *  ``Job.result``. */
  exportCompareGrid: (payload: CompareGridRequestPayload) =>
    request<Job>("/api/match/compare-export", {
      method: "POST",
      json: {
        // Spread, never a field list: the hand-written list this replaced
        // silently dropped every card, the overlay and the summary hold
        // (api.exportBodies.test.ts). ``undefined`` fields are omitted.
        ...payload,
      },
    }),

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

  // Share-link management (hosted-only). These ride scopeRequestPath so
  // /api/match/shares rewrites to /api/matches/{id}/match/shares on the owner
  // route. IMPORTANT: only call from the owner match URL (/match/:matchId/...).
  // On a /share/:token URL scopeRequestPath rewrites onto the share prefix and
  // the routes 404 - the ShareDialog mounts only when !shareToken.

  /** List share links for the current match. */
  listShares: () => request<ShareListResponse>("/api/match/shares"),

  /** Create a new share link for the current match. */
  createShare: () =>
    request<ShareInfo>("/api/match/shares", { method: "POST" }),

  /** Set which camera each shooter starts on for one link's viewers;
   *  null follows the shooters' saved defaults. */
  setShareCameras: (shareId: string, cameras: Record<string, string> | null) =>
    request<ShareInfo>(`/api/match/shares/${encodeURIComponent(shareId)}/cameras`, {
      method: "PATCH",
      json: { cameras },
    }),

  /** Revoke (soft-delete) a share link by id. */
  revokeShare: (shareId: string) =>
    request<void>(`/api/match/shares/${encodeURIComponent(shareId)}`, {
      method: "DELETE",
    }),

  // Admin worker management. These paths are operator-global (/api/admin/...)
  // and do not ride the match-scope prefix.

  /** Revoke a desktop sync token by id. ``revoked`` is false for an
   *  unknown id or one owned by another user. */
  revokeDesktopToken: (tokenId: string) =>
    request<DesktopTokenRevokeResponse>(
      `/api/me/desktop-tokens/${encodeURIComponent(tokenId)}`,
      { method: "DELETE" },
    ),

  // Hosted-sync settings + match sync trigger (desktop-to-hosted sync MVP,
  // #631 Task 9/11). Local-only - every route 404s in hosted mode; the
  // SPA only mounts SyncCard when useDeploymentMode().mode === "local", so
  // that 404 should never be reachable from the UI. Settings are an
  // operator-global install setting (/api/settings/...), not match-scoped;
  // the trigger/status routes ride /api/match/... so scopeRequestPath
  // rewrites them onto the current match, same as listShares above.

  /** Poll the current match's sync status: configured?, when it last
   *  synced, whether it's stale, and any plan errors that would block
   *  a push. */
  getSyncStatus: () => request<SyncStatusResponse>("/api/match/sync/status"),

  /** Submit a ``sync_match`` job for the current match. 409
   *  ``sync_not_configured`` when no hosted-sync target is saved yet. */
  startSync: () => request<Job>("/api/match/sync", { method: "POST" }),
  /** Ask the desktop to run something for this match (#1100). Mirrors
   *  only; the same request while one is waiting returns that one. */
  requestDesktopCommand: (body: { kind: "shot_detect"; slug: string; stage_number: number }) =>
    request<DesktopCommand>("/api/match/desktop-commands", { method: "POST", json: body }),
  /** Ask the linked desktop to render this shooter's match video and
   *  upload it. Spreads the payload, as exportMatch does. */
  requestDesktopRender: (slug: string, payload: MatchExportRequestPayload) =>
    request<DesktopCommand>("/api/match/desktop-commands", {
      method: "POST",
      json: { kind: "render_upload", slug, args: { request: { ...payload } } },
    }),
  listDesktopCommands: () => request<DesktopCommandList>("/api/match/desktop-commands"),
  cancelDesktopCommand: (id: string) =>
    request<DesktopCommand>(`/api/match/desktop-commands/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  /** Upload a match deleted on hosted as a new copy: resets its sync
   *  state and starts a sync. */
  republishSync: () => request<Job>("/api/match/sync/republish", { method: "POST" }),
  /** Desktop auto-sync state for this match (spec 2026-09-27). */
  getAutoSync: () => request<AutoSyncStatus>("/api/match/sync/auto"),
  /** Changes only the fields sent; ``enabled: null`` restores the default
   *  (on once the match has synced). */
  setAutoSync: (patch: { enabled?: boolean | null; full_media?: boolean }) =>
    request<AutoSyncStatus>("/api/match/sync/auto", { method: "PUT", json: patch }),
};
