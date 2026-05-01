/**
 * Ingest screen (#13).
 *
 * Lets the user:
 *   1. Drop in a SSI Scoreboard JSON to populate the stage list
 *   2. Scan a folder of videos -- the backend symlinks them into <project>/raw/
 *      and runs video_match.py to suggest primary assignments
 *   3. Reassign videos: make primary / secondary / ignored, or unassign
 *
 * Re-enterable: the ingest page is the persistent stage-assignment view, not
 * a one-shot wizard. The user returns here to add more videos (e.g. friend's
 * bay-cam dropped in days later) without losing audit work.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Crosshair,
  FileJson,
  FolderInput,
  Loader2,
  Trash2,
  Video as VideoIcon,
  XCircle,
} from "lucide-react";

import { BeepSection } from "@/components/BeepSection";
import { FolderPicker } from "@/components/FolderPicker";
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
import { ApiError, api, type MatchProject, type StageEntry, type StageVideo, type VideoRole } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Ingest() {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      setProject(await api.getProject());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleScoreboard = async (file: File) => {
    setBusy(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const overwrite = (project?.stages.length ?? 0) > 0;
      if (overwrite) {
        const ok = window.confirm(
          "This project already has stages. Importing will replace them and orphan any current video assignments. Continue?",
        );
        if (!ok) {
          setBusy(false);
          return;
        }
      }
      const updated = await api.importScoreboard(data, overwrite);
      setProject(updated);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleScan = async (sourceDir: string) => {
    setBusy(true);
    try {
      await api.scanVideos(sourceDir, true);
      await reload();
      setError(null);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`Scan failed: ${e.detail}`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const move = async (videoPath: string, toStage: number | null, role: VideoRole) => {
    setBusy(true);
    try {
      const updated = await api.moveAssignment(videoPath, toStage, role);
      setProject(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!project && !error) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-7 w-1/3" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Ingest</h1>
        <p className="text-sm text-muted-foreground">
          Drop a scoreboard JSON, scan a folder of videos, confirm assignments. Returnable any time to add more videos to existing stages.
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

      <ScoreboardSection
        project={project}
        busy={busy}
        onScoreboard={handleScoreboard}
      />

      <ScanSection
        disabled={busy || !project || project.stages.length === 0}
        initialPath={project?.last_scanned_dir ?? null}
        onScan={handleScan}
      />

      {project ? (
        <>
          <UnassignedSection
            project={project}
            busy={busy}
            onAssign={(path, stage, role) => move(path, stage, role)}
          />
          <StagesSection
            project={project}
            busy={busy}
            setBusy={setBusy}
            setError={setError}
            onProjectUpdate={setProject}
            onMove={move}
          />
        </>
      ) : null}
    </div>
  );
}

function ScoreboardSection({
  project,
  busy,
  onScoreboard,
}: {
  project: MatchProject | null;
  busy: boolean;
  onScoreboard: (f: File) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileJson className="size-5" />
          Scoreboard
        </CardTitle>
        <CardDescription>
          {project?.stages.length
            ? `${project.stages.length} stages loaded for ${project.competitor_name ?? "the primary competitor"}.`
            : "No stages yet. Drop in an SSI Scoreboard JSON to load them."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <FileDropZone
          accept=".json,application/json"
          label={
            project?.stages.length
              ? "Replace scoreboard JSON (warns first)"
              : "Drop or pick an SSI Scoreboard JSON"
          }
          icon={<FileJson className="size-5" />}
          disabled={busy}
          onFile={onScoreboard}
        />
        {project?.scoreboard_match_id ? (
          <p className="text-xs text-muted-foreground">
            Match id: <code>{project.scoreboard_match_id}</code>
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScanSection({
  disabled,
  initialPath,
  onScan,
}: {
  disabled: boolean;
  initialPath: string | null;
  onScan: (sourceDir: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderInput className="size-5" />
          Scan video folder
        </CardTitle>
        <CardDescription>
          Pick a folder containing the match's MP4/MOV files. Videos are
          symlinked into <code>&lt;project&gt;/raw/</code> — nothing is uploaded.
          Confident matches are auto-assigned as primary.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!open ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={disabled} onClick={() => setOpen(true)}>
              <FolderInput />
              Browse for folder
            </Button>
            {initialPath ? (
              <>
                <Button
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onScan(initialPath)}
                  title={`Re-scan ${initialPath}`}
                >
                  Re-scan {shortPath(initialPath)}
                </Button>
                <span className="font-mono text-xs text-muted-foreground" title={initialPath}>
                  last: {initialPath}
                </span>
              </>
            ) : null}
          </div>
        ) : (
          <FolderPicker
            initialPath={initialPath}
            onSelect={(p) => {
              setOpen(false);
              onScan(p);
            }}
            onCancel={() => setOpen(false)}
          />
        )}
        {disabled ? (
          <p className="text-xs text-muted-foreground">
            Load a scoreboard first.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function shortPath(p: string): string {
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 2) return p;
  return `…/${parts.slice(-2).join("/")}`;
}

function UnassignedSection({
  project,
  busy,
  onAssign,
}: {
  project: MatchProject;
  busy: boolean;
  onAssign: (videoPath: string, stage: number | null, role: VideoRole) => void;
}) {
  if (project.unassigned_videos.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <VideoIcon className="size-5" />
          Unassigned videos · {project.unassigned_videos.length}
        </CardTitle>
        <CardDescription>
          Drag onto a stage, or use the menu to pick a stage.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {project.unassigned_videos.map((v) => (
          <div
            key={v.path}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm"
          >
            <div className="flex items-center gap-2 font-mono text-xs">
              <VideoIcon className="size-3.5 text-muted-foreground" />
              {v.path}
            </div>
            <div className="flex flex-wrap gap-1">
              {project.stages.map((s) => (
                <Button
                  key={s.stage_number}
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => onAssign(v.path, s.stage_number, "secondary")}
                  title={`Assign to stage ${s.stage_number}: ${s.stage_name}`}
                >
                  → S{s.stage_number}
                </Button>
              ))}
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => onAssign(v.path, null, "secondary")}
                title="Mark as ignored (warmup, neighbour bay, etc.)"
              >
                <Trash2 />
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function StagesSection({
  project,
  busy,
  setBusy,
  setError,
  onProjectUpdate,
  onMove,
}: {
  project: MatchProject;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
}) {
  if (project.stages.length === 0) return null;

  // Compute primary-conflict highlighting: a video appearing as primary on more
  // than one stage. Belt-and-braces; the data model prevents this today, but
  // surface it just in case some future flow allows it.
  const primaryCounts = new Map<string, number>();
  for (const s of project.stages) {
    for (const v of s.videos) {
      if (v.role === "primary") {
        primaryCounts.set(v.path, (primaryCounts.get(v.path) ?? 0) + 1);
      }
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight">Stages</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {project.stages.map((s) => (
          <StageCard
            key={s.stage_number}
            stage={s}
            allStages={project.stages}
            busy={busy}
            primaryCounts={primaryCounts}
            setBusy={setBusy}
            setError={setError}
            onProjectUpdate={onProjectUpdate}
            onMove={onMove}
          />
        ))}
      </div>
    </section>
  );
}

function StageCard({
  stage,
  allStages,
  busy,
  primaryCounts,
  setBusy,
  setError,
  onProjectUpdate,
  onMove,
}: {
  stage: StageEntry;
  allStages: StageEntry[];
  busy: boolean;
  primaryCounts: Map<string, number>;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
}) {
  const primary = stage.videos.find((v) => v.role === "primary");
  const secondaries = stage.videos.filter((v) => v.role === "secondary");
  const ignored = stage.videos.filter((v) => v.role === "ignored");
  const hasConflict = primary && (primaryCounts.get(primary.path) ?? 0) > 1;

  return (
    <Card className={cn(hasConflict && "border-status-warning")}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">
              Stage {stage.stage_number}: {stage.stage_name}
            </CardTitle>
            <CardDescription className="font-mono tabular-nums">
              {stage.time_seconds.toFixed(2)}s ·{" "}
              {stage.scorecard_updated_at
                ? new Date(stage.scorecard_updated_at).toLocaleString()
                : "no scorecard time"}
            </CardDescription>
          </div>
          <StatusGlyph stage={stage} />
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {stage.videos.length === 0 ? (
          <p className="text-sm text-muted-foreground">No videos assigned.</p>
        ) : null}
        {primary ? (
          <>
            <VideoRow
              video={primary}
              badge={
                <Badge
                  variant={hasConflict ? "statusWarning" : "statusInProgress"}
                  className="gap-1"
                >
                  <Crosshair className="size-3" /> Primary
                </Badge>
              }
              actions={
                <RoleActions
                  video={primary}
                  stage={stage}
                  allStages={allStages}
                  busy={busy}
                  currentRole="primary"
                  onMove={onMove}
                />
              }
            />
            <BeepSection
              stageNumber={stage.stage_number}
              primary={primary}
              busy={busy}
              setBusy={setBusy}
              setError={setError}
              onProjectUpdate={onProjectUpdate}
            />
          </>
        ) : null}
        {secondaries.map((v) => (
          <VideoRow
            key={v.path}
            video={v}
            badge={<Badge variant="secondary">Secondary</Badge>}
            actions={
              <RoleActions
                video={v}
                stage={stage}
                allStages={allStages}
                busy={busy}
                currentRole="secondary"
                onMove={onMove}
              />
            }
          />
        ))}
        {ignored.map((v) => (
          <VideoRow
            key={v.path}
            video={v}
            badge={
              <Badge variant="outline" className="gap-1 opacity-70">
                <XCircle className="size-3" /> Ignored
              </Badge>
            }
            actions={
              <RoleActions
                video={v}
                stage={stage}
                allStages={allStages}
                busy={busy}
                currentRole="ignored"
                onMove={onMove}
              />
            }
          />
        ))}
      </CardContent>
    </Card>
  );
}

function VideoRow({
  video,
  badge,
  actions,
}: {
  video: StageVideo;
  badge: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5">
      <div className="flex min-w-0 items-center gap-2 text-xs">
        {badge}
        <span className="truncate font-mono" title={video.path}>
          {video.path.split("/").pop()}
        </span>
        {video.processed.beep ? (
          <span
            className="text-muted-foreground"
            title={`beep at ${video.beep_time?.toFixed(3) ?? "?"}s`}
          >
            <CheckCircle2 className="size-3.5" />
          </span>
        ) : (
          <Loader2
            className="size-3.5 text-muted-foreground"
            aria-label="beep detection pending"
          />
        )}
      </div>
      <div className="flex gap-1">{actions}</div>
    </div>
  );
}

function RoleActions({
  video,
  stage,
  allStages,
  busy,
  currentRole,
  onMove,
}: {
  video: StageVideo;
  stage: StageEntry;
  allStages: StageEntry[];
  busy: boolean;
  currentRole: VideoRole;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
}) {
  return (
    <>
      {currentRole !== "primary" ? (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => onMove(video.path, stage.stage_number, "primary")}
          title="Make primary (audit truth)"
        >
          Make primary
        </Button>
      ) : null}
      {currentRole !== "ignored" ? (
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={() => onMove(video.path, stage.stage_number, "ignored")}
          title="Mark as ignored (kept on stage but skipped by pipeline)"
        >
          Ignore
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        disabled={busy}
        onClick={() => onMove(video.path, null, "secondary")}
        title="Unassign (move back to tray)"
      >
        Unassign
      </Button>
      {allStages.length > 1 ? (
        <select
          aria-label="Move to a different stage"
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          disabled={busy}
          value=""
          onChange={(e) => {
            const next = e.target.value;
            if (next === "") return;
            onMove(video.path, Number(next), "secondary");
          }}
        >
          <option value="">→ stage…</option>
          {allStages
            .filter((s) => s.stage_number !== stage.stage_number)
            .map((s) => (
              <option key={s.stage_number} value={s.stage_number}>
                S{s.stage_number} — {s.stage_name}
              </option>
            ))}
        </select>
      ) : null}
    </>
  );
}

function StatusGlyph({ stage }: { stage: StageEntry }) {
  if (stage.skipped) {
    return (
      <Badge variant="outline" className="gap-1">
        <XCircle className="size-3" /> Skipped
      </Badge>
    );
  }
  if (!stage.videos.length) {
    return (
      <Badge variant="statusNotStarted" className="gap-1">
        ○ No videos
      </Badge>
    );
  }
  if (stage.videos.find((v) => v.role === "primary")) {
    return (
      <Badge variant="statusComplete" className="gap-1">
        <CheckCircle2 className="size-3" /> Ready
      </Badge>
    );
  }
  return (
    <Badge variant="statusWarning" className="gap-1">
      ▲ No primary
    </Badge>
  );
}

function FileDropZone({
  accept,
  label,
  icon,
  disabled,
  onFile,
}: {
  accept: string;
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
  onFile: (f: File) => void;
}) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-muted/20 px-4 py-6 text-sm transition-colors",
        over && "border-ring bg-muted/40",
        disabled && "pointer-events-none opacity-50",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) onFile(file);
      }}
    >
      {icon}
      <span className="text-foreground">{label}</span>
      <span className="text-xs text-muted-foreground">drag & drop, or click to browse</span>
      <input
        type="file"
        accept={accept}
        className="sr-only"
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
    </label>
  );
}
