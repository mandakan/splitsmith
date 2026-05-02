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
  CalendarDays,
  CheckCircle2,
  Crosshair,
  FileJson,
  FolderInput,
  Loader2,
  PlayCircle,
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
import {
  ApiError,
  api,
  type MatchProject,
  type NonEmptyOldDirsDetail,
  type StageEntry,
  type StageVideo,
  type VideoRole,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function Ingest() {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<{
    video: StageVideo;
    stage: StageEntry | null;
  } | null>(null);

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
      // Real (non-placeholder) stages need explicit overwrite; placeholders
      // are overlaid automatically without losing video assignments.
      const realStages = (project?.stages ?? []).filter((s) => !s.placeholder);
      const overwrite = realStages.length > 0;
      if (overwrite) {
        const ok = window.confirm(
          "This project already has scoreboard-backed stages. Importing will replace them and orphan any current video assignments. Continue?",
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

  const handleStartFromVideos = async (
    files: { path: string; mtime: number | null }[],
    stageCount: number,
    matchName: string | null,
    matchDate: string | null,
  ) => {
    setBusy(true);
    try {
      await api.createPlaceholderStages({
        stage_count: stageCount,
        match_name: matchName,
        match_date: matchDate,
      });
      await api.scanFiles(
        files.map((f) => f.path),
        false,
      );
      await reload();
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

  const handleScanFiles = async (sourcePaths: string[]) => {
    setBusy(true);
    try {
      await api.scanFiles(sourcePaths, true);
      await reload();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (videoPath: string, resetAudit: boolean) => {
    setBusy(true);
    try {
      const resp = await api.removeVideo(videoPath, resetAudit);
      setProject(resp.project);
      setRemoveTarget(null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
          Bootstrap from a scoreboard JSON or just point at your videos -- a
          real scoreboard can be uploaded later and will overlay onto the
          placeholder stages without losing assignments.
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

      {project && project.stages.length === 0 ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <ScoreboardSection
            project={project}
            busy={busy}
            onScoreboard={handleScoreboard}
          />
          <StartFromVideosSection
            project={project}
            busy={busy}
            onSubmit={handleStartFromVideos}
          />
        </div>
      ) : (
        <ScoreboardSection
          project={project}
          busy={busy}
          onScoreboard={handleScoreboard}
        />
      )}

      <ScanSection
        disabled={busy || !project || project.stages.length === 0}
        initialPath={project?.last_scanned_dir ?? null}
        onScan={handleScan}
        onScanFiles={handleScanFiles}
      />

      {project ? (
        <SettingsSection project={project} busy={busy} setBusy={setBusy} setError={setError} onProjectUpdate={setProject} />
      ) : null}

      {project ? (
        <>
          <UnassignedSection
            project={project}
            busy={busy}
            onAssign={(path, stage, role) => move(path, stage, role)}
            onRemove={(video, stage) => setRemoveTarget({ video, stage })}
          />
          <StagesSection
            project={project}
            busy={busy}
            setBusy={setBusy}
            setError={setError}
            onProjectUpdate={setProject}
            onMove={move}
            onRemove={(video, stage) => setRemoveTarget({ video, stage })}
          />
        </>
      ) : null}

      {removeTarget ? (
        <RemoveVideoDialog
          target={removeTarget}
          busy={busy}
          onCancel={() => setRemoveTarget(null)}
          onConfirm={(resetAudit) => handleRemove(removeTarget.video.path, resetAudit)}
        />
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
  const stages = project?.stages ?? [];
  const realCount = stages.filter((s) => !s.placeholder).length;
  const placeholderCount = stages.filter((s) => s.placeholder).length;
  const description = realCount
    ? `${realCount} stages loaded for ${project!.competitor_name ?? "the primary competitor"}.`
    : placeholderCount
      ? `${placeholderCount} placeholder stages -- upload a real scoreboard to fill in names, competitor metadata, and timestamps.`
      : "No stages yet. Drop in an SSI Scoreboard JSON to load them.";
  const dropLabel = realCount
    ? "Replace scoreboard JSON (warns first)"
    : placeholderCount
      ? "Upload scoreboard to overlay placeholders"
      : "Drop or pick an SSI Scoreboard JSON";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileJson className="size-5" />
          Scoreboard
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <FileDropZone
          accept=".json,application/json"
          label={dropLabel}
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

function StartFromVideosSection({
  project,
  busy,
  onSubmit,
}: {
  project: MatchProject;
  busy: boolean;
  onSubmit: (
    files: { path: string; mtime: number | null }[],
    stageCount: number,
    matchName: string | null,
    matchDate: string | null,
  ) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<
    { path: string; mtime: number | null }[] | null
  >(null);
  const [stageCount, setStageCount] = useState<number>(0);
  const [matchName, setMatchName] = useState<string>(project.name);
  const [matchDate, setMatchDate] = useState<string>("");

  const beginPick = () => {
    setPicking(true);
  };

  const onFilesChosen = (files: { path: string; mtime: number | null }[]) => {
    setPicking(false);
    setPicked(files);
    setStageCount(files.length);
    const earliest = files
      .map((f) => f.mtime)
      .filter((m): m is number => m !== null)
      .sort((a, b) => a - b)[0];
    if (earliest !== undefined) {
      // mtime is seconds since epoch; toISOString().slice(0, 10) gives YYYY-MM-DD.
      setMatchDate(new Date(earliest * 1000).toISOString().slice(0, 10));
    } else {
      setMatchDate(new Date().toISOString().slice(0, 10));
    }
  };

  const submit = () => {
    if (!picked || stageCount < 1) return;
    onSubmit(
      picked,
      stageCount,
      matchName.trim() || null,
      matchDate || null,
    );
    setPicked(null);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PlayCircle className="size-5" />
          Start from videos
        </CardTitle>
        <CardDescription>
          No scoreboard yet? Pick the videos you shot and tell splitsmith how
          many stages you ran. Stage count and date are detected from the
          files; you can adjust before continuing.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {picking ? (
          <FolderPicker
            initialPath={project.last_scanned_dir ?? null}
            onSelect={() => {
              /* not used: this card always wants files, not a folder. */
            }}
            onSelectFiles={onFilesChosen}
            onCancel={() => setPicking(false)}
          />
        ) : picked ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {picked.length} video{picked.length === 1 ? "" : "s"} selected.
            </p>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Number of stages</span>
              <span className="text-xs text-muted-foreground">
                Defaulted to the number of files. Adjust if your camera split a
                stage across multiple clips, or rolled across stages.
              </span>
              <input
                type="number"
                min={1}
                value={stageCount}
                onChange={(e) => setStageCount(Number(e.target.value))}
                disabled={busy}
                className="flex h-8 w-24 rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Match name</span>
              <input
                type="text"
                value={matchName}
                onChange={(e) => setMatchName(e.target.value)}
                disabled={busy}
                className="flex h-8 rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="flex items-center gap-1.5 font-medium">
                <CalendarDays className="size-3.5" />
                Match date
              </span>
              <input
                type="date"
                value={matchDate}
                onChange={(e) => setMatchDate(e.target.value)}
                disabled={busy}
                className="flex h-8 w-44 rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <div className="flex gap-2">
              <Button onClick={submit} disabled={busy || stageCount < 1}>
                Create {stageCount || "?"} placeholder stage
                {stageCount === 1 ? "" : "s"}
              </Button>
              <Button
                variant="outline"
                onClick={() => setPicked(null)}
                disabled={busy}
              >
                Back
              </Button>
            </div>
          </div>
        ) : (
          <Button onClick={beginPick} disabled={busy}>
            <FolderInput />
            Pick videos
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ScanSection({
  disabled,
  initialPath,
  onScan,
  onScanFiles,
}: {
  disabled: boolean;
  initialPath: string | null;
  onScan: (sourceDir: string) => void;
  onScanFiles: (sourcePaths: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderInput className="size-5" />
          Scan videos
        </CardTitle>
        <CardDescription>
          Pick a folder to scan in bulk, or check specific files (e.g. straight
          off your camera over USB). Source files are <em>referenced via
          symlink</em> — splitsmith never copies them. Confident timestamp
          matches are auto-assigned as primary.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!open ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={disabled} onClick={() => setOpen(true)}>
              <FolderInput />
              Browse / pick files
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
            onSelectFiles={(files) => {
              setOpen(false);
              onScanFiles(files.map((f) => f.path));
            }}
            onCancel={() => setOpen(false)}
          />
        )}
        {disabled ? (
          <p className="text-xs text-muted-foreground">
            Bootstrap the project first (upload a scoreboard or start from videos).
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SettingsSection({
  project,
  busy,
  setBusy,
  setError,
  onProjectUpdate,
}: {
  project: MatchProject;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (p: MatchProject) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [pickerFor, setPickerFor] = useState<
    null | "raw" | "audio" | "trimmed" | "exports" | "probes" | "thumbs"
  >(null);
  const fields: {
    key: "raw_dir" | "audio_dir" | "trimmed_dir" | "exports_dir" | "probes_dir" | "thumbs_dir";
    label: string;
    help: string;
  }[] = [
    { key: "raw_dir", label: "Source video links", help: "Symlinks to source files (default: <project>/raw)." },
    { key: "audio_dir", label: "Audio cache", help: "Extracted WAVs (default: <project>/audio). Heavy intermediate; SSD-friendly." },
    { key: "trimmed_dir", label: "Trimmed clips", help: "Short-GOP MP4s used by the audit screen (default: <project>/trimmed)." },
    { key: "exports_dir", label: "Outputs", help: "CSV / FCPXML / report (default: <project>/exports)." },
    { key: "probes_dir", label: "ffprobe cache", help: "Cached duration / codec metadata (default: <project>/probes)." },
    { key: "thumbs_dir", label: "Thumbnail cache", help: "Cached preview JPGs (default: <project>/thumbs)." },
  ];
  const labelFor = (field: string) =>
    fields.find((f) => f.key === field)?.label ?? field;

  const update = async (patch: Partial<Record<typeof fields[number]["key"], string | null>>) => {
    setBusy(true);
    try {
      const updated = await api.updateSettings(patch);
      onProjectUpdate(updated);
      setError(null);
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        e.body &&
        typeof e.body === "object" &&
        (e.body as { code?: string }).code === "non_empty_old_dirs"
      ) {
        const detail = e.body as NonEmptyOldDirsDetail;
        const lines = detail.dirs
          .map(
            (d) =>
              `  - ${labelFor(d.field)}: ${d.path} (${d.file_count} item${d.file_count === 1 ? "" : "s"})`,
          )
          .join("\n");
        const ok = window.confirm(
          `The following directories contain files that will be left behind ` +
            `(splitsmith does not migrate cache or exports between paths):\n\n${lines}\n\n` +
            `Proceed anyway?`,
        );
        if (ok) {
          try {
            const updated = await api.updateSettings({ ...patch, confirm: true });
            onProjectUpdate(updated);
            setError(null);
          } catch (e2) {
            setError(e2 instanceof Error ? e2.message : String(e2));
          }
        } else {
          setError(null);
        }
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={() => setExpanded((v) => !v)}>
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <FolderInput className="size-4" />
            Project storage
          </span>
          <span className="text-xs font-normal text-muted-foreground">
            {expanded ? "Hide" : "Configure paths"}
          </span>
        </CardTitle>
        <CardDescription>
          Source videos are <em>never copied</em>; the project just references
          them. Cache and outputs default to subfolders of the project root —
          override them per project if you want heavy intermediates on a scratch
          SSD or outputs next to your FCP library.
        </CardDescription>
      </CardHeader>
      {expanded ? (
        <CardContent className="space-y-3">
          {fields.map(({ key, label, help }) => {
            const current = project[key] ?? "";
            return (
              <div key={key} className="space-y-1">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">{label}</span>
                  <span className="text-xs text-muted-foreground">{help}</span>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      defaultValue={current}
                      placeholder={`<project>/${key.replace("_dir", "")}`}
                      className="flex h-8 flex-1 rounded-md border border-input bg-background px-2 py-1 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      disabled={busy}
                      onBlur={(e) => {
                        const value = e.target.value.trim();
                        if (value === (project[key] ?? "")) return;
                        void update({ [key]: value || "" } as never);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        setPickerFor(
                          key.replace("_dir", "") as
                            | "raw"
                            | "audio"
                            | "trimmed"
                            | "exports"
                            | "probes"
                            | "thumbs",
                        )
                      }
                    >
                      Browse
                    </Button>
                    {current ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => void update({ [key]: "" } as never)}
                        title="Reset to default (subfolder of project root)"
                      >
                        Reset
                      </Button>
                    ) : null}
                  </div>
                </label>
              </div>
            );
          })}
          {pickerFor ? (
            <FolderPicker
              initialPath={project[`${pickerFor}_dir` as keyof MatchProject] as string | null}
              onSelect={(p) => {
                void update({ [`${pickerFor}_dir`]: p } as never);
                setPickerFor(null);
              }}
              onCancel={() => setPickerFor(null)}
            />
          ) : null}
          <TrimBufferRow
            label="Pre-beep buffer (seconds)"
            help="Pad before the beep in the trimmed clip. Longer = more pre-roll for FCP fades."
            value={project.trim_pre_buffer_seconds}
            disabled={busy}
            onCommit={(seconds) => void update({ trim_pre_buffer_seconds: seconds } as never)}
          />
          <TrimBufferRow
            label="Post-stage buffer (seconds)"
            help="Pad after the stage end. Longer = more tail for FCP fades and transitions."
            value={project.trim_post_buffer_seconds}
            disabled={busy}
            onCommit={(seconds) => void update({ trim_post_buffer_seconds: seconds } as never)}
          />
        </CardContent>
      ) : null}
    </Card>
  );
}

function TrimBufferRow({
  label,
  help,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  help: string;
  value: number;
  disabled: boolean;
  onCommit: (seconds: number) => void;
}) {
  return (
    <div className="space-y-1">
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">{label}</span>
        <span className="text-xs text-muted-foreground">{help}</span>
        <input
          type="number"
          min={0}
          step={0.5}
          defaultValue={value}
          disabled={disabled}
          className="flex h-8 w-32 rounded-md border border-input bg-background px-2 py-1 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onBlur={(e) => {
            const next = Number.parseFloat(e.target.value);
            if (!Number.isFinite(next) || next < 0) return;
            if (Math.abs(next - value) < 1e-6) return;
            onCommit(next);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
        />
      </label>
    </div>
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
  onRemove,
}: {
  project: MatchProject;
  busy: boolean;
  onAssign: (videoPath: string, stage: number | null, role: VideoRole) => void;
  onRemove: (video: StageVideo, stage: StageEntry | null) => void;
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
          Drag onto a stage, or use the menu to pick a stage. Trash removes the
          video from the project (cache is cleared; the original source on USB
          / external storage is never touched).
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
                onClick={() => onRemove(v, null)}
                title="Remove from project"
                aria-label={`Remove ${v.path}`}
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
  onRemove,
}: {
  project: MatchProject;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
  onRemove: (video: StageVideo, stage: StageEntry) => void;
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
      {/* Cards carry a beep section + stage-move dropdown + multiple
          video previews; squeezing them into 3 columns at the xl
          breakpoint (1280px viewport, ~992px content width after the
          240px sidebar) clipped controls. Push 2-col to lg and 3-col
          to 2xl so each card has room to breathe. */}
      <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
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
            onRemove={onRemove}
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
  onRemove,
}: {
  stage: StageEntry;
  allStages: StageEntry[];
  busy: boolean;
  primaryCounts: Map<string, number>;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
  onRemove: (video: StageVideo, stage: StageEntry) => void;
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
                  onRemove={onRemove}
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
                onRemove={onRemove}
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
                onRemove={onRemove}
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
  onRemove,
}: {
  video: StageVideo;
  stage: StageEntry;
  allStages: StageEntry[];
  busy: boolean;
  currentRole: VideoRole;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
  onRemove: (video: StageVideo, stage: StageEntry) => void;
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
      <Button
        size="sm"
        variant="ghost"
        disabled={busy}
        onClick={() => onRemove(video, stage)}
        title="Remove from project (clears caches; source on disk is untouched)"
        aria-label={`Remove ${video.path}`}
      >
        <Trash2 />
      </Button>
    </>
  );
}

function RemoveVideoDialog({
  target,
  busy,
  onCancel,
  onConfirm,
}: {
  target: { video: StageVideo; stage: StageEntry | null };
  busy: boolean;
  onCancel: () => void;
  onConfirm: (resetAudit: boolean) => void;
}) {
  const { video, stage } = target;
  const filename = video.path.split("/").pop() ?? video.path;
  const isPrimary = video.role === "primary" && stage !== null;
  const hasAudit = video.processed.beep || video.processed.shot_detect || video.processed.trim;
  const offerAuditChoice = isPrimary && hasAudit;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="remove-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4"
      onClick={onCancel}
    >
      <Card
        className="w-full max-w-lg shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader>
          <CardTitle id="remove-dialog-title" className="flex items-center gap-2">
            <Trash2 className="size-5" />
            Remove video
          </CardTitle>
          <CardDescription>
            <span className="font-mono text-xs">{filename}</span>
            {stage ? (
              <>
                {" "}
                from Stage {stage.stage_number}: {stage.stage_name}
              </>
            ) : (
              " (unassigned)"
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>
            The symlink in the project's <code>raw/</code> folder is removed
            and any cached audio / trimmed clip for this video is cleared.{" "}
            <strong>The original source file is never touched.</strong>
          </p>
          {offerAuditChoice ? (
            <div className="rounded-md border border-status-warning/40 bg-status-warning/10 p-3 text-xs">
              <p className="mb-2 font-semibold">
                Stage {stage!.stage_number} has audit data.
              </p>
              <p>
                <em>Keep audit</em> preserves detected beep / shot times so you
                can re-ingest a different file for this stage and pick up
                where you left off.{" "}
                <em>Reset audit</em> wipes the stage audit JSON and clears the
                processed flags.
              </p>
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <Button variant="ghost" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            {offerAuditChoice ? (
              <>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => onConfirm(false)}
                >
                  Remove, keep audit
                </Button>
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => onConfirm(true)}
                >
                  Remove and reset audit
                </Button>
              </>
            ) : (
              <Button
                variant="destructive"
                disabled={busy}
                onClick={() => onConfirm(false)}
              >
                Remove
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
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
