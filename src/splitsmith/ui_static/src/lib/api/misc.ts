/** The settings, looks, admin, system and the other calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import { notifyHostedAccountChanged, request } from "./client";
import type {
  AccessRequest,
  AdminAccount,
  CalibratedCameraModel,
  CheckFinding,
  CreateWorkerResponse,
  DetectionModelSettings,
  DevicePendingInfo,
  DeviceStartResponse,
  DeviceStatusResponse,
  DeviceUnlinkResponse,
  ExportPreset,
  ExportPresetBody,
  FontInfo,
  FsListing,
  GradeInfo,
  HostedSyncSettings,
  Job,
  LookInfo,
  OwnFontInfo,
  ScoreboardMatchData,
  ServerHealth,
  StoredLook,
  StoredLookBody,
  TemplateEdit,
  TemplateInfo,
  TransitionFamilyInfo,
  WhatsNewPayload,
  WorkerListResponse,
  WorkerPatch,
  WorkerView,
  YouTubeConnectStart,
  YouTubeConnectStatus,
  YouTubePlaylist,
  YouTubeSettings,
} from "../api";

export const miscApi = {
  /** Directory-only listing without a bound project (#322). Used by the
   * create-match folder picker -- the create flow runs before any project
   * exists, so the shooter-scoped fs/list endpoint isn't reachable. */
  listFolderUnbound: (path?: string) => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<FsListing>(`/api/fs/list-dirs${qs}`);
  },

  /** Fetch full match data (incl. competitor list) without a bound
   *  shooter (#322). Used by the create-from-scoreboard flow to populate
   *  the multi-shooter picker before any project exists. */
  getScoreboardMatchDataUnbound: (contentType: number, matchId: number) =>
    request<ScoreboardMatchData>(
      `/api/scoreboard/matches/${contentType}/${matchId}`,
    ),

  /** List the camera models calibrated in the shipped artifact. The SPA
   *  presents these as the camera-model dropdown options on Ingest. */
  getCalibratedCameraModels: () =>
    request<{ models: CalibratedCameraModel[] }>(
      "/api/calibrated-camera-models",
    ),

  /** Server-side feature flags (fetched once on app mount). Surfaces:
   *  - ``lab`` -- hide the Lab nav entry unless ``splitsmith ui --lab``.
   *  - ``mode`` -- ``"local"`` vs ``"hosted"``; SPA suppresses host
   *    filesystem pickers / project-folder inputs in hosted mode. */
  getServerFeatures: () =>
    request<{ lab: boolean; mode: "local" | "hosted" }>("/api/server/features"),

  /** Whether the renderer (headless Chromium) launches here; local only. */
  getChromiumStatus: () => request<{ installed: boolean }>("/api/system/chromium"),

  /** Enqueue the ``chromium_install`` job (idempotent while one runs). */
  installChromium: () => request<Job>("/api/system/chromium/install", { method: "POST" }),

  /** Hosted mode -- start a magic-link sign-in: the server e-mails a link
   *  to ``email``. Always 200 (never reveals whether the address has an
   *  account); the account is created when the link is redeemed. */
  authBegin: (email: string) =>
    request<{ ok: true }>("/api/v1/auth/begin", {
      method: "POST",
      json: { email },
    }),

  /** Hosted mode -- ask for access. The server answers 202 with the same
   *  ``message`` whatever happened (known email or not). */
  requestAccess: (email: string, note: string | null) =>
    request<{ ok: boolean; message: string }>("/api/v1/access-requests", {
      method: "POST",
      json: { email, note },
    }),

  /** Hosted mode -- revoke the current session + clear the cookie. */
  authLogout: () =>
    request<{ ok: true }>("/api/v1/auth/logout", { method: "POST" }),

  /** Server health + bind state. The picker route polls this on mount
   *  to decide whether the user landed in unbound mode (boot with no
   *  ``--project``) or whether a project is already open. */
  getHealth: () => request<ServerHealth>("/api/health"),

  /** Open the OS file manager at ``path`` (selecting the file on macOS /
   *  Windows; opening the parent dir on Linux). The backend rejects paths
   *  outside the project root. */
  revealFile: (path: string) =>
    request<{ revealed: string }>("/api/files/reveal", {
      method: "POST",
      json: { path },
    }),

  /** List all registered compute workers. */
  adminListWorkers: () => request<WorkerListResponse>("/api/admin/workers"),

  /** Register a new self-hosted worker. Returns the worker plus a one-time
   *  registration token and the docker command to run. */
  adminCreateWorker: (name: string, priority: number) =>
    request<CreateWorkerResponse>("/api/admin/workers", {
      method: "POST",
      json: { name, priority },
    }),

  /** Update a worker's name, priority, or enabled state. */
  adminUpdateWorker: (id: string, patch: WorkerPatch) =>
    request<WorkerView>(`/api/admin/workers/${encodeURIComponent(id)}`, {
      method: "PATCH",
      json: patch,
    }),

  /** Delete a worker. 204 on success, 404 for unknown, 400 for the
   *  platform (Cloud Run) worker, which cannot be deleted via the API. */
  adminDeleteWorker: (id: string) =>
    request<void>(`/api/admin/workers/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  /** Access requests, pending first, then newest request first (hosted
   *  admin only; 404 locally, 403 for anyone else). */
  adminAccessRequests: () => request<AccessRequest[]>("/api/admin/access-requests"),

  /** Approve a pending request with ``tier``. 409 when already decided,
   *  404 "not found" / "account deleted", 422 for an unknown tier. */
  adminApproveAccessRequest: (id: string, tier: string) =>
    request<AccessRequest>(`/api/admin/access-requests/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      json: { tier },
    }),

  /** Decline a pending request. 409 when it is not pending. */
  adminDeclineAccessRequest: (id: string) =>
    request<AccessRequest>(`/api/admin/access-requests/${encodeURIComponent(id)}/decline`, {
      method: "POST",
    }),

  /** Send an approved request's sign-in mail again. 409 unless approved. */
  adminResendAccessRequest: (id: string) =>
    request<AccessRequest>(`/api/admin/access-requests/${encodeURIComponent(id)}/resend`, {
      method: "POST",
    }),

  /** Every account with its access tier. */
  adminUsers: () => request<AdminAccount[]>("/api/admin/users"),

  /** Move an account to ``tier``. 422 for an unknown tier, 404 unknown id. */
  adminSetUserTier: (id: string, tier: string) =>
    request<AdminAccount>(`/api/admin/users/${encodeURIComponent(id)}`, {
      method: "PATCH",
      json: { access_tier: tier },
    }),

  /** Set how many exports an account may run at once; ``null`` clears it
   *  back to the tier's number. */
  adminSetUserExports: (id: string, value: number | null) =>
    request<AdminAccount>(`/api/admin/users/${encodeURIComponent(id)}`, {
      method: "PATCH",
      json: { concurrent_exports: value },
    }),

  /** Read the current hosted-sync target. */
  getSyncSettings: () => request<HostedSyncSettings>("/api/settings/hosted-sync"),

  // YouTube (issue #1000): the install's one channel locally, the
  // account's one channel hosted (phase 2). Same routes in both modes;
  // the server decides what a login is.

  getYouTubeSettings: () => request<YouTubeSettings>("/api/settings/youtube"),

  // Export presets (spec 2026-09-15 s1): both modes, per user hosted.

  getExportPresets: () => request<{ presets: ExportPreset[] }>("/api/settings/export-presets"),
  /** What's new: the entries and what this user has seen. */
  getWhatsNew: () => request<WhatsNewPayload>("/api/whats-new"),
  /** Mark entries (or ``chip:<key>``) seen; answers the new state. */
  markWhatsNewSeen: (ids: string[]) =>
    request<WhatsNewPayload>("/api/whats-new/seen", { method: "POST", json: { ids } }),

  /** The shipped colour grade presets, each with its thumbnail. */
  listGrades: () => request<{ presets: GradeInfo[] }>("/api/grades"),
  /** Your own LUTs (desktop only; a 404 hosted). */
  listUserGrades: () => request<{ user: GradeInfo[] }>("/api/grades/user"),
  /** Keep a .cube of your own (desktop only): checked and named by its bytes. */
  uploadGrade: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<GradeInfo>("/api/grades", { method: "POST", body: form });
  },
  /** The installed Looks with their slots, variants and previews (#1246). */
  listLooks: () =>
    request<{ looks: LookInfo[]; transitions?: TransitionFamilyInfo[]; fonts?: FontInfo[] }>("/api/looks"),
  /** The caller's own Looks (#1263, #1264); a shipped Look is a 404 here. */
  getLook: (name: string) => request<StoredLook>(`/api/looks/${encodeURIComponent(name)}`),
  putLook: (name: string, body: StoredLookBody) =>
    request<StoredLook>(`/api/looks/${encodeURIComponent(name)}`, { method: "PUT", json: body }),
  deleteLook: (name: string) => request<void>(`/api/looks/${encodeURIComponent(name)}`, { method: "DELETE" }),
  /** Store a brand logo in the Look (local only): PNG, JPEG, WebP or SVG up to 2 MB. */
  uploadBrandLogo: (name: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<{ logo: string; url: string }>(`/api/looks/${encodeURIComponent(name)}/brand-logo`, {
      method: "POST",
      body: form,
    });
  },
  /** A Look's own font files (#1272), local only. */
  listOwnFonts: (name: string) => request<OwnFontInfo[]>(`/api/looks/${encodeURIComponent(name)}/fonts`),
  /** Upload a TTF or OTF of at most 2 MB into the Look; the server sniffs
   *  it and names the file by its content. */
  uploadOwnFont: (name: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<OwnFontInfo>(`/api/looks/${encodeURIComponent(name)}/fonts`, { method: "POST", body: form });
  },
  /** The template editor (#1265), local only. */
  listTemplates: (name: string) =>
    request<{ templates: TemplateInfo[]; starters: { name: string; content: string }[] }>(
      `/api/looks/${encodeURIComponent(name)}/templates`,
    ),
  saveTemplate: (name: string, edit: TemplateEdit) =>
    request<TemplateInfo>(`/api/looks/${encodeURIComponent(name)}/templates`, { method: "PUT", json: edit }),
  templateSamples: (name: string, slot: string, variant: string) =>
    request<{ cases: { case: string; context: Record<string, unknown> }[] }>(
      `/api/looks/${encodeURIComponent(name)}/samples?slot=${encodeURIComponent(slot)}&variant=${encodeURIComponent(variant)}`,
    ),
  checkLook: (name: string, draft: StoredLookBody | null, templates: TemplateEdit[]) =>
    request<{ items: CheckFinding[]; errors: number; warnings: number }>(
      `/api/looks/${encodeURIComponent(name)}/check`,
      { method: "POST", json: { draft, templates } },
    ),
  /** Your Look's unedited copies of an older shipped template (local only). */
  outdatedLookTemplates: (name: string) =>
    request<{ files: string[] }>(`/api/looks/${encodeURIComponent(name)}/outdated`),
  /** Drop those copies so the cards draw the current shipped templates. */
  refreshLookTemplates: (name: string) =>
    request<{ files: string[] }>(`/api/looks/${encodeURIComponent(name)}/refresh`, { method: "POST" }),
  revealLook: (name: string) =>
    request<{ revealed: string }>(`/api/looks/${encodeURIComponent(name)}/reveal`, { method: "POST" }),
  duplicateLook: (name: string, source: string, label?: string) =>
    request<StoredLook>(`/api/looks/${encodeURIComponent(name)}/duplicate`, {
      method: "POST",
      json: label ? { source, label } : { source },
    }),

  /** ``id === "new"`` creates; any other id replaces. Built-in ids 403. */
  putExportPreset: (id: string, name: string, body: ExportPresetBody) =>
    request<ExportPreset>(`/api/settings/export-presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      json: { name, body },
    }),

  deleteExportPreset: (id: string) =>
    request<void>(`/api/settings/export-presets/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** Start a login. The SPA opens ``auth_url`` itself and polls
   *  {@link api.youtubeConnectStatus} until it leaves ``pending``. */
  startYouTubeConnect: () =>
    request<YouTubeConnectStart>("/api/settings/youtube/connect/start", { method: "POST" }),

  youtubeConnectStatus: () => request<YouTubeConnectStatus>("/api/settings/youtube/connect/status"),

  /** The connected channel's playlists, for the Export page's picker.
   *  409 when not connected, 429 when the quota is spent. */
  getYouTubePlaylists: () => request<{ playlists: YouTubePlaylist[] }>("/api/settings/youtube/playlists"),

  /** Forget the stored login (revokes upstream, best effort). */
  disconnectYouTube: () =>
    request<{ connected: boolean }>("/api/settings/youtube/session", { method: "DELETE" }),

  /** Save the hosted-sync target. ``token: null`` keeps whatever token is
   *  already stored so the caller can resubmit ``baseUrl`` alone without
   *  re-typing the secret; ``""`` clears it; any other string replaces it. */
  putSyncSettings: async (baseUrl: string, token: string | null) => {
    const settings = await request<HostedSyncSettings>("/api/settings/hosted-sync", {
      method: "PUT",
      json: { base_url: baseUrl, token },
    });
    notifyHostedAccountChanged();
    return settings;
  },

  /** Begin a browser-assisted link to the hosted account (#719). 409
   *  ``hosted_base_url_not_set`` when no hosted target is configured.
   *  A login already in flight is not an error: the still-live flow
   *  comes back with ``resumed: true``. */
  startDeviceLogin: () =>
    request<DeviceStartResponse>("/api/settings/hosted-sync/device/start", {
      method: "POST",
    }),

  /** Poll the in-flight device login. Safe to call on a short interval --
   *  the local server throttles the upstream forward to the hosted
   *  interval, so this never trips ``slow_down``. */
  getDeviceStatus: () =>
    request<DeviceStatusResponse>("/api/settings/hosted-sync/device/status"),

  /** Unlink: revoke upstream, then clear the local token and account. */
  unlinkHostedAccount: async () => {
    const resp = await request<DeviceUnlinkResponse>("/api/settings/hosted-sync/session", {
      method: "DELETE",
    });
    notifyHostedAccountChanged();
    return resp;
  },

  setGlobalAutoSync: (enabled: boolean) =>
    request<{ global_enabled: boolean }>("/api/settings/auto-sync", {
      method: "PUT",
      json: { enabled },
    }),
  /** The machine's shot-detection model set and whether its files are here (local only). */
  getDetectionModel: () => request<DetectionModelSettings>("/api/settings/detection-model"),
  /** Choose a model set; choosing ``clap`` starts its download when the files are missing. */
  putDetectionModel: (modelSet: "base" | "clap") =>
    request<DetectionModelSettings>("/api/settings/detection-model", {
      method: "PUT",
      json: { model_set: modelSet },
    }),
  getScrubSettings: () => request<{ full_res_scrub: boolean }>("/api/settings/scrub"),
  setScrubSettings: (fullRes: boolean) =>
    request<{ full_res_scrub: boolean }>("/api/settings/scrub", {
      method: "PUT",
      json: { full_res_scrub: fullRes },
    }),

  // Device-flow approval screen (hosted browser side, #719). The desktop
  // install polls elsewhere; these three drive the operator-facing
  // /desktop/approve page.

  /** Load the approval screen's data. 404 for an unknown, already-decided
   *  or expired code -- the screen renders one message for all three. */
  getDevicePending: (userCode: string) =>
    request<DevicePendingInfo>(`/api/device/pending/${encodeURIComponent(userCode)}`),

  /** Approve the device. Mints nothing -- the desktop install's next poll
   *  collects the credential. */
  approveDevice: (userCode: string) =>
    request<{ approved: boolean }>(
      `/api/device/pending/${encodeURIComponent(userCode)}/approve`,
      { method: "POST" },
    ),

  /** Deny the device. */
  denyDevice: (userCode: string) =>
    request<{ approved: boolean }>(
      `/api/device/pending/${encodeURIComponent(userCode)}/deny`,
      { method: "POST" },
    ),
};
