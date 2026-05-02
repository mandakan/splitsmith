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

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileBarChart,
  FileText,
  FolderOpen,
  Loader2,
  PlayCircle,
  RefreshCw,
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
  type MatchProject,
  type StageAudit,
  type StageExportStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function Export() {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [overview, setOverview] = useState<ExportOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

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

      <div className="space-y-3">
        {(overview?.stages ?? []).map((row) => (
          <StageRow
            key={row.stage_number}
            row={row}
            onChanged={reload}
            onError={setError}
          />
        ))}
      </div>
    </div>
  );
}

function StageRow({
  row,
  onChanged,
  onError,
}: {
  row: StageExportStatus;
  onChanged: () => Promise<void>;
  onError: (msg: string | null) => void;
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
      const submitted = await api.exportStage(row.stage_number, {
        write_trim: trim,
        write_csv: csv,
        write_fcpxml: fcpxml,
        write_report: reportFlag,
        write_overlay: overlay,
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
