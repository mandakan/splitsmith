/** The per-stage calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import { authorKey } from "@/lib/authorKey";
import { type Pending, whenReady, type WhenReadyOptions } from "@/lib/pending";
import { AUTHOR_KEY_HEADER, failedJobError, request, scopeRequestPath } from "./client";
import type {
  BeepSnapResult,
  BeepSnippetPeaks,
  BulkCameraSetRequest,
  CameraMount,
  CoachShot,
  CoachShotPatch,
  CoachStageResponse,
  Comment,
  CommentCreateInput,
  CommentListResponse,
  ExportStageRequestPayload,
  Job,
  MatchProject,
  PeaksResult,
  Spectrogram,
  StageAudit,
  StageEvent,
  TriageResponse,
} from "../api";

export const stagesApi = {
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

  /** Submit the audit trim job for a specific video. A trim the cache
   *  holds as current is reused unless ``force``, which re-cuts it and its
   *  audit audio (the Footage sheet's Advanced "Re-cut trim"). */
  trimForVideo: (
    slug: string,
    stageNumber: number,
    videoId: string,
    force = false,
  ) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/trim${
        force ? "?force=true" : ""
      }`,
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
    // The snap reads the video's published WAV; in the rare case it is still
    // being made (spec 2026-10-14) the answer is 202 and this asks again.
    whenReady(
      () =>
        request<BeepSnapResult | Pending>(
          `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep/snap`,
          { method: "POST", json: { hint_time: hintTime, window_s: windowS } },
        ),
      { jobError: failedJobError },
    ),

  /** Submit an audit-mode short-GOP trim job. Returns a Job snapshot;
   *  idempotent on the worker side -- when the cached MP4 is fresh the
   *  job completes near-instantly without re-encoding. */
  trimStage: (slug: string, stageNumber: number) =>
    request<Job>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/trim`,
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

  /** Accept a stage's audit (mark ready). Returns the refreshed triage
   *  list - same contract as confirmBeepInQueue. */
  acceptStage: (slug: string, stageNumber: number) =>
    request<TriageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audit/accept`,
      { method: "POST" },
    ),

  stageAudioUrl: (slug: string, stageNumber: number) =>
    scopeRequestPath(`/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audio`),

  /** Per-video WAV URL. Primary forwards to the legacy stage audio
   *  endpoint (trimmed audit clip preferred); secondary serves the full
   *  per-cam WAV so the picker has the whole clip to scrub. */
  videoAudioUrl: (slug: string, stageNumber: number, videoId: string) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/audio`,
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

  /** The stage's audit-clip peaks. A WAV still being made answers 202
   *  (spec 2026-10-14); this asks again until it is ready, calling
   *  ``opts.onPending`` once so the page can say "Preparing audio". */
  getStagePeaks: (slug: string, stageNumber: number, bins = 1200, opts?: WhenReadyOptions) =>
    whenReady(
      () =>
        request<PeaksResult | Pending>(
          `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/peaks?bins=${bins}`,
        ),
      { jobError: failedJobError, ...opts },
    ),

  /** Per-video peaks. Same payload shape as the stage endpoint so the
   *  waveform picker takes the same code path for every role. */
  getVideoPeaks: (
    slug: string,
    stageNumber: number,
    videoId: string,
    bins = 1200,
    /** One window of the WAV, source seconds (the beep picker's 1 ms detail). */
    window?: { start: number; end: number },
    opts?: WhenReadyOptions,
    /** A frequency band, ``LO-HI`` Hz (the beep step's filter); only with a window. */
    band?: string,
  ) =>
    whenReady(
      () =>
        request<PeaksResult | Pending>(
          `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/peaks?bins=${bins}`
            + (window ? `&start=${window.start}&end=${window.end}` : "")
            + (band ? `&band=${band}` : ""),
        ),
      { jobError: failedJobError, ...opts },
    ),

  /** The beep step's spectrogram row: one window of the video's own WAV. */
  getVideoSpectrum: (
    slug: string,
    stageNumber: number,
    videoId: string,
    window: { start: number; end: number },
    opts?: WhenReadyOptions,
  ) =>
    whenReady(
      () =>
        request<Spectrogram | Pending>(
          `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/spectrum?start=${window.start}&end=${window.end}`,
        ),
      { jobError: failedJobError, ...opts },
    ),

  /** URL for the desktop-pushed beep review audio snippet (mirror-only,
   *  slice 3). 404s until ``snippet_ready`` is true on the queue item. */
  beepSnippetAudioUrl: (slug: string, stageNumber: number, videoId: string) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-snippet/audio`,
    ),

  /** Peaks + candidates for the pushed beep review snippet. Fetched fresh
   *  every call - desktop rewrites the object in place when it
   *  regenerates the snippet, so caching would go stale. */
  getBeepSnippetPeaks: (slug: string, stageNumber: number, videoId: string) =>
    request<BeepSnippetPeaks>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-snippet/peaks`,
    ),

  /** Comments on a stage, newest-first per the server. The author-key
   *  header is optional here - a caller sending none gets ``mine: false``
   *  on every row, which is correct for a first-time reader. */
  listStageComments(slug: string, stage: number): Promise<CommentListResponse> {
    return request<CommentListResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments`,
      { headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },

  createStageComment(
    slug: string,
    stage: number,
    input: CommentCreateInput,
  ): Promise<Comment> {
    return request<Comment>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments`,
      { method: "POST", json: input, headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },

  deleteStageComment(slug: string, stage: number, id: string): Promise<void> {
    return request<void>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments/${encodeURIComponent(id)}`,
      { method: "DELETE", headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },

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

  /** Coach view: per-shot interval class + flags + notes (#161).
   *  ``stale=true`` means the rule disagrees with the stored class and the
   *  user can accept the recompute via reclassify. Returns null when the
   *  stage has no audit JSON yet. ``seed`` is Breakdown's alone: it asks
   *  the server to propose reload regions (and keep them). Every other
   *  reader leaves it off, so reading a stage never writes it. */
  getStageCoach: (slug: string, stageNumber: number, options: { seed?: boolean } = {}) =>
    request<CoachStageResponse | null>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/coach${options.seed ? "?seed=1" : ""}`,
    ),

  reclassifyStageCoach: (slug: string, stageNumber: number) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/coach/reclassify`,
      {
        method: "POST",
      },
    ),

  putStageEvents: (slug: string, stageNumber: number, events: StageEvent[], version: string | null | undefined) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/events`,
      { method: "PUT", json: { events, _version: version ?? null } },
    ),

  /** Write the stage note (#1376); "" clears it. ``version`` is the coach
   *  payload's ``_version``: a stale one is a 409 ``version_conflict``. */
  patchStageNote: (slug: string, stageNumber: number, stageNote: string, version: string | null | undefined) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/stage-note`,
      { method: "PATCH", json: { stage_note: stageNote, _version: version ?? null } },
    ),

  /** Patch one shot's coach annotation (#844).
   *
   *  Prefers the by-id route, which is immune to renumbering: ``shot_number``
   *  is positional, so an insert or delete anywhere earlier in the stage
   *  shifts it and an annotation composed against a stale number lands on
   *  the neighbouring shot. A shot the server could not give a usable id
   *  falls back to the positional route, guarded by ``expectedVersion`` --
   *  the server refuses the patch outright rather than applying it to
   *  whatever now sits at that index.
   *
   *  ``expectedVersion`` is deliberately *not* sent on the by-id route: an
   *  id does not renumber, so the guard buys nothing there and would 409
   *  whenever the client's version has legitimately moved on (an Undo
   *  re-patch after the first patch bumped the doc, say). Pass
   *  ``coach.version`` from the response the shot was read out of; each
   *  PATCH returns a fresh ``CoachStageResponse`` that replaces it. */
  patchStageShotCoach: (
    slug: string,
    stageNumber: number,
    shot: Pick<CoachShot, "id" | "shot_number">,
    patch: CoachShotPatch,
    expectedVersion?: number,
  ) => {
    const shots = `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/shots`;
    if (shot.id) {
      return request<CoachStageResponse>(
        `${shots}/by-id/${encodeURIComponent(shot.id)}/coach`,
        { method: "PATCH", json: patch },
      );
    }
    return request<CoachStageResponse>(`${shots}/${shot.shot_number}/coach`, {
      method: "PATCH",
      json:
        expectedVersion === undefined
          ? patch
          : { ...patch, expected_version: expectedVersion },
    });
  },

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
        // Spread, never a field list: a field listed by hand here is a field
        // a new option can be missing from (api.exportBodies.test.ts). An
        // ``undefined`` field is omitted by JSON.stringify, so the server's
        // default holds; ``null`` goes through as-is (``overlay_max_*``:
        // "match source"; ``secondary_video_ids``: exclude every cam).
        ...opts,
        write_trim: opts.write_trim ?? true,
        write_csv: opts.write_csv ?? true,
        write_fcpxml: opts.write_fcpxml ?? true,
        write_report: opts.write_report ?? true,
        write_overlay: opts.write_overlay ?? false,
        overlay_codec: opts.overlay_codec ?? "auto",
      },
    }),

};
