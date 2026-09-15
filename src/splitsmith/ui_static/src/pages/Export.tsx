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
import { CutGroup } from "@/components/export/CutGroup";
import { DetailsGroup } from "@/components/export/DetailsGroup";
import { ExportHistory } from "@/components/export/ExportHistory";
import { LookGroup } from "@/components/export/LookGroup";
import { OutputGroup } from "@/components/export/OutputGroup";
import { PresetRow } from "@/components/export/PresetRow";
import { PreviewPane } from "@/components/export/PreviewPane";
import { SavePresetSheet } from "@/components/export/SavePresetSheet";
import { Section } from "@/components/export/Section";
import { StageTable } from "@/components/export/StageTable";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Button } from "@/components/ui/button";
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
  type ExportPreset,
  type ExportRun,
  type Job,
  type MatchExportResult,
  type MatchProject,
  type YouTubeSettings,
} from "@/lib/api";
import { camExportFields, syncedSecondaryCount } from "@/lib/camOptions";
import { rowUploadOptions } from "@/lib/youtubeRows";
import { hostedDownloads as buildHostedDownloads } from "@/lib/exportDownloads";
import type { LookFocus } from "@/lib/exportPreview";
import {
  applyBody,
  DEFAULT_EXPORT_SETTINGS,
  groupSummary,
  isDirty,
  loadLastUsed,
  NEW_PRESET_ID,
  saveLastUsed,
  settingsToBody,
  type ExportSettings,
  type SettingsGroup,
} from "@/lib/exportPresets";
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
  clampSeconds,
  describeRenderOptions,
  matchExportFields,
  renderOptionsSeconds,
  transitionsSupported,
  type OutputFormat,
} from "@/lib/renderOptions";
import { cn } from "@/lib/utils";
import { buildCompareGridPayload, CANVAS_CHOICES, summarizeGridResult } from "@/pages/matchExportModel";

/** What ``ui/match_exports.py`` names the timeline file per format. */
const BUNDLE_EXTENSION: Record<OutputFormat, string> = { fcpxml: ".fcpxml", fcp7xml: ".xml", mp4: ".mp4" };

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
  // The recurring half of the form is one object so a preset applies and
  // compares as a unit (lib/exportPresets). Match-specific fields stay
  // separate below and are never stored.
  const [settings, setSettings] = useState<ExportSettings>(() => {
    const last = loadLastUsed(window.localStorage);
    return last ? applyBody(DEFAULT_EXPORT_SETTINGS, last.body) : DEFAULT_EXPORT_SETTINGS;
  });
  const patch = useCallback((p: Partial<ExportSettings>) => setSettings((s) => ({ ...s, ...p })), []);
  const {
    mode,
    outputFormat,
    overlayCodec,
    camOptions,
    youtube,
    headPad,
    tailPad,
    transitionKind,
    transitionSeconds,
    renderOptions,
    includeOverlay,
    gridOverlay,
    gridHoldSeconds,
    uploadOptions,
  } = settings;
  const canvas = CANVAS_CHOICES.find((c) => c.id === settings.canvas) ?? CANVAS_CHOICES[0];

  const [selection, setSelection] = useState<Set<number>>(() => new Set());
  const [projectName, setProjectName] = useState<string>("");
  const [descriptionLead, setDescriptionLead] = useState<string>("");
  // Compare grid: the reference shooter sets the frame rate.
  const [audioFrom, setAudioFrom] = useState<string>("");

  // Presets. Built-ins come from the server too; a failed load leaves the
  // row with Custom alone and the page fully usable.
  const [presets, setPresets] = useState<ExportPreset[]>([]);
  const [activePresetId, setActivePresetId] = useState<string | null>(
    () => loadLastUsed(window.localStorage)?.presetId ?? null,
  );
  const [saveSheet, setSaveSheet] = useState<{ mode: "saveAs" | "rename"; id?: string } | null>(null);
  // The rail's preview follows the last picked Look tile and, while the
  // pointer is on one, that tile's generic thumbnail.
  const [lookFocus, setLookFocus] = useState<LookFocus | null>(null);
  const [lookHover, setLookHover] = useState<LookFocus | null>(null);
  const [openGroups, setOpenGroups] = useState<Record<SettingsGroup, boolean>>({
    output: false,
    cut: false,
    look: false,
  });
  const toggleGroup = (g: SettingsGroup) => setOpenGroups((o) => ({ ...o, [g]: !o[g] }));

  useEffect(() => {
    let cancelled = false;
    Promise.resolve(api.getExportPresets())
      .then((r) => {
        if (!cancelled) setPresets(r.presets);
      })
      .catch(() => {
        if (!cancelled) setPresets([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // First visit, nothing remembered: start on the first built-in.
  const hadLastUsed = useRef(loadLastUsed(window.localStorage) !== null);
  useEffect(() => {
    if (activePresetId === null && presets.length > 0 && !hadLastUsed.current) {
      setSettings((s) => applyBody(s, presets[0].body));
      setActivePresetId(presets[0].preset_id);
    }
  }, [presets, activePresetId]);

  const activePreset = presets.find((p) => p.preset_id === activePresetId) ?? null;
  const dirty = activePreset ? isDirty(settings, activePreset.body) : true;

  // Last-used follows every change, debounced; the id it names may be a
  // preset the form has since diverged from, which the row shows as Custom.
  useEffect(() => {
    const t = window.setTimeout(() => saveLastUsed(window.localStorage, settings, activePresetId), 300);
    return () => window.clearTimeout(t);
  }, [settings, activePresetId]);

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
  const selectMode = useCallback(
    (next: ExportMode) => {
      patch({ mode: next });
      setSelection(new Set());
      setResult(null);
      setGridResult(null);
      setQueuedNote(null);
      setLookFocus(null);
      setLookHover(null);
    },
    [patch],
  );

  // A preset the match cannot run is offered greyed, like the mode option
  // itself; applying it would otherwise overwrite every other setting
  // while the mode stayed put.
  const presetUnavailable = useCallback(
    (p: ExportPreset) =>
      p.body.mode === "compare" && !multiShooter ? "The compare grid needs two or more shooters on the match" : null,
    [multiShooter],
  );

  function applyPreset(id: string) {
    const p = presets.find((x) => x.preset_id === id);
    if (!p || presetUnavailable(p) !== null) return;
    if (p.body.mode !== mode) selectMode(p.body.mode);
    setSettings((s) => applyBody(s, p.body));
    setActivePresetId(id);
    setOpenGroups({ output: false, cut: false, look: false });
    setLookFocus(null);
  }

  async function savePreset(id: string, name: string) {
    try {
      const saved = await api.putExportPreset(id, name, settingsToBody(settings));
      // Re-read rather than splice: the server owns the order (built-ins
      // first, own by casefolded name), and a client-side sort would
      // disagree with it on the next load.
      let list: ExportPreset[];
      try {
        list = (await api.getExportPresets()).presets;
      } catch {
        list = [...presets.filter((p) => p.preset_id !== saved.preset_id), saved];
      }
      setPresets(list);
      setActivePresetId(saved.preset_id);
      setSaveSheet(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function deletePreset(id: string) {
    const p = presets.find((x) => x.preset_id === id);
    if (!p) return;
    const answer = await confirm({ title: `Delete preset "${p.name}"?`, confirmLabel: "Delete" });
    if (!answer.confirmed) return;
    try {
      await api.deleteExportPreset(id);
      setPresets((list) => list.filter((x) => x.preset_id !== id));
      if (activePresetId === id) setActivePresetId(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

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
    transitionKind: transitionsSupported(outputFormat) ? transitionKind : "none",
    transitionSeconds,
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
        transition_kind: transitionsSupported(outputFormat) ? transitionKind : "none",
        transition_duration_seconds: clampSeconds(transitionSeconds, 0.1),
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
  // Selected bundle stages exporting without audited shots: the rail
  // counts them and the shot-dependent options say what they lose.
  const bareSelected = rows.filter((r) => r.bare && selection.has(r.stage.stage_number)).length;
  const lines = summaryLines({
    mode,
    selected: orderedSelection.length,
    eligible: eligibleNumbers.length,
    head: headPad,
    tail: tailPad,
    transitionKind: transitionsSupported(outputFormat) ? transitionKind : "none",
    transitionSeconds,
    cards: describeRenderOptions(renderOptions, compare ? "grid" : "single", compare ? "mp4" : outputFormat),
    overlay: compare ? gridOverlay : includeOverlay,
    cams: mode === "single" && secondaryCount > 0 ? (camOptions.includeSecondaries ? secondaryCount : 0) : null,
    youtube: renderedMp4 ? youtube : null,
    gridCamera: project?.compare_camera ?? null,
    reference: shooters.find((s) => s.slug === audioFrom)?.name ?? null,
    canvas: canvas.label,
    bare: bareSelected,
  });
  const summaryCtx = { secondaryCount };
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
          <PresetRow
            presets={presets}
            unavailable={presetUnavailable}
            activeId={activePresetId}
            dirty={dirty}
            busy={busy}
            onApply={applyPreset}
            onSave={(id) => void savePreset(id, activePreset?.name ?? "")}
            onSaveAs={() => setSaveSheet({ mode: "saveAs" })}
            onRename={(id) => setSaveSheet({ mode: "rename", id })}
            onDelete={(id) => void deletePreset(id)}
          />


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


          {/* Output: the mode control stays in the header so switching is
              one click while the group is folded. */}
          <Section
            label="Output"
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
            summary={groupSummary(settings, "output", summaryCtx)}
            open={openGroups.output}
            onToggle={() => toggleGroup("output")}
          >
            <OutputGroup
              settings={settings}
              patch={patch}
              busy={busy}
              editDenied={editDenied}
              shooters={shooters}
              audioFrom={audioFrom}
              onAudioFrom={setAudioFrom}
              cameraOptions={cameraOptions}
              compareCamera={project?.compare_camera ?? ""}
              onChangeCamera={(v) => void changeCamera(v)}
              secondaryCount={secondaryCount}
              bareSelected={bareSelected}
            />
          </Section>

          {mode === "single" ? (
            <Section
              label="Cut"
              summary={groupSummary(settings, "cut", summaryCtx)}
              open={openGroups.cut}
              onToggle={() => toggleGroup("cut")}
            >
              <CutGroup settings={settings} patch={patch} busy={busy} />
            </Section>
          ) : null}

          {mode !== "trims" ? (
            <Section
              label="Look"
              summary={groupSummary(settings, "look", summaryCtx)}
              open={openGroups.look}
              onToggle={() => toggleGroup("look")}
            >
              <LookGroup
                settings={settings}
                patch={patch}
                busy={busy}
                bareSelected={bareSelected}
                onHover={setLookHover}
                onSelect={setLookFocus}
              />
            </Section>
          ) : null}

          {mode === "single" || (compare && (renderOptions.titlePage || renderOptions.closingCard)) ? (
            <Section label="Details">
              <DetailsGroup
                settings={settings}
                patch={patch}
                busy={busy}
                hosted={hosted}
                projectName={projectName}
                onProjectName={setProjectName}
                exportsDir={project?.exports_dir ?? null}
                descriptionLead={descriptionLead}
                onDescriptionLead={setDescriptionLead}
                youtubeSettings={youtubeSettings}
                onYouTubeSettingsChange={() => void reloadYouTube()}
                matchName={projectName || project?.name || ""}
              />
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
        {/* Pinned under the shell's measured header, not the viewport top,
            and capped to the viewport so a long rail scrolls inside itself
            instead of losing its first rows under the bar. */}
        <aside className="lg:sticky lg:top-[calc(var(--shell-header-h,86px)+12px)] lg:max-h-[calc(100dvh-var(--shell-header-h,86px)-24px)] lg:self-start lg:overflow-y-auto">
          <div className="overflow-hidden rounded-[10px] border border-rule-strong bg-surface">
            <div className="flex items-center justify-between border-b border-rule px-3.5 py-2.5">
              <Label>{trimsOnly ? "Trims" : compare ? "Grid" : "Bundle"}</Label>
              <span className="numeral text-sm text-ink-2" title="Estimated duration">
                ~ {formatDuration(duration)}
              </span>
            </div>
            <PreviewPane
              slug={compare ? audioFrom || slug : slug}
              stageNumber={orderedSelection[0] ?? 0}
              settings={settings}
              projectName={projectName || project?.name || ""}
              focus={lookFocus}
              hover={lookHover}
              enabled={!trimsOnly && orderedSelection.length > 0}
            />
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

      {saveSheet ? (
        <SavePresetSheet
          key={saveSheet.mode + (saveSheet.id ?? "")}
          open
          title={saveSheet.mode === "rename" ? (dirty ? "Rename and save preset" : "Rename preset") : "Save preset"}
          initialName={
            saveSheet.mode === "rename" ? (presets.find((p) => p.preset_id === saveSheet.id)?.name ?? "") : ""
          }
          onClose={() => setSaveSheet(null)}
          onSubmit={(name) => void savePreset(saveSheet.mode === "rename" ? (saveSheet.id ?? NEW_PRESET_ID) : NEW_PRESET_ID, name)}
        />
      ) : null}
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
