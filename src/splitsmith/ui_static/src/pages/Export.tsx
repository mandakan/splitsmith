/**
 * Analysis & Export screen (#17).
 *
 * Match overview at the top: project name, audit progress (X / N stages),
 * a row per stage with audit + export status. Click a stage to drill into
 * its analysis: editable shot table, anomalies, output toggles, and a
 * Generate button that wraps csv_gen / fcpxml_gen / report.write_report
 * via /api/stages/{n}/export.
 *
 * Notes editing is inline; saves write back to the audit JSON via the
 * existing /api/stages/{n}/audit endpoint, so the same notes flow through
 * to the splits CSV on the next Generate.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Film,
  FileBarChart,
  FileText,
  FolderOpen,
  Loader2,
  PlayCircle,
  RefreshCw,
  Video,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  api,
  asSourceUnreachable,
  type ExportOverview,
  type Job,
  type MatchExportResult,
  type MatchExportTemplateEntry,
  type MatchProject,
  type OverlayCodec,
  type SecondaryExportStatus,
  type StageAudit,
  type StageExportStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type PaddingPreset = "full" | "action" | "highlight" | "custom";

const PADDING_PRESETS: Record<
  Exclude<PaddingPreset, "custom">,
  { label: string; head: number; tail: number; help: string }
> = {
  full: {
    label: "Full",
    head: 5.0,
    tail: 5.0,
    help: "Matches the per-stage export defaults (5s before beep, 5s after final shot).",
  },
  action: {
    label: "Action cut",
    head: 0.5,
    tail: 1.0,
    help: "Tight: 0.5s before beep, 1s after final shot. Best for a fast-moving match reel.",
  },
  highlight: {
    label: "Highlight",
    head: 1.5,
    tail: 2.0,
    help: "Mid: 1.5s before beep, 2s after final shot. Room to read the body before the draw.",
  },
};

export function Export() {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [overview, setOverview] = useState<ExportOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Match-export multi-select. A stage qualifies for inclusion only when
  // it's already exported (lossless trim + audit shots present); otherwise
  // the match-export endpoint would 400. The dialog reads ``selectedForMatch``
  // and the trim-buffer caps from the project settings.
  const [selectedForMatch, setSelectedForMatch] = useState<Set<number>>(
    () => new Set(),
  );
  const [matchDialogOpen, setMatchDialogOpen] = useState(false);
  const [matchResult, setMatchResult] = useState<MatchExportResult | null>(null);

  const reload = useCallback(async () => {
    try {
      const [proj, ov] = await Promise.all([
        api.getProject(),
        api.getExportOverview(),
      ]);
      setProject(proj);
      setOverview(ov);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // A stage is eligible for the match export once its audit is complete
  // (primary + beep + shots) AND its source video is reachable -- the
  // worker may need to (re-)trim, which requires the source on disk.
  // We exclude unreachable stages here rather than letting the endpoint
  // 424 on submit; the row's StatusBadge already flags "Source missing"
  // so the user can see why a stage is greyed out.
  //
  // All match-export hooks live ABOVE the early-return below so the hook
  // order is stable across the loading -> loaded transition (React rules
  // of hooks: every render must call the same hooks in the same order).
  const matchEligibleStageNumbers = useMemo(
    () =>
      (overview?.stages ?? [])
        .filter(
          (s) =>
            !s.skipped &&
            s.ready_to_export &&
            s.source_reachable !== false,
        )
        .map((s) => s.stage_number),
    [overview],
  );
  const eligibleSet = useMemo(
    () => new Set(matchEligibleStageNumbers),
    [matchEligibleStageNumbers],
  );
  // Drop any selections whose stage stopped being eligible (re-run cleared
  // exports, stage skipped, etc) so the banner count and dialog stay
  // truthful.
  useEffect(() => {
    setSelectedForMatch((prev) => {
      const next = new Set<number>();
      for (const n of prev) {
        if (eligibleSet.has(n)) next.add(n);
      }
      return next.size === prev.size ? prev : next;
    });
  }, [eligibleSet]);

  const toggleSelection = useCallback(
    (stageNumber: number, checked: boolean) => {
      setSelectedForMatch((prev) => {
        const next = new Set(prev);
        if (checked) next.add(stageNumber);
        else next.delete(stageNumber);
        return next;
      });
    },
    [],
  );

  const orderedSelection = useMemo(
    () =>
      (overview?.stages ?? [])
        .map((s) => s.stage_number)
        .filter((n) => selectedForMatch.has(n)),
    [overview, selectedForMatch],
  );

  if (!project && !error) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-7 w-1/3" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  const total = (overview?.stages ?? []).filter((s) => !s.skipped).length;
  const ready = (overview?.stages ?? []).filter(
    (s) => !s.skipped && s.ready_to_export,
  ).length;
  const exported = (overview?.stages ?? []).filter(
    (s) => !s.skipped && s.has_exports,
  ).length;

  const headPadCap = project?.trim_pre_buffer_seconds ?? 5.0;
  const tailPadCap = project?.trim_post_buffer_seconds ?? 5.0;

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Analysis &amp; Export
        </h1>
        <p className="text-sm text-muted-foreground">
          Per-stage shot review and CSV / FCPXML / report generation. Outputs
          are written to <code>{project?.exports_dir ?? "<project>/exports"}</code>;
          re-running overwrites in place.
        </p>
      </header>

      {error ? (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start gap-2 text-sm text-destructive">
              <AlertCircle className="size-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FileBarChart className="size-5" />
            {project?.name ?? "Match"}
          </CardTitle>
          <CardDescription className="space-x-4 tabular-nums">
            <span>
              {ready} / {total} stages ready
            </span>
            <span className="text-muted-foreground">
              {exported} / {total} have exports
            </span>
            {project?.match_date ? (
              <span className="text-muted-foreground">
                {project.match_date}
              </span>
            ) : null}
          </CardDescription>
        </CardHeader>
      </Card>

      {matchEligibleStageNumbers.length > 0 ? (
        <div className="sticky top-0 z-10 -mx-4 border-b border-border/60 bg-background/95 px-4 py-2 backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div>
              <strong>{selectedForMatch.size}</strong> of{" "}
              {matchEligibleStageNumbers.length} eligible stage
              {matchEligibleStageNumbers.length === 1 ? "" : "s"} selected
              {selectedForMatch.size === 0 ? " -- pick stages to stitch" : ""}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={
                  selectedForMatch.size === matchEligibleStageNumbers.length
                }
                onClick={() =>
                  setSelectedForMatch(new Set(matchEligibleStageNumbers))
                }
                title="Select every audited stage"
              >
                Select all
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={selectedForMatch.size === 0}
                onClick={() => setSelectedForMatch(new Set())}
              >
                Clear
              </Button>
              <Button
                size="sm"
                disabled={selectedForMatch.size < 2}
                onClick={() => setMatchDialogOpen(true)}
                title={
                  selectedForMatch.size >= 2
                    ? "Stitch the selected stages into one FCPXML (auto-runs missing per-stage trims)"
                    : "Select 2+ stages to enable"
                }
              >
                <Film className="size-4" />
                Export match...
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {matchResult ? (
        <Card>
          <CardContent className="pt-6 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="space-y-1">
                <div className="flex items-center gap-2 font-medium">
                  <CheckCircle2 className="size-4 text-status-complete" />
                  Match export written ({matchResult.stage_count} stages,{" "}
                  {matchResult.duration_seconds.toFixed(1)}s)
                </div>
                <div className="font-mono text-xs text-muted-foreground">
                  {matchResult.fcpxml_path}
                </div>
                {matchResult.anomalies.length > 0 ? (
                  <ul className="ml-4 list-disc text-xs text-status-warning">
                    {matchResult.anomalies.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    void api
                      .revealFile(matchResult.fcpxml_path)
                      .catch((e) =>
                        setError(e instanceof Error ? e.message : String(e)),
                      );
                  }}
                >
                  <FolderOpen className="size-4" />
                  Reveal in Finder
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setMatchResult(null)}
                >
                  Dismiss
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="space-y-3">
        {(overview?.stages ?? []).map((row) => (
          <StageRow
            key={row.stage_number}
            row={row}
            onChanged={reload}
            onError={setError}
            matchEligible={eligibleSet.has(row.stage_number)}
            matchSelected={selectedForMatch.has(row.stage_number)}
            onToggleMatchSelection={toggleSelection}
          />
        ))}
      </div>

      {matchDialogOpen ? (
        <MatchExportDialog
          stageNumbers={orderedSelection}
          headPadCap={headPadCap}
          tailPadCap={tailPadCap}
          defaultProjectName={project?.name ?? "match"}
          stages={overview?.stages ?? []}
          onCancel={() => setMatchDialogOpen(false)}
          onSuccess={(result) => {
            setMatchDialogOpen(false);
            setMatchResult(result);
          }}
        />
      ) : null}
    </div>
  );
}

function StageRow({
  row,
  onChanged,
  onError,
  matchEligible,
  matchSelected,
  onToggleMatchSelection,
}: {
  row: StageExportStatus;
  onChanged: () => Promise<void>;
  onError: (msg: string | null) => void;
  matchEligible: boolean;
  matchSelected: boolean;
  onToggleMatchSelection: (stageNumber: number, checked: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Card className={cn(row.skipped && "opacity-60")}>
      <CardHeader
        className="cursor-pointer pb-3"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-primary"
              checked={matchSelected}
              disabled={!matchEligible}
              title={
                matchEligible
                  ? "Include this stage in a match export (any missing trim is produced automatically)"
                  : row.source_reachable === false
                    ? "Source video is not reachable -- reconnect external storage or re-link on Ingest"
                    : "Finish the audit first -- shot detection must produce at least one shot"
              }
              onClick={(e) => e.stopPropagation()}
              onChange={(e) =>
                onToggleMatchSelection(row.stage_number, e.target.checked)
              }
            />
            {expanded ? (
              <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            )}
            <div>
              <CardTitle className="text-base">
                Stage {row.stage_number}: {row.stage_name}
              </CardTitle>
              <CardDescription className="font-mono tabular-nums">
                {/* Math: 61 shots audited from 91 candidates means 61
                    were kept, 91 - 61 = 30 were rejected. Every candidate
                    is decided once shot detection has run -- nothing is
                    "pending" in the audit_events sense. */}
                {row.audit_shot_count} shot{row.audit_shot_count === 1 ? "" : "s"} audited
                {row.total_candidate_count > 0
                  ? ` from ${row.total_candidate_count} candidate${row.total_candidate_count === 1 ? "" : "s"}`
                  : ""}
                {row.last_export_at
                  ? ` -- last export ${new Date(row.last_export_at).toLocaleString()}`
                  : ""}
              </CardDescription>
            </div>
          </div>
          <StatusBadge row={row} />
        </div>
      </CardHeader>
      {expanded ? (
        <CardContent className="space-y-4 pt-0">
          <StageActions row={row} onChanged={onChanged} onError={onError} />
          <StageShotTable row={row} onChanged={onChanged} onError={onError} />
        </CardContent>
      ) : null}
    </Card>
  );
}

function StatusBadge({ row }: { row: StageExportStatus }) {
  if (row.skipped) {
    return <Badge variant="outline">Skipped</Badge>;
  }
  if (!row.has_primary) {
    return <Badge variant="statusNotStarted">No primary</Badge>;
  }
  if (row.source_reachable === false) {
    return (
      <Badge variant="statusWarning" className="gap-1">
        <AlertCircle className="size-3" /> Source missing
      </Badge>
    );
  }
  if (row.ready_to_export && row.has_exports) {
    return (
      <Badge variant="statusComplete" className="gap-1">
        <CheckCircle2 className="size-3" /> Exported
      </Badge>
    );
  }
  if (row.ready_to_export) {
    return (
      <Badge variant="statusInProgress" className="gap-1">
        Ready
      </Badge>
    );
  }
  if (row.audit_shot_count > 0 || row.total_candidate_count > 0) {
    return <Badge variant="statusInProgress">Auditing</Badge>;
  }
  return <Badge variant="statusNotStarted">Not started</Badge>;
}

function StageActions({
  row,
  onChanged,
  onError,
}: {
  row: StageExportStatus;
  onChanged: () => Promise<void>;
  onError: (msg: string | null) => void;
}) {
  const [trim, setTrim] = useState(true);
  const [csv, setCsv] = useState(true);
  const [fcpxml, setFcpxml] = useState(true);
  const [reportFlag, setReportFlag] = useState(true);
  // Overlay (issue #45) defaults off: render is slower than the other
  // writers and most users only want it once per stage.
  const [overlay, setOverlay] = useState(false);
  // Overlay format knobs (issue #45 follow-up). Defaults match the
  // server: ``"auto"`` codec with no resolution / fps cap. ``"source"``
  // is a UI-only sentinel that maps to ``null`` on the wire so the
  // renderer mirrors the trimmed clip frame-for-frame.
  const [overlayCodec, setOverlayCodec] = useState<OverlayCodec>("auto");
  const [overlayMaxHeight, setOverlayMaxHeight] = useState<number | "source">(
    "source",
  );
  const [overlayMaxFps, setOverlayMaxFps] = useState<number | "source">(
    "source",
  );
  // Per-cam include/exclude (issue #54). Default-on when the cam is
  // shippable (has a beep + source reachable); resyncs whenever the row
  // refreshes from the overview so flipping a cam's role / detecting its
  // beep doesn't leave stale state behind.
  const eligibleCamIds = useMemo(
    () =>
      row.secondaries
        .filter((s) => s.has_beep && s.source_reachable)
        .map((s) => s.video_id),
    [row.secondaries],
  );
  const [selectedCams, setSelectedCams] = useState<Set<string>>(
    () => new Set(eligibleCamIds),
  );
  useEffect(() => {
    setSelectedCams((prev) => {
      const next = new Set<string>();
      const eligible = new Set(eligibleCamIds);
      for (const id of prev) {
        if (eligible.has(id)) next.add(id);
      }
      for (const id of eligibleCamIds) {
        // Newly eligible cams (e.g. user just confirmed the beep) opt in
        // by default; the user can untick before clicking Generate.
        if (!prev.has(id) && !next.has(id)) next.add(id);
      }
      return next;
    });
  }, [eligibleCamIds]);

  const [job, setJob] = useState<Job | null>(null);

  const sourceUnreachable = row.has_primary && row.source_reachable === false;
  const willDegrade = sourceUnreachable && (trim || fcpxml);
  const busy = job?.status === "pending" || job?.status === "running";

  // Resume an in-flight export job after a page reload, so the JobsPanel
  // (sidebar) and this row stay consistent.
  useEffect(() => {
    let cancelled = false;
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "export" &&
            j.stage_number === row.stage_number &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        const final = await api.pollJob(active.id, (j) => {
          if (!cancelled) setJob(j);
        });
        if (cancelled) return;
        if (final.status === "failed") {
          onError(final.error ?? "Export failed");
        }
        await onChanged();
      })
      .catch(() => {
        // Best effort; jobs endpoint occasionally hiccups during reload.
      });
    return () => {
      cancelled = true;
    };
  }, [row.stage_number, onChanged, onError]);

  const generate = async () => {
    onError(null);
    try {
      // Forward the per-cam allowlist only when the stage actually has
      // secondaries -- on a single-cam stage we let the server keep its
      // legacy "all eligible cams" default rather than sending an empty
      // list that would explicitly suppress nothing.
      const submitted = await api.exportStage(row.stage_number, {
        write_trim: trim,
        write_csv: csv,
        write_fcpxml: fcpxml,
        write_report: reportFlag,
        write_overlay: overlay,
        overlay_codec: overlayCodec,
        overlay_max_height:
          overlayMaxHeight === "source" ? null : overlayMaxHeight,
        overlay_max_fps: overlayMaxFps === "source" ? null : overlayMaxFps,
        ...(row.secondaries.length > 0
          ? { secondary_video_ids: Array.from(selectedCams) }
          : {}),
      });
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "failed") {
        onError(final.error ?? "Export failed");
      } else if (final.status === "cancelled") {
        onError("Export cancelled");
      }
      await onChanged();
    } catch (e) {
      // Source-unreachable: surface the structured message so the user
      // gets the same wording across detect-beep, trim, beep preview,
      // and export.
      const unreachable = asSourceUnreachable(e);
      if (unreachable) {
        onError(unreachable.message);
      } else {
        onError(
          e instanceof ApiError
            ? `Generate failed: ${e.detail}`
            : e instanceof Error
              ? e.message
              : String(e),
        );
      }
    } finally {
      // Clear the local job snapshot a moment after completion so the
      // row's UI returns to its idle state. The JobsPanel still keeps
      // the terminal entry in its history.
      setTimeout(() => setJob(null), 1500);
    }
  };

  const reveal = async (path: string | null) => {
    if (!path) return;
    try {
      await api.revealFile(path);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-3">
      {sourceUnreachable ? (
        <div className="rounded-md border border-status-warning/40 bg-status-warning/5 p-3 text-xs">
          <div className="mb-1 flex items-center gap-1 font-medium">
            <AlertCircle className="size-3.5 text-status-warning" />
            Source video not reachable
          </div>
          <p className="text-muted-foreground">
            The primary's symlink is dangling -- typically because the USB
            drive / SD card is unplugged. CSV and report can still be
            generated from the audit JSON. Reconnect the source to produce
            a fresh trim and FCPXML.
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={trim}
            disabled={busy}
            onChange={(e) => setTrim(e.target.checked)}
          />
          Trim (lossless MP4)
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={csv}
            disabled={busy}
            onChange={(e) => setCsv(e.target.checked)}
          />
          CSV
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={fcpxml}
            disabled={busy}
            onChange={(e) => setFcpxml(e.target.checked)}
          />
          FCPXML
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={reportFlag}
            disabled={busy}
            onChange={(e) => setReportFlag(e.target.checked)}
          />
          Report
        </label>
        <label
          className="flex items-center gap-1.5"
          title={
            row.audit_shot_count > 0
              ? "Pre-render an alpha overlay MOV (N/M, last split, " +
                "running total) and reference it from the FCPXML on V2"
              : "Finish the audit first -- overlay needs at least one shot"
          }
        >
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={overlay}
            disabled={busy || row.audit_shot_count === 0}
            onChange={(e) => setOverlay(e.target.checked)}
          />
          Overlay (alpha MOV)
        </label>
        <Button
          size="sm"
          onClick={generate}
          disabled={
            busy ||
            !row.ready_to_export ||
            (!trim && !csv && !fcpxml && !reportFlag && !overlay)
          }
          title={
            row.ready_to_export
              ? willDegrade
                ? "Source is unreachable; trim and FCPXML will be skipped, " +
                  "CSV/report will still write."
                : "Write the selected artefacts (overwrites if present)"
              : "Finish the audit first -- need at least one shot in the audit JSON"
          }
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {row.has_exports ? "Re-generate" : "Generate"}
        </Button>
        <Button asChild size="sm" variant="outline" title="Open audit screen for this stage">
          <Link to={`/audit/${row.stage_number}`}>
            <PlayCircle className="size-4" />
            Audit
          </Link>
        </Button>
      </div>

      {overlay ? (
        <OverlayFormatPanel
          codec={overlayCodec}
          onCodecChange={setOverlayCodec}
          maxHeight={overlayMaxHeight}
          onMaxHeightChange={setOverlayMaxHeight}
          maxFps={overlayMaxFps}
          onMaxFpsChange={setOverlayMaxFps}
          disabled={busy}
        />
      ) : null}

      <SecondariesPanel
        secondaries={row.secondaries}
        selected={selectedCams}
        onToggle={(id, checked) =>
          setSelectedCams((prev) => {
            const next = new Set(prev);
            if (checked) next.add(id);
            else next.delete(id);
            return next;
          })
        }
        disabled={busy}
        onReveal={reveal}
      />

      <FileLinks row={row} onReveal={reveal} />

      {job ? (
        <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
          {job.status === "succeeded" ? (
            <CheckCircle2 className="size-3.5 text-status-success" />
          ) : job.status === "failed" || job.status === "cancelled" ? (
            <AlertCircle className="size-3.5 text-status-warning" />
          ) : (
            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
          )}
          <span className="text-muted-foreground">{job.message ?? job.status}</span>
        </div>
      ) : null}
    </div>
  );
}

function OverlayFormatPanel({
  codec,
  onCodecChange,
  maxHeight,
  onMaxHeightChange,
  maxFps,
  onMaxFpsChange,
  disabled,
}: {
  codec: OverlayCodec;
  onCodecChange: (next: OverlayCodec) => void;
  maxHeight: number | "source";
  onMaxHeightChange: (next: number | "source") => void;
  maxFps: number | "source";
  onMaxFpsChange: (next: number | "source") => void;
  disabled: boolean;
}) {
  // Width / fps presets cover the common deltas a head-cam + FCP timeline
  // sees in practice (1080p / 720p, 30 / 24 fps); the source-matching
  // option preserves frame-for-frame parity for users who don't want any
  // scaling on the FCP timeline.
  const heightOptions: { value: number | "source"; label: string }[] = [
    { value: "source", label: "Match source" },
    { value: 1080, label: "1080p" },
    { value: 720, label: "720p" },
  ];
  const fpsOptions: { value: number | "source"; label: string }[] = [
    { value: "source", label: "Match source" },
    { value: 30, label: "30 fps" },
    { value: 24, label: "24 fps" },
  ];
  return (
    <div className="space-y-2 rounded-md border border-border/60 bg-muted/10 p-2 text-xs">
      <div className="px-1 font-medium text-muted-foreground">
        Overlay format
        <span className="ml-1 text-[11px]">
          -- Auto picks HEVC w/ alpha on macOS (~10-20x smaller than ProRes
          4444); caps tell FCP to scale a smaller overlay across the timeline
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">Codec</span>
          <select
            className="rounded-md border border-border/60 bg-background px-2 py-1"
            value={codec}
            disabled={disabled}
            onChange={(e) => onCodecChange(e.target.value as OverlayCodec)}
          >
            <option value="auto">Auto (recommended)</option>
            <option value="hevc-alpha">HEVC w/ alpha (smallest, macOS)</option>
            <option value="prores-4444">ProRes 4444 (largest, archival)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">Max height</span>
          <select
            className="rounded-md border border-border/60 bg-background px-2 py-1"
            value={maxHeight === "source" ? "source" : String(maxHeight)}
            disabled={disabled}
            onChange={(e) =>
              onMaxHeightChange(
                e.target.value === "source" ? "source" : Number(e.target.value),
              )
            }
          >
            {heightOptions.map((o) => (
              <option key={String(o.value)} value={String(o.value)}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">Max fps</span>
          <select
            className="rounded-md border border-border/60 bg-background px-2 py-1"
            value={maxFps === "source" ? "source" : String(maxFps)}
            disabled={disabled}
            onChange={(e) =>
              onMaxFpsChange(
                e.target.value === "source" ? "source" : Number(e.target.value),
              )
            }
          >
            {fpsOptions.map((o) => (
              <option key={String(o.value)} value={String(o.value)}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}


function SecondariesPanel({
  secondaries,
  selected,
  onToggle,
  disabled,
  onReveal,
}: {
  secondaries: SecondaryExportStatus[];
  selected: Set<string>;
  onToggle: (videoId: string, checked: boolean) => void;
  disabled: boolean;
  onReveal: (path: string | null) => void;
}) {
  if (secondaries.length === 0) return null;
  const eligibleCount = secondaries.filter(
    (s) => s.has_beep && s.source_reachable,
  ).length;
  const selectedCount = secondaries.filter(
    (s) => s.has_beep && s.source_reachable && selected.has(s.video_id),
  ).length;
  return (
    <div className="space-y-1 rounded-md border border-border/60 bg-muted/10 p-2 text-xs">
      <div className="flex items-center gap-2 px-1 pb-1 text-muted-foreground">
        <Video className="size-3.5" />
        <span className="font-medium">
          Secondary cams ({selectedCount} of {eligibleCount} selected)
        </span>
        <span className="text-[11px]">
          -- each ships its own lossless trim and rides the multi-cam FCPXML
          on lanes V1, V2, ...
        </span>
      </div>
      {secondaries.map((s) => {
        const eligible = s.has_beep && s.source_reachable;
        const checked = selected.has(s.video_id);
        const reason = !s.source_reachable
          ? "Source unreachable -- reconnect external storage"
          : !s.has_beep
            ? "No beep yet -- detect or set the beep on the ingest screen"
            : null;
        return (
          <div
            key={s.video_id}
            className={cn(
              "flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/40 bg-background/40 px-2 py-1",
              !eligible && "opacity-60",
            )}
          >
            <label className="flex min-w-0 items-center gap-2">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={eligible && checked}
                disabled={disabled || !eligible}
                onChange={(e) => onToggle(s.video_id, e.target.checked)}
                title={reason ?? "Include this cam in the next export"}
              />
              <span className="truncate font-mono">{s.label}</span>
              {!s.has_beep ? (
                <Badge variant="statusNotStarted" className="shrink-0">
                  No beep
                </Badge>
              ) : !s.beep_reviewed ? (
                <Badge variant="statusInProgress" className="shrink-0">
                  Beep unreviewed
                </Badge>
              ) : null}
              {!s.source_reachable ? (
                <Badge variant="statusWarning" className="shrink-0 gap-1">
                  <AlertCircle className="size-3" /> Source missing
                </Badge>
              ) : null}
              {s.trim_present ? (
                <Badge variant="statusComplete" className="shrink-0">
                  Trim ready
                </Badge>
              ) : null}
            </label>
            {s.trim_path ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2"
                onClick={() => onReveal(s.trim_path)}
                title="Reveal the per-cam trim in the OS file manager"
              >
                <FolderOpen className="size-3.5" />
              </Button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function FileLinks({
  row,
  onReveal,
}: {
  row: StageExportStatus;
  onReveal: (path: string | null) => void;
}) {
  // Only call out the lossless trim as the deliverable. The audit-mode
  // short-GOP scrub copy in <project>/trimmed/ is a cache file and isn't
  // meant to ship to FCP, so we don't surface it here -- the row falls
  // back to "(not yet generated)" until the user hits Generate with the
  // Trim toggle on.
  const items: { label: string; path: string | null }[] = [
    {
      label: "Trim (lossless MP4)",
      path: row.lossless_trim_present ? row.trimmed_video_path : null,
    },
    { label: "Splits CSV", path: row.csv_path },
    { label: "FCPXML", path: row.fcpxml_path },
    { label: "Report", path: row.report_path },
    { label: "Overlay (alpha MOV)", path: row.overlay_path },
  ];
  return (
    <div className="space-y-1 text-xs">
      {items.map(({ label, path }) => (
        <div
          key={label}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1"
        >
          <div className="flex min-w-0 items-center gap-2">
            {label === "Report" ? (
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <FileBarChart className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="font-medium">{label}:</span>
            <span
              className={cn(
                "truncate font-mono",
                !path && "text-muted-foreground italic",
              )}
              title={path ?? "(not yet generated)"}
            >
              {path ?? "(not yet generated)"}
            </span>
          </div>
          {path ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2"
              onClick={() => onReveal(path)}
              title="Reveal in OS file manager"
            >
              <FolderOpen className="size-3.5" />
            </Button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function StageShotTable({
  row,
  onChanged,
  onError,
}: {
  row: StageExportStatus;
  onChanged: () => Promise<void>;
  onError: (msg: string | null) => void;
}) {
  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const a = await api.getStageAudit(row.stage_number);
      setAudit(a);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [row.stage_number, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return <Skeleton className="h-32" />;
  }
  if (!audit) {
    return (
      <p className="text-xs text-muted-foreground">
        No audit JSON yet. Open the audit screen for this stage to start.
      </p>
    );
  }

  const sortedShots = [...audit.shots].sort((a, b) => a.shot_number - b.shot_number);
  const splits = sortedShots.map((s, i, arr) => {
    if (i === 0) return s.ms_after_beep / 1000;
    return (s.ms_after_beep - arr[i - 1].ms_after_beep) / 1000;
  });

  const saveNote = async (shotNumber: number, value: string) => {
    if (!audit) return;
    const next: StageAudit = {
      ...audit,
      shots: audit.shots.map((s) =>
        s.shot_number === shotNumber ? { ...s, notes: value } : s,
      ),
    };
    try {
      const saved = await api.saveStageAudit(row.stage_number, next);
      setAudit(saved);
      onError(null);
      // The overview's last_export_at doesn't move on note edit, but the
      // notes flow into the next CSV regen -- keep the row's status fresh
      // anyway in case the caller cares.
      void onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (sortedShots.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Audit JSON exists but no shots yet.{" "}
        <Link
          className="text-foreground underline"
          to={`/audit/${row.stage_number}`}
        >
          Open audit screen
        </Link>
        .
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="border-b border-border/60 text-left text-muted-foreground">
            <th className="px-2 py-1 text-right">#</th>
            <th className="px-2 py-1 text-right">t (s)</th>
            <th className="px-2 py-1 text-right">split (s)</th>
            <th className="px-2 py-1">notes</th>
          </tr>
        </thead>
        <tbody>
          {sortedShots.map((s, i) => (
            <tr
              key={s.shot_number}
              className="border-b border-border/30 hover:bg-accent/20"
            >
              <td className="px-2 py-1 text-right">{s.shot_number}</td>
              <td className="px-2 py-1 text-right font-mono">
                {(s.ms_after_beep / 1000).toFixed(3)}
              </td>
              <td className="px-2 py-1 text-right font-mono">
                {splits[i].toFixed(3)}
              </td>
              <td className="px-2 py-1">
                <input
                  type="text"
                  defaultValue={s.notes ?? ""}
                  placeholder="--"
                  className="h-6 w-full rounded border border-input bg-background px-1.5 py-0.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onBlur={(e) => {
                    const value = e.target.value;
                    if ((s.notes ?? "") !== value) void saveNote(s.shot_number, value);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
        <ExternalLink className="size-3" />
        Notes save on blur or Enter; flow into the next CSV regen.
      </p>
    </div>
  );
}

function MatchExportDialog({
  stageNumbers,
  headPadCap,
  tailPadCap,
  defaultProjectName,
  stages,
  onCancel,
  onSuccess,
}: {
  stageNumbers: number[];
  headPadCap: number;
  tailPadCap: number;
  defaultProjectName: string;
  stages: StageExportStatus[];
  onCancel: () => void;
  onSuccess: (result: MatchExportResult) => void;
}) {
  // Default to "Full" -- mirrors the per-stage export's defaults so the
  // user has to opt in to a tighter cut.
  const [preset, setPreset] = useState<PaddingPreset>("full");
  const [headPad, setHeadPad] = useState<number>(PADDING_PRESETS.full.head);
  const [tailPad, setTailPad] = useState<number>(PADDING_PRESETS.full.tail);
  const [includeSecondaries, setIncludeSecondaries] = useState(true);
  // Issue #193. ``stacked`` mirrors today's behaviour (every secondary
  // covers the one below at full frame). ``pip-corners`` adds an FCPXML
  // adjust-transform to each cam so they land in rotating corners at 25%
  // scale, ready for review without manual sizing in FCP.
  const [pipLayout, setPipLayout] = useState<"stacked" | "pip-corners">(
    "stacked",
  );
  // Issue #197. ``fcpxml`` is Final Cut Pro 1.10 (the editor splitsmith was
  // built around). ``fcp7xml`` is the legacy xmeml format Premiere Pro and
  // DaVinci Resolve import; coverage matches the FCPXML renderer for
  // primary / secondaries / PiP / overlay / markers, with transitions and
  // titles deferred per #195 / #196.
  const [outputFormat, setOutputFormat] = useState<
    "fcpxml" | "fcp7xml" | "mp4"
  >("fcpxml");
  // Issue #195. ``"none"`` is today's hard-cut stitch. Cross-dissolve
  // is the most-used transition in IPSC match recaps; dip-to-color is
  // for stylised cuts. Only FCPXML emits transitions today.
  const [transitionKind, setTransitionKind] = useState<
    "none" | "cross-dissolve" | "dip-to-color"
  >("none");
  const [transitionDurationSeconds, setTransitionDurationSeconds] =
    useState<number>(0.5);
  // Issue #196. ``"none"`` keeps the timeline title-less. ``"slate"``
  // adds a pre-stage card on the spine (extends total duration);
  // ``"lower-third"`` overlays a small text clip at each stage's
  // start (no spine extension). Only FCPXML emits titles today.
  const [titleKind, setTitleKind] = useState<
    "none" | "slate" | "lower-third"
  >("none");
  const [titleDurationSeconds, setTitleDurationSeconds] =
    useState<number>(1.5);
  // Issue #173. Optional intro / outro video clips. Empty string =
  // disabled. The server expands ``~`` and probes the file; missing
  // paths surface as anomalies (non-fatal) rather than failing the
  // whole export.
  const [introPath, setIntroPath] = useState<string>("");
  const [outroPath, setOutroPath] = useState<string>("");
  // Issue #204. YouTube sidecar writes match-youtube.json + .srt
  // alongside the export and (FCPXML route only) embeds chapter
  // markers in the timeline so they round-trip through FCP into
  // the MP4 chapter atom.
  const [youtubeSidecar, setYoutubeSidecar] = useState<boolean>(false);
  // Issue #204 layer 2. YouTube-tuned encode preset for the MP4
  // renderer (H.264 High, CRF 18, 2s closed GOP, BT.709 tags, AAC
  // 384k 48k stereo, +faststart). Only meaningful when the chosen
  // output format is "mp4"; the request layer surfaces an anomaly
  // if it's set against another renderer.
  const [youtubePreset, setYoutubePreset] = useState<boolean>(false);
  // Overlay defaults off because the per-frame PIL + ffmpeg render is the
  // slowest writer; opt in per export. Mirrors the per-stage Generate's
  // default.
  const [includeOverlay, setIncludeOverlay] = useState(false);
  // Match-level overlay format. Mirrors the per-stage controls: any non-
  // default value forces a re-render even if a stale overlay sits on
  // disk, so the user's choice here actually applies.
  const [overlayCodec, setOverlayCodec] = useState<OverlayCodec>("auto");
  const [overlayMaxHeight, setOverlayMaxHeight] = useState<number | "source">(
    "source",
  );
  const [overlayMaxFps, setOverlayMaxFps] = useState<number | "source">(
    "source",
  );
  const [projectName, setProjectName] = useState(defaultProjectName);
  const [job, setJob] = useState<Job | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const busy = job?.status === "pending" || job?.status === "running";

  // Issue #198. Fetch available templates on mount so the dialog can
  // offer them in a dropdown. Failures don't block the dialog --
  // templates are an additive feature, the manual controls still
  // work without them.
  const [templates, setTemplates] = useState<MatchExportTemplateEntry[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("");
  useEffect(() => {
    let cancelled = false;
    api
      .getMatchTemplates()
      .then((res) => {
        if (!cancelled) setTemplates(res.templates);
      })
      .catch(() => {
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const applyTemplate = (id: string) => {
    setSelectedTemplateId(id);
    const entry = templates.find((t) => t.id === id);
    if (!entry) return;
    const t = entry.template;
    if (t.head_pad_seconds !== undefined) setHeadPad(t.head_pad_seconds);
    if (t.tail_pad_seconds !== undefined) setTailPad(t.tail_pad_seconds);
    if (t.head_pad_seconds !== undefined || t.tail_pad_seconds !== undefined) {
      // The user no longer matches one of the named presets -- reflect
      // that so the preset chips don't claim a stale selection.
      setPreset("custom");
    }
    if (t.include_secondaries !== undefined)
      setIncludeSecondaries(t.include_secondaries);
    if (t.include_overlay !== undefined) setIncludeOverlay(t.include_overlay);
    if (t.pip_layout !== undefined) setPipLayout(t.pip_layout);
    if (t.output_format !== undefined) setOutputFormat(t.output_format);
    if (t.transition_kind !== undefined) setTransitionKind(t.transition_kind);
    if (t.transition_duration_seconds !== undefined)
      setTransitionDurationSeconds(t.transition_duration_seconds);
    if (t.title_kind !== undefined) setTitleKind(t.title_kind);
    if (t.title_duration_seconds !== undefined)
      setTitleDurationSeconds(t.title_duration_seconds);
    if (t.intro_path !== undefined) setIntroPath(t.intro_path);
    if (t.outro_path !== undefined) setOutroPath(t.outro_path);
  };

  const choosePreset = (next: PaddingPreset) => {
    setPreset(next);
    if (next !== "custom") {
      const cfg = PADDING_PRESETS[next];
      // Cap presets at the project's pre/post buffer in case it was
      // configured below the preset's nominal value.
      setHeadPad(Math.min(cfg.head, headPadCap));
      setTailPad(Math.min(cfg.tail, tailPadCap));
    }
  };

  // Any flag that has at least one stage in the selection covers it. If
  // none of the selected stages have e.g. a secondary, the toggle is
  // disabled (the matching backend would silently ignore an empty list,
  // but it reads better to grey it out).
  const anySelectedStageHasSecondaries = stages
    .filter((s) => stageNumbers.includes(s.stage_number))
    .some((s) => s.secondaries.length > 0);

  const submit = async () => {
    // Errors during submit live inline in the dialog so the user sees
    // them in context. Page-level ``onError`` is reserved for failures
    // that occur after the dialog has closed (or that the user explicitly
    // dismisses by closing the dialog).
    setDialogError(null);
    try {
      const submitted = await api.exportMatch({
        stage_numbers: stageNumbers,
        head_pad_seconds: headPad,
        tail_pad_seconds: tailPad,
        include_secondaries: includeSecondaries,
        pip_layout: pipLayout,
        output_format: outputFormat,
        transition_kind: transitionKind,
        transition_duration_seconds: transitionDurationSeconds,
        title_kind: titleKind,
        title_duration_seconds: titleDurationSeconds,
        intro_path: introPath || undefined,
        outro_path: outroPath || undefined,
        youtube_sidecar: youtubeSidecar,
        youtube_preset: youtubePreset,
        include_overlay: includeOverlay,
        overlay_codec: overlayCodec,
        overlay_max_height:
          overlayMaxHeight === "source" ? null : overlayMaxHeight,
        overlay_max_fps: overlayMaxFps === "source" ? null : overlayMaxFps,
        project_name: projectName,
      });
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "failed") {
        setDialogError(final.error ?? "Match export failed");
        return;
      }
      if (final.status === "cancelled") {
        setDialogError("Match export cancelled");
        return;
      }
      const result = final.result as
        | {
            fcpxml_path: string;
            stage_count: number;
            duration_seconds: number;
            anomalies: string[];
          }
        | null;
      if (!result) {
        setDialogError("Match export finished without a result payload");
        return;
      }
      onSuccess(result);
    } catch (e) {
      const unreachable = asSourceUnreachable(e);
      if (unreachable) {
        setDialogError(unreachable.message);
      } else {
        setDialogError(
          e instanceof ApiError
            ? `Match export failed: ${e.detail}`
            : e instanceof Error
              ? e.message
              : String(e),
        );
      }
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="match-export-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4"
      onClick={onCancel}
    >
      <Card
        className="w-full max-w-xl shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader>
          <CardTitle id="match-export-title" className="flex items-center gap-2">
            <Film className="size-5" />
            Export match
          </CardTitle>
          <CardDescription>
            Stitches {stageNumbers.length} stage
            {stageNumbers.length === 1 ? "" : "s"} into one FCPXML in stage
            order. Composes from existing trims; no re-encoding.
          </CardDescription>
          {(() => {
            // #214 -- export is permissive about empty shots. Surface a
            // friendly inline note when any selected stage has zero
            // audited shots so the user knows what to expect (no shot
            // markers, CSV, or overlay for those stages) without being
            // gated.
            const shotless = stages.filter(
              (s) =>
                stageNumbers.includes(s.stage_number) &&
                s.audit_shot_count === 0,
            );
            if (shotless.length === 0) return null;
            const stageList = shotless
              .map((s) => `Stage ${s.stage_number}`)
              .join(", ");
            const verb = shotless.length === 1 ? "exports" : "export";
            return (
              <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                {stageList} {verb} without shot markers, CSV, or overlay
                (no shots audited). Detection is optional -- you can still
                export the trim and stitched timeline.
              </div>
            );
          })()}
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {templates.length > 0 && (
            <section className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Template
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className="rounded border border-border bg-background px-2 py-1 text-sm"
                  value={selectedTemplateId}
                  disabled={busy}
                  onChange={(e) => applyTemplate(e.target.value)}
                >
                  <option value="">(none) -- manual</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.template.name ?? t.id}
                      {t.source === "user" ? "" : " (built-in)"}
                    </option>
                  ))}
                </select>
                {selectedTemplateId &&
                  templates.find((t) => t.id === selectedTemplateId)?.template
                    .description && (
                    <span className="text-xs text-muted-foreground">
                      {
                        templates.find((t) => t.id === selectedTemplateId)
                          ?.template.description
                      }
                    </span>
                  )}
              </div>
            </section>
          )}
          <section className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Padding
            </div>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(PADDING_PRESETS) as Array<keyof typeof PADDING_PRESETS>).map(
                (key) => (
                  <label
                    key={key}
                    className={cn(
                      "flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
                      preset === key && "border-primary bg-primary/10",
                    )}
                    title={PADDING_PRESETS[key].help}
                  >
                    <input
                      type="radio"
                      name="match-export-preset"
                      checked={preset === key}
                      onChange={() => choosePreset(key)}
                      className="accent-primary"
                    />
                    {PADDING_PRESETS[key].label}
                    <span className="text-muted-foreground">
                      ({PADDING_PRESETS[key].head}s / {PADDING_PRESETS[key].tail}s)
                    </span>
                  </label>
                ),
              )}
              <label
                className={cn(
                  "flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
                  preset === "custom" && "border-primary bg-primary/10",
                )}
              >
                <input
                  type="radio"
                  name="match-export-preset"
                  checked={preset === "custom"}
                  onChange={() => choosePreset("custom")}
                  className="accent-primary"
                />
                Custom
              </label>
            </div>
            <div
              className={cn(
                "grid grid-cols-2 gap-3 pt-1",
                preset !== "custom" && "opacity-60",
              )}
            >
              <label className="space-y-1 text-xs">
                <div className="flex items-center justify-between">
                  <span>Head (before beep)</span>
                  <span className="font-mono tabular-nums">
                    {headPad.toFixed(2)}s
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={headPadCap}
                  step={0.1}
                  value={headPad}
                  disabled={preset !== "custom" || busy}
                  onChange={(e) => setHeadPad(parseFloat(e.target.value))}
                  className="w-full accent-primary"
                />
              </label>
              <label className="space-y-1 text-xs">
                <div className="flex items-center justify-between">
                  <span>Tail (after final shot)</span>
                  <span className="font-mono tabular-nums">
                    {tailPad.toFixed(2)}s
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={tailPadCap}
                  step={0.1}
                  value={tailPad}
                  disabled={preset !== "custom" || busy}
                  onChange={(e) => setTailPad(parseFloat(e.target.value))}
                  className="w-full accent-primary"
                />
              </label>
            </div>
          </section>

          <section className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Tracks
            </div>
            <div className="flex flex-wrap gap-3 text-xs">
              <label
                className={cn(
                  "flex items-center gap-1.5",
                  !anySelectedStageHasSecondaries && "opacity-60",
                )}
                title={
                  anySelectedStageHasSecondaries
                    ? "Attach each stage's per-cam trims as connected clips"
                    : "None of the selected stages have secondary cams"
                }
              >
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={includeSecondaries && anySelectedStageHasSecondaries}
                  disabled={!anySelectedStageHasSecondaries || busy}
                  onChange={(e) => setIncludeSecondaries(e.target.checked)}
                />
                Include secondary cams
              </label>
              {includeSecondaries && anySelectedStageHasSecondaries && (
                <label
                  className="ml-6 flex items-center gap-1.5"
                  title="Stacked: cams cover the primary at full size (today's default). PiP corners: each cam lands as a 25% inset in a rotating corner so the headcam stays visible."
                >
                  Layout
                  <select
                    className="rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                    value={pipLayout}
                    disabled={busy}
                    onChange={(e) =>
                      setPipLayout(
                        e.target.value as "stacked" | "pip-corners",
                      )
                    }
                  >
                    <option value="stacked">Stacked full-frame</option>
                    <option value="pip-corners">PiP corners</option>
                  </select>
                </label>
              )}
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={includeOverlay}
                  disabled={busy}
                  onChange={(e) => setIncludeOverlay(e.target.checked)}
                />
                Include overlay (when present)
              </label>
              <label
                className="flex items-center gap-1.5"
                title="FCPXML: Final Cut Pro 1.10 (default). FCP7 XML: legacy xmeml file importable into Premiere Pro and DaVinci Resolve."
              >
                Format
                <select
                  className="rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                  value={outputFormat}
                  disabled={busy}
                  onChange={(e) =>
                    setOutputFormat(
                      e.target.value as "fcpxml" | "fcp7xml" | "mp4",
                    )
                  }
                >
                  <option value="fcpxml">FCPXML (Final Cut Pro)</option>
                  <option value="fcp7xml">FCP7 XML (Premiere / DaVinci)</option>
                  <option value="mp4">MP4 (baked, ffmpeg)</option>
                </select>
              </label>
              <label
                className="flex items-center gap-1.5"
                title="Cross-dissolve / dip-to-color between consecutive stages. FCPXML only; FCP7 XML still hard-cuts."
              >
                Transition
                <select
                  className="rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                  value={transitionKind}
                  disabled={busy}
                  onChange={(e) =>
                    setTransitionKind(
                      e.target.value as
                        | "none"
                        | "cross-dissolve"
                        | "dip-to-color",
                    )
                  }
                >
                  <option value="none">None (hard cuts)</option>
                  <option value="cross-dissolve">Cross dissolve</option>
                  <option value="dip-to-color">Dip to color</option>
                </select>
              </label>
              {transitionKind !== "none" && (
                <label
                  className="flex items-center gap-1.5"
                  title="Total transition length. Each adjacent stage's effective window must contain at least half this value."
                >
                  Duration
                  <input
                    type="number"
                    min={0.1}
                    max={3.0}
                    step={0.1}
                    className="w-16 rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                    value={transitionDurationSeconds}
                    disabled={busy}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!Number.isNaN(v) && v > 0)
                        setTransitionDurationSeconds(v);
                    }}
                  />
                  s
                </label>
              )}
              <label
                className="flex items-center gap-1.5"
                title="Slate: pre-stage card on the timeline (extends duration). Lower-third: connected text overlay at the start of each primary. FCPXML only."
              >
                Titles
                <select
                  className="rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                  value={titleKind}
                  disabled={busy}
                  onChange={(e) =>
                    setTitleKind(
                      e.target.value as "none" | "slate" | "lower-third",
                    )
                  }
                >
                  <option value="none">None</option>
                  <option value="slate">Slate (pre-stage)</option>
                  <option value="lower-third">Lower third</option>
                </select>
              </label>
              {titleKind !== "none" && (
                <label
                  className="flex items-center gap-1.5"
                  title="Title duration in seconds. Slates add this to the total timeline; lower-thirds overlay for this many seconds at the start of each stage."
                >
                  Duration
                  <input
                    type="number"
                    min={0.5}
                    max={10.0}
                    step={0.5}
                    className="w-16 rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                    value={titleDurationSeconds}
                    disabled={busy}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!Number.isNaN(v) && v > 0)
                        setTitleDurationSeconds(v);
                    }}
                  />
                  s
                </label>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <label
                className="flex items-center gap-1.5"
                title="Optional video clip placed before the first stage. Frame rate must match the timeline. ~ expands to your home directory. Leave blank to skip."
              >
                Intro clip
                <input
                  type="text"
                  placeholder="~/match-intro.mov"
                  className="flex-1 rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                  value={introPath}
                  disabled={busy}
                  onChange={(e) => setIntroPath(e.target.value)}
                />
              </label>
              <label
                className="flex items-center gap-1.5"
                title="Optional video clip placed after the last stage. Same constraints as the intro."
              >
                Outro clip
                <input
                  type="text"
                  placeholder="~/match-outro.mov"
                  className="flex-1 rounded border border-border bg-background px-1.5 py-0.5 text-sm"
                  value={outroPath}
                  disabled={busy}
                  onChange={(e) => setOutroPath(e.target.value)}
                />
              </label>
              <label
                className="flex items-center gap-1.5"
                title="Generate a YouTube-shaped JSON sidecar (title, chapter timestamps, tags) plus a per-shot .srt. The FCPXML route also embeds chapter markers so they round-trip into the MP4 chapter atom."
              >
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={youtubeSidecar}
                  disabled={busy}
                  onChange={(e) => setYoutubeSidecar(e.target.checked)}
                />
                YouTube sidecar (chapters + .srt)
              </label>
              <label
                className="flex items-center gap-1.5"
                title="Encode the MP4 with YouTube's recommended H.264 profile, 2s closed GOP, BT.709 colour tags, and AAC 384k 48k stereo. Only applies when output format is MP4."
              >
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={youtubePreset && outputFormat === "mp4"}
                  disabled={busy || outputFormat !== "mp4"}
                  onChange={(e) => setYoutubePreset(e.target.checked)}
                />
                YouTube encode preset (MP4 only)
              </label>
            </div>
            {includeOverlay ? (
              <OverlayFormatPanel
                codec={overlayCodec}
                onCodecChange={setOverlayCodec}
                maxHeight={overlayMaxHeight}
                onMaxHeightChange={setOverlayMaxHeight}
                maxFps={overlayMaxFps}
                onMaxFpsChange={setOverlayMaxFps}
                disabled={busy}
              />
            ) : null}
          </section>

          <section className="space-y-1">
            <label className="text-xs">
              Project name
              <input
                type="text"
                value={projectName}
                disabled={busy}
                onChange={(e) => setProjectName(e.target.value)}
                className="mt-1 block w-full rounded border border-input bg-background px-2 py-1 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </label>
            <p className="text-[11px] text-muted-foreground">
              Output file: <code>exports/&lt;slug&gt;-match.fcpxml</code>.
              Re-running overwrites.
            </p>
          </section>

          {job && !dialogError ? (
            <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs">
              <div className="flex items-center gap-2 font-medium">
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : null}
                {job.message ?? "Running..."}
              </div>
              {job.progress != null ? (
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-primary transition-[width]"
                    style={{ width: `${Math.round(job.progress * 100)}%` }}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          {dialogError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs">
              <div className="mb-1 flex items-center gap-1 font-medium text-destructive">
                <AlertCircle className="size-3.5" />
                Match export failed
              </div>
              <p className="text-muted-foreground">{dialogError}</p>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
            <Button variant="ghost" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={busy || !projectName.trim()}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Film className="size-4" />
              )}
              Export match
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
