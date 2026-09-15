/**
 * Export (/match/:id/export/:slug) -- spec 2026-09-13 s4.8.
 *
 * Mode / stages / options / summary, on the primitives. The output mode
 * is one segmented control: Timeline (FCPXML + CSV + report), Trims only
 * (one lossless cut per stage) and, on a multi-shooter match, Compare
 * grid (every shooter beep-aligned into one MP4 -- what the slug-less
 * Match export page used to do on its own). The stage picker is a table
 * where every stage that cannot export says why in one line and offers
 * the fix; the ladder lives in lib/exportPlan.ts, and hosted copy never
 * names a drive. The summary rail carries the page's one primary and,
 * in its footer, the storage actions (Reclaim space, Delete match).
 *
 * Mounted under <MatchShell />, so the chrome is shared with the other
 * match pages. Data plumbing (overview + project + runs load, the three
 * submit paths, the cleanup dialog reload) is unchanged from the
 * pre-restructure page.
 */

import { Download, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { CleanupDialog } from "@/components/CleanupDialog";
import { ExportHistory } from "@/components/export/ExportHistory";
import { SelectField } from "@/components/export/SelectField";
import { StageTable } from "@/components/export/StageTable";
import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import { CamOptionsPanel } from "@/components/render/CamOptionsPanel";
import { RenderOptionsPanel, Seconds } from "@/components/render/RenderOptionsPanel";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  capabilityDenied,
  READ_ONLY_MIRROR_MESSAGE,
  type CompareGridResult,
  type ExportOverview,
  type ExportRun,
  type Job,
  type MatchExportResult,
  type MatchProject,
  type OverlayCodec,
  type YouTubeSettings,
} from "@/lib/api";
import { camExportFields, DEFAULT_CAM_OPTIONS, syncedSecondaryCount, type CamOptions } from "@/lib/camOptions";
import { DEFAULT_UPLOAD_OPTIONS, rowUploadOptions, type UploadFormOptions } from "@/lib/youtubeRows";
import { hostedDownloads as buildHostedDownloads } from "@/lib/exportDownloads";
import {
  estimateDuration,
  exportRows,
  formatDuration,
  summaryLines,
  type ExportMode,
  type FixTarget,
} from "@/lib/exportPlan";
import { useDeploymentMode } from "@/lib/features";
import { useMatchHref } from "@/lib/matchHref";
import {
  DEFAULT_RENDER_OPTIONS,
  describeRenderOptions,
  matchExportFields,
  renderOptionsSeconds,
  type OutputFormat,
  type RenderOptions,
} from "@/lib/renderOptions";
import { cn } from "@/lib/utils";
import {
  buildCompareGridPayload,
  CANVAS_CHOICES,
  summarizeGridResult,
  type CanvasChoice,
} from "@/pages/matchExportModel";

type PaddingPreset = "full" | "action" | "highlight" | "custom";

/** What ``ui/match_exports.py`` names the timeline file per format. */
const BUNDLE_EXTENSION: Record<OutputFormat, string> = { fcpxml: ".fcpxml", fcp7xml: ".xml", mp4: ".mp4" };

const PADDING_PRESETS: Record<Exclude<PaddingPreset, "custom">, { label: string; head: number; tail: number }> = {
  full: { label: "Full", head: 5.0, tail: 5.0 },
  action: { label: "Action", head: 0.5, tail: 1.0 },
  highlight: { label: "Highlight", head: 1.5, tail: 2.0 },
};

type TransitionKind = "none" | "zoom" | "static";
const TRANSITIONS: { value: TransitionKind; label: string }[] = [
  { value: "none", label: "Hard cut" },
  { value: "static", label: "Static frame" },
  { value: "zoom", label: "Zoom blur" },
];

export function Export() {
  const { slug, matchId } = useParams<{ slug: string; matchId?: string }>();
  if (!slug) return <Navigate to={matchId ? `/match/${matchId}/ingest` : "/pick"} replace />;
  return <ExportInner slug={slug} />;
}

function ExportInner({ slug }: { slug: string }) {
  const { mode: deploymentMode } = useDeploymentMode();
  const hosted = deploymentMode === "hosted";
  const ctx = useOutletContext<MatchShellOutletContext>();
  const href = useMatchHref();
  const navigate = useNavigate();
  const confirm = useConfirm();
  // #756: gate on the server-derived capability, not this page's own
  // `project` (that's the per-shooter export overview, not the match
  // capability set) - a mirror match 403s every write here.
  const editDenied = capabilityDenied(ctx?.capabilities, "edit");
  const shooters = useMemo(() => ctx?.shooters ?? [], [ctx?.shooters]);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [overview, setOverview] = useState<ExportOverview | null>(null);
  const [runs, setRuns] = useState<ExportRun[]>([]);
  // The YouTube connection is an install-level setting the local server
  // owns; null until it answers (and always null hosted, where the
  // routes do not exist). The row renders nothing while null.
  const [youtubeSettings, setYoutubeSettings] = useState<YouTubeSettings | null>(null);
  const [uploadBusy, setUploadBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<MatchExportResult | null>(null);
  const [gridResult, setGridResult] = useState<CompareGridResult | null>(null);
  // Trims-only queues one job per stage instead of a single bundle job,
  // so it reports "N queued" here and hands progress to the jobs rail.
  const [queueing, setQueueing] = useState<boolean>(false);
  const [queuedNote, setQueuedNote] = useState<string | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState<boolean>(false);

  const reload = useCallback(async () => {
    try {
      const [proj, ov] = await Promise.all([api.getProject(slug), api.getExportOverview(slug)]);
      setProject(proj);
      setOverview(ov);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    // History is secondary to the page's purpose (#629) -- a failed fetch
    // here must never surface an error toast or block the form above.
    try {
      const { runs: fetchedRuns } = await api.getExportRuns(slug);
      setRuns(fetchedRuns);
    } catch {
      setRuns([]);
    }
  }, [slug]);

  const reloadYouTube = useCallback(async () => {
    if (hosted) return;
    try {
      setYoutubeSettings(await api.getYouTubeSettings());
    } catch {
      setYoutubeSettings(null);
    }
  }, [hosted]);

  useEffect(() => {
    void reloadYouTube();
  }, [reloadYouTube]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Trim jobs outlive a navigation away from this page; the post-run
  // refresh below checks this before touching state.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // === Form state ===
  const [mode, setMode] = useState<ExportMode>("single");
  const [selection, setSelection] = useState<Set<number>>(() => new Set());
  const [preset, setPreset] = useState<PaddingPreset>("full");
  const [headPad, setHeadPad] = useState<number>(PADDING_PRESETS.full.head);
  const [tailPad, setTailPad] = useState<number>(PADDING_PRESETS.full.tail);
  const [transitionKind, setTransitionKind] = useState<TransitionKind>("none");
  const [transitionDurationSeconds, setTransitionDurationSeconds] = useState<number>(0.5);
  const [includeOverlay, setIncludeOverlay] = useState<boolean>(false);
  const [overlayCodec, setOverlayCodec] = useState<OverlayCodec>("auto");
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("fcpxml");
  const [projectName, setProjectName] = useState<string>("");
  // The generated cards and the summary hold (#973, #972) are one piece
  // of state shared by the timeline and the grid: switching mode keeps a
  // title page the user already set up. Which of them a format can draw
  // is the panel's and the mappers' business, never this page's.
  const [renderOptions, setRenderOptions] = useState<RenderOptions>(DEFAULT_RENDER_OPTIONS);
  const [camOptions, setCamOptions] = useState<CamOptions>(DEFAULT_CAM_OPTIONS);
  // The YouTube encode preset and the upload sidecar (description with
  // chapters, captions) travel together: one is pointless without the
  // other and both exist only for the rendered MP4.
  const [youtube, setYoutube] = useState<boolean>(false);
  const [descriptionLead, setDescriptionLead] = useState<string>("");
  // One privacy control per page: the history rows upload with it too.
  const [uploadOptions, setUploadOptions] = useState<UploadFormOptions>(DEFAULT_UPLOAD_OPTIONS);
  // Compare grid: the reference shooter sets the frame rate; the canvas
  // sets the render size; the overlay and its summary hold are #705's.
  const [audioFrom, setAudioFrom] = useState<string>("");
  const [canvas, setCanvas] = useState<CanvasChoice>(CANVAS_CHOICES[0]);
  const [gridOverlay, setGridOverlay] = useState<boolean>(false);
  const [gridHoldSeconds, setGridHoldSeconds] = useState<number>(0);

  useEffect(() => {
    if (project && !projectName) setProjectName(project.name);
  }, [project, projectName]);
  useEffect(() => {
    if (!audioFrom && shooters.length > 0) setAudioFrom(shooters[0].slug);
  }, [shooters, audioFrom]);

  const trimsOnly = mode === "trims";
  const compare = mode === "compare";
  const multiShooter = shooters.length >= 2;
  const renderedMp4 = mode === "single" && outputFormat === "mp4";

  // Stage time lives on the project (overview rows don't carry it).
  const stageTimeByNumber = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of project?.stages ?? []) m.set(s.stage_number, s.time_seconds);
    return m;
  }, [project]);

  const rows = useMemo(
    () => exportRows(overview?.stages ?? [], stageTimeByNumber, mode, hosted),
    [overview, stageTimeByNumber, mode, hosted],
  );
  const eligibleNumbers = useMemo(() => rows.filter((r) => r.eligible).map((r) => r.stage.stage_number), [rows]);
  const eligibleSet = useMemo(() => new Set(eligibleNumbers), [eligibleNumbers]);

  // Pre-select all eligible stages on first load (and after a mode switch
  // empties the selection).
  useEffect(() => {
    if (eligibleNumbers.length > 0 && selection.size === 0) setSelection(new Set(eligibleNumbers));
  }, [eligibleNumbers, selection.size]);

  // Drop any stages that became ineligible.
  useEffect(() => {
    setSelection((prev) => {
      const next = new Set<number>();
      for (const n of prev) if (eligibleSet.has(n)) next.add(n);
      return next.size === prev.size ? prev : next;
    });
  }, [eligibleSet]);

  const toggleStage = useCallback(
    (n: number) => {
      if (!eligibleSet.has(n)) return;
      setSelection((prev) => {
        const next = new Set(prev);
        if (next.has(n)) next.delete(n);
        else next.add(n);
        return next;
      });
    },
    [eligibleSet],
  );

  const orderedSelection = useMemo(
    () => rows.map((r) => r.stage.stage_number).filter((n) => selection.has(n)),
    [rows, selection],
  );

  // Secondaries with a confirmed beep on the selected stages: what the
  // cam rows offer, and zero is what hides them.
  const secondaryCount = useMemo(
    () => syncedSecondaryCount(project?.stages ?? [], orderedSelection),
    [project, orderedSelection],
  );

  // Switching mode changes which stages are exportable, so drop the
  // selection and let the pre-select effect refill it from the new
  // eligible set rather than leaving the previous mode's picks behind.
  const selectMode = useCallback((next: ExportMode) => {
    setMode(next);
    setSelection(new Set());
    setResult(null);
    setGridResult(null);
    setQueuedNote(null);
  }, []);

  // Selectors the server's ``camera_select.validate_camera`` will accept:
  // mounts tagged on this shooter's videos, plus whichever roles are
  // actually present. Offering anything else would just earn a 400.
  const cameraSelectors = useMemo(() => {
    const found = new Set<string>();
    for (const stage of project?.stages ?? []) {
      if (stage.skipped) continue;
      for (const v of stage.videos) {
        if (v.role === "ignored") continue;
        if (v.camera_mount) found.add(v.camera_mount);
        if (v.role === "primary" || v.role === "secondary") found.add(v.role);
      }
    }
    return [...found].sort();
  }, [project]);

  // The persisted ``compare_camera`` can name a selector this project no
  // longer offers; carry the orphan as its own option, labelled, so the
  // picker stays truthful (see #761).
  const cameraOptions = useMemo(() => {
    const current = project?.compare_camera ?? null;
    const options = [{ value: "", label: "Primary (default)" }, ...cameraSelectors.map((s) => ({ value: s, label: s }))];
    if (current !== null && !cameraSelectors.includes(current)) {
      options.push({ value: current, label: `${current} (not on this shooter)` });
    }
    return options;
  }, [cameraSelectors, project]);

  async function changeCamera(value: string) {
    if (editDenied) return;
    const next = value || null;
    if (next === (project?.compare_camera ?? null)) return;
    setError(null);
    try {
      setProject(await api.setCompareCamera(slug, next));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  // #629: every input to this is persistent -- see ``lib/exportDownloads``.
  const hostedDownloads = useMemo(() => {
    if (!hosted) return [];
    return buildHostedDownloads({
      matchExports: overview?.match_exports ?? [],
      stages: overview?.stages ?? [],
      selection: orderedSelection,
    });
  }, [hosted, overview, orderedSelection]);

  function selectPreset(next: PaddingPreset) {
    setPreset(next);
    if (next !== "custom") {
      setHeadPad(PADDING_PRESETS[next].head);
      setTailPad(PADDING_PRESETS[next].tail);
    }
  }

  const cardSeconds =
    mode === "trims"
      ? 0
      : renderOptionsSeconds(
          renderOptions,
          orderedSelection.length,
          compare ? "grid" : "single",
          compare ? "mp4" : outputFormat,
        ) + (compare && gridOverlay ? Math.max(0, gridHoldSeconds || 0) * orderedSelection.length : 0);
  const duration = estimateDuration(orderedSelection, stageTimeByNumber, {
    mode,
    head: mode === "single" ? headPad : (project?.trim_pre_buffer_seconds ?? 0),
    tail: mode === "single" ? tailPad : (project?.trim_post_buffer_seconds ?? 0),
    transitionKind,
    transitionSeconds: transitionDurationSeconds,
    cardSeconds,
  });

  const busy = job?.status === "pending" || job?.status === "running" || queueing;
  const canExport =
    !busy && orderedSelection.length > 0 && !!project && !editDenied && (!compare || audioFrom !== "");

  /** Queue one trim-only job per selected stage through the per-stage
   *  export endpoint. The write flags are literals rather than the
   *  section state: the overlay / format controls are hidden in this
   *  mode, and a stale toggle must never turn a bare trim back into a
   *  full bundle. Progress lives in the jobs rail from here on. */
  async function submitTrims() {
    setQueueing(true);
    // The endpoint returns the *existing* job when one is already active
    // for a stage rather than queueing a second one -- and that job may be
    // a full-bundle export with entirely different flags. Report the two
    // outcomes separately.
    let active = new Set<number>();
    try {
      const running = await api.listJobs();
      active = new Set(
        running
          .filter(
            (j) =>
              j.kind === "export" &&
              j.shooter_slug === slug &&
              j.stage_number !== null &&
              (j.status === "pending" || j.status === "running"),
          )
          .map((j) => j.stage_number as number),
      );
    } catch {
      // Advisory only -- a failed pre-check must not block the export.
    }

    const queuedIds: string[] = [];
    let attached = 0;
    try {
      for (const stageNumber of orderedSelection) {
        const submitted = await api.exportStage(slug, stageNumber, {
          write_trim: true,
          write_csv: false,
          write_fcpxml: false,
          write_report: false,
          write_overlay: false,
        });
        if (active.has(stageNumber)) attached += 1;
        queuedIds.push(submitted.id);
      }
      const queued = queuedIds.length - attached;
      setQueuedNote(
        [
          `Queued ${queued} trim ${queued === 1 ? "job" : "jobs"}.`,
          attached > 0
            ? ` ${attached} ${attached === 1 ? "stage" : "stages"} already had an export running -- that job was joined, not replaced.`
            : "",
          " Progress shows under the top bar.",
        ].join(""),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      if (queuedIds.length > 0) setQueuedNote(`Queued ${queuedIds.length} of ${orderedSelection.length} trim jobs.`);
    } finally {
      setQueueing(false);
    }

    // This page's own state (has_exports, lossless_trim_present) is
    // written by those jobs; refresh once they settle.
    if (queuedIds.length > 0) {
      void Promise.allSettled(queuedIds.map((id) => api.pollJob(id, () => {}))).then(() => {
        if (mountedRef.current) void reload();
      });
    }
  }

  /** Upload one finished render from its history row. The job rides the
   *  same rail as an export; the history re-reads once it settles so the
   *  row picks up the sidecar's new record. */
  async function uploadRow(filename: string, again: boolean) {
    setUploadBusy(filename);
    setError(null);
    try {
      const submitted = await api.uploadToYouTube(slug, {
        filename,
        again,
        ...rowUploadOptions(uploadOptions),
      });
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "failed") setError(final.error ?? "Upload failed");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setUploadBusy(null);
      if (mountedRef.current) void reload();
    }
  }

  async function submitBundle() {
    if (!project) return;
    try {
      // The cam rows render only with a synced secondary on the selection;
      // without one the server's own default (cams on) changes nothing,
      // so the chosen value travels either way.
      const submitted = await api.exportMatch(slug, {
        stage_numbers: orderedSelection,
        head_pad_seconds: headPad,
        tail_pad_seconds: tailPad,
        ...camExportFields(camOptions),
        output_format: outputFormat,
        transition_kind: transitionKind,
        transition_duration_seconds: transitionDurationSeconds,
        ...matchExportFields(renderOptions, outputFormat),
        intro_path: undefined,
        outro_path: undefined,
        youtube_sidecar: renderedMp4 && youtube,
        description_lead: renderedMp4 && youtube ? descriptionLead.trim() || null : undefined,
        youtube_preset: renderedMp4 && youtube,
        youtube_upload: renderedMp4 && youtube && !!youtubeSettings?.connected && uploadOptions.enabled,
        youtube_privacy: rowUploadOptions(uploadOptions).privacy,
        youtube_playlist: rowUploadOptions(uploadOptions).playlist,
        youtube_playlist_id: rowUploadOptions(uploadOptions).playlist_id,
        youtube_publish_at: rowUploadOptions(uploadOptions).publish_at,
        youtube_notify_subscribers: rowUploadOptions(uploadOptions).notify_subscribers,
        include_overlay: includeOverlay,
        overlay_codec: overlayCodec,
        overlay_max_height: null,
        overlay_max_fps: null,
        project_name: projectName || project.name,
      });
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "succeeded" && final.result) {
        setResult(final.result as unknown as MatchExportResult);
        // The overview's match-export rows and the history come from the
        // server and would otherwise stay as they were before the run.
        void reload();
      } else if (final.status === "failed") {
        setError(final.error ?? "Export failed");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function submitGrid() {
    try {
      const payload = buildCompareGridPayload({
        stageNumbers: orderedSelection,
        audioFrom,
        canvas,
        outputName: "compare-grid",
        render: renderOptions,
        overlay: gridOverlay,
        summaryHoldSeconds: gridHoldSeconds,
      });
      const submitted = await api.exportCompareGrid(payload);
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "succeeded" && final.result) {
        setGridResult(final.result as unknown as CompareGridResult);
      } else if (final.status === "failed") {
        setError(final.error ?? "Render failed.");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function submitExport() {
    if (!canExport || !project) return;
    setError(null);
    setResult(null);
    setGridResult(null);
    setQueuedNote(null);
    if (trimsOnly) await submitTrims();
    else if (compare) await submitGrid();
    else await submitBundle();
  }

  async function reveal(path: string) {
    try {
      await api.revealFile(path);
    } catch {
      // Reveal is non-critical
    }
  }

  async function deleteMatch() {
    const matchRoot = ctx?.health?.project_root;
    const matchName = ctx?.project?.name ?? project?.name ?? "this match";
    if (!matchRoot) return;
    // Mode-specific opt-in extras. Desktop can wipe the folder on disk;
    // hosted can additionally drop raw uploads that fed only this match.
    const checkboxes = hosted
      ? [
          {
            key: "deleteRawUploads",
            label: "Also delete raw uploads that fed only this match",
            help: "Uploaded videos still attached to another match are kept.",
          },
        ]
      : [
          {
            key: "deleteLocalFiles",
            label: "Also delete the project folder on disk",
            help: "Permanently removes the footage, audit work, and exports under this match's folder. This cannot be undone.",
          },
        ];
    const answer = await confirm({
      title: `Delete ${matchName}?`,
      body: "This removes the match and every resource it owns -- detection state, trims, exports, and any running jobs. This cannot be undone.",
      confirmLabel: "Delete match",
      checkboxes,
    });
    if (!answer.confirmed) return;
    try {
      const resp = await api.deleteProject(matchRoot, {
        deleteLocalFiles: Boolean(answer.checked.deleteLocalFiles),
        deleteRawUploads: Boolean(answer.checked.deleteRawUploads),
      });
      if (resp.summary.errors.length > 0) {
        setError(
          `Deleted with ${resp.summary.errors.length} issue${resp.summary.errors.length === 1 ? "" : "s"}: ${resp.summary.errors.join("; ")}`,
        );
        return;
      }
      navigate("/pick", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const fixHref = useCallback(
    (to: FixTarget, stageNumber: number): string => {
      if (to === "audit") return href("audit", slug, String(stageNumber));
      if (to === "scores") return href("");
      return href("ingest", slug);
    },
    [href, slug],
  );

  if (!project && !error) {
    return <div className="px-7 py-6 text-md text-muted">Loading project...</div>;
  }

  const shooterName = project?.competitor_name ?? shooters.find((s) => s.slug === slug)?.name ?? null;
  const totalStages = rows.length;
  const lines = summaryLines({
    mode,
    selected: orderedSelection.length,
    eligible: eligibleNumbers.length,
    head: headPad,
    tail: tailPad,
    transitionKind,
    transitionSeconds: transitionDurationSeconds,
    cards: describeRenderOptions(renderOptions, compare ? "grid" : "single", compare ? "mp4" : outputFormat),
    overlay: compare ? gridOverlay : includeOverlay,
    cams: mode === "single" && secondaryCount > 0 ? (camOptions.includeSecondaries ? secondaryCount : 0) : null,
    youtube: renderedMp4 ? youtube : null,
    gridCamera: project?.compare_camera ?? null,
    reference: shooters.find((s) => s.slug === audioFrom)?.name ?? null,
    canvas: canvas.label,
    bare: rows.filter((r) => r.bare && selection.has(r.stage.stage_number)).length,
  });
  const primaryLabel = trimsOnly ? "Export trims" : compare ? "Render grid" : "Export bundle";
  const busyLabel = trimsOnly ? "Queueing..." : compare ? "Rendering..." : "Exporting...";
  const bundleName = projectName || project?.name || "";
  const gridSummary = gridResult ? summarizeGridResult(gridResult) : null;

  return (
    <div className="px-7 py-5">
      <PageHeader
        title="Export"
        sub={
          <>
            {shooterName ? `${shooterName} · ` : null}
            <span className="numeral">{eligibleNumbers.length}</span> of{" "}
            <span className="numeral">{totalStages}</span> stages ready
          </>
        }
        actions={
          project?.exports_dir && !hosted ? (
            <Button type="button" onClick={() => void reveal(project.exports_dir!)}>
              Reveal folder <ExternalLink className="size-3.5" />
            </Button>
          ) : null
        }
      />

      {error ? (
        <p role="alert" className="mb-4 rounded-md border border-destructive/45 px-3 py-2 text-md text-destructive">
          {error}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-3">
          {/* Output mode */}
          <Section
            label="Output"
            aside={
              <span className="text-sm text-muted">
                {trimsOnly
                  ? "one lossless trim per stage"
                  : compare
                    ? "one MP4, every shooter beep-aligned per stage"
                    : "FCPXML + CSV + report"}
              </span>
            }
            control={
              <Segmented<ExportMode>
                label="Output mode"
                value={mode}
                onChange={selectMode}
                options={[
                  { value: "single", label: "Timeline" },
                  { value: "trims", label: "Trims only" },
                  {
                    value: "compare",
                    label: "Compare grid",
                    disabled: !multiShooter,
                    title: "The compare grid needs two or more shooters on the match",
                  },
                ]}
              />
            }
          >
            {trimsOnly ? (
              <Field
                label="Grid camera"
                help="Which of this shooter's cameras the compare grid uses. Saved on the shooter; the trims cover every camera on the stage."
              >
                <SelectField
                  label="Camera for the grid"
                  value={project?.compare_camera ?? ""}
                  onChange={(v) => void changeCamera(v)}
                  options={cameraOptions}
                  disabled={editDenied}
                  title={READ_ONLY_MIRROR_MESSAGE}
                  className="w-full max-w-xs"
                />
              </Field>
            ) : null}
            {compare ? (
              <>
                <Field label="Reference" help="Sets the frame rate from this shooter's footage; every shooter is in the mixed track.">
                  <Segmented
                    label="Reference shooter"
                    value={audioFrom}
                    onChange={setAudioFrom}
                    options={shooters.map((s) => ({
                      value: s.slug,
                      label: s.name,
                      tick: s.slug === audioFrom ? ("movement" as const) : undefined,
                    }))}
                  />
                </Field>
                <Field label="Canvas" help="1080p renders faster.">
                  <Segmented
                    label="Canvas"
                    value={canvas.id}
                    onChange={(id) => {
                      const next = CANVAS_CHOICES.find((c) => c.id === id);
                      if (next) setCanvas(next);
                    }}
                    options={CANVAS_CHOICES.map((c) => ({ value: c.id, label: c.label }))}
                  />
                </Field>
                <Field
                  label="Overlay"
                  help={
                    gridOverlay
                      ? "Per-tile shot counter and split with the running clock; the summary holds each tile's own stage figures after its last shot, 0 is off."
                      : "Off: a faster render with no counters on the tiles."
                  }
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <Segmented<"off" | "on">
                      label="Grid overlay"
                      value={gridOverlay ? "on" : "off"}
                      onChange={(v) => setGridOverlay(v === "on")}
                      options={[
                        { value: "off", label: "None" },
                        { value: "on", label: "Counter + splits" },
                      ]}
                    />
                    {gridOverlay ? (
                      <Seconds
                        id="export-grid-hold"
                        label="Grid summary hold seconds"
                        value={gridHoldSeconds}
                        min={0}
                        disabled={busy}
                        onChange={setGridHoldSeconds}
                      />
                    ) : null}
                  </div>
                </Field>
                <RenderOptionsPanel
                  value={renderOptions}
                  onChange={setRenderOptions}
                  surface="grid"
                  outputFormat="mp4"
                  busy={busy}
                />
              </>
            ) : null}
          </Section>

          {/* Stages */}
          <Section
            label="Stages"
            aside={
              <span className="text-sm text-muted">
                <span className="numeral">{eligibleNumbers.length}</span> of <span className="numeral">{totalStages}</span>{" "}
                exportable {"·"} <span className="numeral">{orderedSelection.length}</span> selected
              </span>
            }
            flush
          >
            {rows.length === 0 ? (
              <p className="px-3.5 py-3 text-md text-muted">No stages on this match yet.</p>
            ) : (
              <StageTable rows={rows} selected={selection} onToggle={toggleStage} fixHref={fixHref} />
            )}
          </Section>

          {/* Options: only a timeline has a cut to shape. */}
          {mode === "single" ? (
            <Section label="Options">
              <Field label="Format" help="The splits CSV and the text report are always written alongside.">
                <SelectField
                  label="Timeline format"
                  value={outputFormat}
                  onChange={setOutputFormat}
                  options={[
                    { value: "fcpxml", label: "FCPXML 1.10 (Final Cut Pro)" },
                    { value: "fcp7xml", label: "FCP 7 XML (Premiere / Resolve)" },
                    { value: "mp4", label: "MP4 (rendered)" },
                  ]}
                  className="w-full max-w-xs"
                />
              </Field>
              <Field
                label="Padding"
                help={`${headPad.toFixed(1)} s before the beep · ${tailPad.toFixed(1)} s after the last shot`}
              >
                <div className="flex flex-wrap items-center gap-3">
                  <Segmented<PaddingPreset>
                    label="Trim padding"
                    value={preset}
                    onChange={selectPreset}
                    options={[
                      ...(Object.keys(PADDING_PRESETS) as Array<Exclude<PaddingPreset, "custom">>).map((k) => ({
                        value: k as PaddingPreset,
                        label: PADDING_PRESETS[k].label,
                      })),
                      { value: "custom" as PaddingPreset, label: "Custom" },
                    ]}
                  />
                  {preset === "custom" ? (
                    <>
                      <NumInput label="Before beep (s)" value={headPad} step={0.1} min={0} onChange={setHeadPad} />
                      <NumInput label="After last shot (s)" value={tailPad} step={0.1} min={0} onChange={setTailPad} />
                    </>
                  ) : null}
                </div>
              </Field>
              <Field label="Transition">
                <div className="flex flex-wrap items-center gap-3">
                  <Segmented<TransitionKind>
                    label="Transition"
                    value={transitionKind}
                    onChange={setTransitionKind}
                    options={TRANSITIONS}
                  />
                  {transitionKind !== "none" ? (
                    <NumInput
                      label="Duration (s)"
                      value={transitionDurationSeconds}
                      step={0.1}
                      min={0.1}
                      onChange={setTransitionDurationSeconds}
                    />
                  ) : null}
                </div>
              </Field>
              <RenderOptionsPanel
                value={renderOptions}
                onChange={setRenderOptions}
                surface="single"
                outputFormat={outputFormat}
                busy={busy}
              />
              <Field
                label="Overlay"
                help={
                  includeOverlay
                    ? "Burned-in shot counter and splits; a slower render. The overlay is a transparent MOV, so the codec has to carry alpha: Auto picks HEVC on macOS and ProRes 4444 elsewhere."
                    : "Off: a faster export; the FCPXML still carries shot markers."
                }
              >
                <div className="flex flex-wrap items-center gap-3">
                  <Segmented<"off" | "on">
                    label="Overlay"
                    value={includeOverlay ? "on" : "off"}
                    onChange={(v) => setIncludeOverlay(v === "on")}
                    options={[
                      { value: "off", label: "None" },
                      { value: "on", label: "Shot counter + splits" },
                    ]}
                  />
                  {includeOverlay ? (
                    <SelectField
                      label="Overlay codec"
                      value={overlayCodec}
                      onChange={setOverlayCodec}
                      options={[
                        { value: "auto", label: "Auto" },
                        { value: "hevc-alpha", label: "HEVC + alpha (macOS)" },
                        { value: "prores-4444", label: "ProRes 4444" },
                      ]}
                      className="w-56"
                    />
                  ) : null}
                </div>
              </Field>
              <CamOptionsPanel value={camOptions} onChange={setCamOptions} secondaryCount={secondaryCount} busy={busy} />
              {renderedMp4 ? (
                <Field
                  label="YouTube"
                  help={
                    youtube
                      ? "Encodes with the YouTube preset and writes the title, description with chapters and tags (paste-ready), per-shot captions (.srt) and a thumbnail beside the video."
                      : "Off: the default encode, no upload sidecar."
                  }
                >
                  <Segmented<"off" | "on">
                    label="YouTube"
                    value={youtube ? "on" : "off"}
                    onChange={(v) => setYoutube(v === "on")}
                    options={[
                      { value: "off", label: "Off" },
                      { value: "on", label: "Preset + sidecar" },
                    ]}
                  />
                  {youtube ? (
                    <textarea
                      id="export-description-lead"
                      aria-label="Description lead"
                      rows={2}
                      className={cn(inputClass, "mt-2 max-w-md")}
                      placeholder="What the video is, above the chapter list: division, camera, the day"
                      value={descriptionLead}
                      onChange={(e) => setDescriptionLead(e.target.value)}
                    />
                  ) : null}
                  {!hosted ? (
                    <div className="mt-2.5">
                      <YouTubeConnect
                        settings={youtubeSettings}
                        onSettingsChange={() => void reloadYouTube()}
                        options={uploadOptions}
                        onOptionsChange={setUploadOptions}
                        matchName={projectName || project?.name || ""}
                        showUploadControl={renderedMp4 && youtube}
                        busy={busy}
                      />
                    </div>
                  ) : null}
                </Field>
              ) : null}
              <Field label="Bundle name" htmlFor="export-bundle-name" help={project?.exports_dir ?? "exports/"}>
                <input
                  id="export-bundle-name"
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className={cn(inputClass, "max-w-xs font-mono text-sm")}
                />
              </Field>
            </Section>
          ) : null}

          {/* Rendered in both deployment modes -- only the reveal
              affordance is desktop-specific; the download link works on
              both (#629). */}
          <ExportHistory
            runs={runs}
            exportFileUrl={(f) => api.exportFileUrl(slug, f)}
            youtube={
              !hosted && youtubeSettings?.connected
                ? { connected: true, onUpload: (f, again) => void uploadRow(f, again), busyFilename: uploadBusy }
                : undefined
            }
          />
        </div>

        {/* Summary rail */}
        <aside className="lg:sticky lg:top-3 lg:self-start">
          <div className="overflow-hidden rounded-[10px] border border-rule-strong bg-surface">
            <div className="flex items-center justify-between border-b border-rule px-3.5 py-2.5">
              <Label>{trimsOnly ? "Trims" : compare ? "Grid" : "Bundle"}</Label>
              <span className="numeral text-sm text-ink-2" title="Estimated duration">
                ~ {formatDuration(duration)}
              </span>
            </div>
            <dl>
              {lines.map((l) => (
                <div key={l.label} className="flex justify-between gap-3 border-b border-rule px-3.5 py-1.5 text-md">
                  <dt className="text-muted">{l.label}</dt>
                  <dd className={cn("numeral", l.dim ? "text-muted" : "text-ink")}>{l.value}</dd>
                </div>
              ))}
            </dl>
            <div className="border-b border-rule px-3.5 py-2.5 font-mono text-sm leading-relaxed text-ink-2">
              {trimsOnly ? (
                <span>
                  {orderedSelection.length} lossless {orderedSelection.length === 1 ? "trim" : "trims"} into exports/
                </span>
              ) : compare ? (
                <span>compare-grid.mp4</span>
              ) : (
                <>
                  <div className="truncate">
                    {bundleName}
                    {BUNDLE_EXTENSION[outputFormat]}
                  </div>
                  <div className="truncate text-muted">{bundleName}.csv</div>
                  <div className="truncate text-muted">{bundleName}.txt</div>
                  {renderedMp4 && youtube ? (
                    <>
                      <div className="truncate text-muted">{bundleName}-youtube.txt</div>
                      <div className="truncate text-muted">{bundleName}-youtube.json</div>
                      <div className="truncate text-muted">{bundleName}-thumbnail.jpg</div>
                      <div className="truncate text-muted">{bundleName}.srt</div>
                    </>
                  ) : null}
                </>
              )}
            </div>
            <div className="border-b border-rule px-3.5 py-3">
              <Button
                type="button"
                variant="primary"
                className="w-full"
                onClick={() => void submitExport()}
                disabled={!canExport}
                title={editDenied ? READ_ONLY_MIRROR_MESSAGE : undefined}
              >
                {busy ? busyLabel : primaryLabel}
              </Button>
              {busy && job?.message ? (
                <div className="mt-2 text-sm text-muted">
                  {job.message}
                  {job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}
                </div>
              ) : null}
              {queuedNote ? (
                <p className="mt-3 text-sm text-ink-2">
                  <span className="text-done">Queued</span> {"·"} {queuedNote}
                </p>
              ) : null}
              {result ? (
                <ResultPanel
                  result={result}
                  onReveal={reveal}
                  hosted={hosted}
                  downloads={hostedDownloads}
                  exportFileUrl={(filename) => api.exportFileUrl(slug, filename)}
                />
              ) : null}
              {gridResult && gridSummary ? (
                <div className="mt-3 text-sm text-ink-2">
                  <div className={cn("font-medium", gridSummary.partial ? "text-live" : "text-done")}>
                    {gridSummary.headline}
                  </div>
                  {gridSummary.partial ? (
                    <ul className="mt-1 list-disc pl-4 text-muted">
                      {gridSummary.failedStages.map((name) => (
                        <li key={`failed-${name}`}>{name} did not render</li>
                      ))}
                      {gridSummary.skippedStages.map((n) => (
                        <li key={`skipped-${n}`}>Stage {n} had no trim from any shooter -- not rendered</li>
                      ))}
                      {gridSummary.missingTrims.map((line) => (
                        <li key={`missing-${line}`}>{line} -- that cell is black</li>
                      ))}
                    </ul>
                  ) : null}
                  {hosted && gridResult.output_name ? (
                    <a
                      href={api.matchExportFileUrl(gridResult.output_name)}
                      download={gridResult.output_name}
                      className="mt-2 inline-flex items-center gap-1.5 text-ink-2 underline underline-offset-4 hover:text-ink"
                    >
                      <Download className="size-3" /> Download grid
                    </a>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void reveal(gridResult.output_path)}
                      className="mt-2 inline-flex items-center gap-1.5 text-ink-2 underline underline-offset-4 hover:text-ink"
                    >
                      Reveal file <ExternalLink className="size-3" />
                    </button>
                  )}
                </div>
              ) : null}
            </div>
            {/* Storage row: the deliverables list above is where "I have
                too many of these" forms, so cleanup lives here; delete
                moved here from the Matches row (spec s4.1). */}
            <div className="flex items-center justify-between px-3.5 py-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setCleanupOpen(true)}>
                Reclaim space
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => void deleteMatch()}
                disabled={editDenied || !ctx?.health?.project_root}
                title={editDenied ? READ_ONLY_MIRROR_MESSAGE : undefined}
              >
                Delete match
              </Button>
            </div>
          </div>
        </aside>
      </div>

      <CleanupDialog
        slug={slug}
        open={cleanupOpen}
        onClose={() => {
          setCleanupOpen(false);
          // A cleanup can delete exports/trims this page is currently
          // showing download links and presence badges for; without a
          // reload the page keeps offering downloads that now 404. The
          // history rows are durable -- what moves is each artefact's
          // ``available`` flag.
          void reload();
        }}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Pieces                                                                     */
/* -------------------------------------------------------------------------- */

function Section({
  label,
  aside,
  control,
  flush = false,
  children,
}: {
  label: string;
  aside?: React.ReactNode;
  control?: React.ReactNode;
  /** No inner padding: the child brings its own rows. */
  flush?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <section className="rounded-[10px] border border-rule bg-surface">
      <div className={cn("flex flex-wrap items-center gap-3 px-3.5 py-2", children ? "border-b border-rule" : null)}>
        <Label>{label}</Label>
        {control}
        {aside ? <span className="ml-auto">{aside}</span> : null}
      </div>
      {children ? <div className={cn(flush ? "[&>div]:rounded-none [&>div]:border-0" : null)}>{children}</div> : null}
    </section>
  );
}

function ResultPanel({
  result,
  onReveal,
  hosted,
  downloads,
  exportFileUrl,
}: {
  result: MatchExportResult;
  onReveal: (path: string) => void;
  hosted: boolean;
  downloads: { label: string; filename: string }[];
  exportFileUrl: (filename: string) => string;
}) {
  return (
    <div className="mt-3 text-sm text-ink-2">
      <div className="font-medium text-done">Exported</div>
      <div className="numeral text-muted">
        {result.stage_count} stages {"·"} {formatDuration(result.duration_seconds)}
        {result.anomalies.length > 0 ? <> {"·"} {result.anomalies.length} warnings</> : null}
      </div>
      {hosted ? (
        // Hosted: the bundle lives in object storage, not on a local disk to
        // reveal. Download each file (FCPXML + the media it references).
        <div className="mt-2 flex flex-col gap-1">
          {downloads.map((d) => (
            <a
              key={d.filename}
              href={exportFileUrl(d.filename)}
              download={d.filename}
              className="inline-flex items-center gap-1.5 text-ink-2 underline underline-offset-4 hover:text-ink"
            >
              <Download className="size-3" /> {d.label}
            </a>
          ))}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => onReveal(result.fcpxml_path)}
          className="mt-2 inline-flex items-center gap-1.5 text-ink-2 underline underline-offset-4 hover:text-ink"
        >
          Reveal bundle <ExternalLink className="size-3" />
        </button>
      )}
    </div>
  );
}

function NumInput({
  label,
  value,
  step,
  min,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-muted">
      {label}
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className={cn(inputClass, "w-20 font-mono text-sm")}
      />
    </label>
  );
}
