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

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Crosshair,
  FileJson,
  FolderInput,
  HardDrive,
  PlayCircle,
  Search,
  Trash2,
  Video as VideoIcon,
  WifiOff,
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
  asScoreboardError,
  type MatchAnalysis,
  type MatchProject,
  type NonEmptyOldDirsDetail,
  type ScoreboardErrorDetail,
  type ScoreboardMatchRef,
  type ScoreboardSource,
  type StageEntry,
  type StageMatchWindow,
  type StageVideo,
  type VideoMatchAnalysisEntry,
  type VideoRole,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Drag payload mime type: enough to namespace from random file drops, and
 *  carries the source video path so the drop target can move/remove it. */
const DRAG_MIME = "application/x-splitsmith-video";

export function Ingest() {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [analysis, setAnalysis] = useState<MatchAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scoreboardError, setScoreboardError] = useState<ScoreboardErrorDetail | null>(null);
  const [scoreboardSource, setScoreboardSource] = useState<ScoreboardSource | null>(null);
  const [busy, setBusy] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<{
    video: StageVideo;
    stage: StageEntry | null;
  } | null>(null);
  // Tracks which video is currently being dragged so we can surface a
  // floating "remove from project" zone only while a drag is active.
  const [dragging, setDragging] = useState<{
    video: StageVideo;
    stage: StageEntry | null;
  } | null>(null);

  const refreshAnalysis = useCallback(async () => {
    // Cheap GET that re-runs the heuristic over current project state. Fire
    // after any mutation that could change classifications (scan, move,
    // remove, swap) so the timeline never lags behind reality.
    try {
      setAnalysis(await api.getMatchAnalysis());
    } catch {
      // Best effort: if the analysis endpoint is unavailable, the SPA
      // just renders a project without timeline -- the rest of ingest
      // keeps working.
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      // Fetch project + match analysis in parallel: the SPA never renders
      // the timeline from its own state, only from the backend's canonical
      // heuristic output. Analysis lags slightly when the project is a
      // bare placeholder (no scoreboard yet); that's fine, the timeline
      // just doesn't show until stages have scorecard times.
      const [proj, ana, src] = await Promise.all([
        api.getProject(),
        api.getMatchAnalysis().catch(() => null),
        api.getScoreboardSource().catch(() => null),
      ]);
      setProject(proj);
      setAnalysis(ana);
      setScoreboardSource(src);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refreshScoreboardSource = useCallback(async () => {
    try {
      setScoreboardSource(await api.getScoreboardSource());
    } catch {
      // best effort
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
      // Auto-detect: SSI v1 ``MatchData`` has a top-level ``stages`` array;
      // the legacy ``examples/`` shape is wrapped in ``{match, competitors}``
      // with per-competitor stage scores. Dispatch to the matching endpoint
      // so both formats keep working without forcing the user to choose.
      const updated = isSsiV1MatchData(data)
        ? await api.uploadScoreboard(data, overwrite)
        : await api.importScoreboard(data, overwrite);
      setProject(updated);
      await refreshScoreboardSource();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleFetchOnline = async (
    contentType: number,
    matchId: number,
  ) => {
    setBusy(true);
    setScoreboardError(null);
    try {
      const realStages = (project?.stages ?? []).filter((s) => !s.placeholder);
      const overwrite = realStages.length > 0;
      if (overwrite) {
        const ok = window.confirm(
          "This project already has scoreboard-backed stages. Fetching will replace them and orphan any current video assignments. Continue?",
        );
        if (!ok) {
          setBusy(false);
          return;
        }
      }
      const updated = await api.fetchScoreboardMatch(contentType, matchId, overwrite);
      setProject(updated);
      await refreshScoreboardSource();
      setError(null);
    } catch (e) {
      const detail = asScoreboardError(e);
      if (detail) setScoreboardError(detail);
      else setError(e instanceof Error ? e.message : String(e));
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
      // Promotion to primary uses the audit-safe swap endpoint when a stage
      // already has a primary. The endpoint returns 409 with {code:
      // "audit_exists"} when the current primary has shots in audit; we then
      // ask the user and retry with confirm=true. For the no-existing-audit
      // case, the swap is silent.
      if (toStage !== null && role === "primary") {
        const targetStage = project?.stages.find((s) => s.stage_number === toStage);
        const existingPrimary = targetStage?.videos.find((v) => v.role === "primary");
        if (existingPrimary && existingPrimary.path !== videoPath) {
          try {
            const updated = await api.swapPrimary(videoPath, toStage, false);
            setProject(updated);
            void refreshAnalysis();
            return;
          } catch (e) {
            if (
              e instanceof ApiError &&
              e.status === 409 &&
              e.body &&
              typeof e.body === "object" &&
              (e.body as { code?: string }).code === "audit_exists"
            ) {
              const ok = window.confirm(
                `Stage ${toStage} has audited shots on the current primary.\n\n` +
                  "Confirm the swap to back the audit JSON up to .bak and " +
                  "re-run detection on the new primary's audio. Existing " +
                  "shot decisions on the old primary will be preserved in " +
                  "the .bak file but no longer drive the audit screen.",
              );
              if (!ok) return;
              const updated = await api.swapPrimary(videoPath, toStage, true);
              setProject(updated);
              void refreshAnalysis();
              return;
            }
            throw e;
          }
        }
      }
      const updated = await api.moveAssignment(videoPath, toStage, role);
      setProject(updated);
      void refreshAnalysis();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const setStageSkipped = async (stageNumber: number, skipped: boolean) => {
    setBusy(true);
    try {
      const updated = await api.setStageSkipped(stageNumber, skipped);
      setProject(updated);
      void refreshAnalysis();
      setError(null);
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

      {scoreboardError ? (
        <ScoreboardErrorBanner
          detail={scoreboardError}
          onDismiss={() => setScoreboardError(null)}
        />
      ) : null}

      {project && project.stages.length === 0 ? (
        // Stack on a fresh project: the FolderPicker inside Start-from-videos
        // (and the search results inside Scoreboard) need full card width to
        // render filenames + dates without clipping. Squeezing two of these
        // into a 50/50 grid produced the clipped picker rows we saw on first
        // launch -- give each card the row.
        <div className="space-y-4">
          <ScoreboardSection
            project={project}
            source={scoreboardSource}
            busy={busy}
            onScoreboard={handleScoreboard}
            onFetchOnline={handleFetchOnline}
            setScoreboardError={setScoreboardError}
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
          source={scoreboardSource}
          busy={busy}
          onScoreboard={handleScoreboard}
          onFetchOnline={handleFetchOnline}
          setScoreboardError={setScoreboardError}
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
          <ReadyBanner project={project} analysis={analysis} />
          <UnassignedSection
            project={project}
            busy={busy}
            dragging={dragging}
            setDragging={setDragging}
            onAssign={(path, stage, role) => move(path, stage, role)}
            onRemove={(video, stage) => setRemoveTarget({ video, stage })}
          />
          <StagesSection
            project={project}
            analysis={analysis}
            busy={busy}
            dragging={dragging}
            setDragging={setDragging}
            setBusy={setBusy}
            setError={setError}
            onProjectUpdate={setProject}
            onMove={move}
            onSetSkipped={setStageSkipped}
            onRemove={(video, stage) => setRemoveTarget({ video, stage })}
          />
          {dragging ? (
            <RemoveDropZone
              dragging={dragging}
              busy={busy}
              onDrop={() => setRemoveTarget({ video: dragging.video, stage: dragging.stage })}
            />
          ) : null}
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
  source,
  busy,
  onScoreboard,
  onFetchOnline,
  setScoreboardError,
}: {
  project: MatchProject | null;
  source: ScoreboardSource | null;
  busy: boolean;
  onScoreboard: (f: File) => void;
  onFetchOnline: (contentType: number, matchId: number) => void;
  setScoreboardError: (e: ScoreboardErrorDetail | null) => void;
}) {
  const isLocal = source?.mode === "local";
  const tokenReady = source?.http_token_set ?? false;
  // Online search goes through SsiHttpClient, so it's only useful when the
  // token is set. When it isn't, gate it behind a single inline hint
  // instead of letting the request fire and surface a duplicate top-level
  // banner.
  const onlineReady = isLocal || tokenReady;
  const stages = project?.stages ?? [];
  const realCount = stages.filter((s) => !s.placeholder).length;
  const placeholderCount = stages.filter((s) => s.placeholder).length;
  // A match has been selected once the project carries a real
  // scoreboard_match_id (drop-JSON path or online fetch). Once that's true
  // the user is unlikely to revisit this card -- collapse it by default
  // and let them re-open if they need to swap matches.
  const matchLoaded = !!project?.scoreboard_match_id;
  const [expanded, setExpanded] = useState(!matchLoaded);
  // Sync the local default if the project picks up a match in another tab
  // (or after a re-fetch). User clicks override this until the match id
  // changes again.
  const [lastMatchId, setLastMatchId] = useState<string | null>(
    project?.scoreboard_match_id ?? null,
  );
  useEffect(() => {
    const current = project?.scoreboard_match_id ?? null;
    if (current !== lastMatchId) {
      setLastMatchId(current);
      setExpanded(!current);
    }
  }, [project?.scoreboard_match_id, lastMatchId]);

  const description = realCount
    ? `${realCount} stages loaded for ${project!.competitor_name ?? "the primary competitor"}.`
    : placeholderCount
      ? `${placeholderCount} placeholder stages -- upload a real scoreboard to fill in names, competitor metadata, and timestamps.`
      : "No stages yet. Drop in an SSI Scoreboard JSON, or search the live scoreboard.";
  const dropLabel = realCount
    ? "Replace scoreboard JSON (warns first)"
    : placeholderCount
      ? "Upload scoreboard to overlay placeholders"
      : "Drop or pick an SSI Scoreboard JSON";

  return (
    <Card>
      <CardHeader
        className="cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <FileJson className="size-5" />
            Scoreboard
          </span>
          <span className="text-xs font-normal text-muted-foreground">
            {expanded ? "Hide" : "Change"}
          </span>
        </CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-x-2">
          <span>{description}</span>
          {project?.scoreboard_match_id ? (
            <span className="text-xs text-muted-foreground">
              · match <code>{project.scoreboard_match_id}</code>
              {project.scoreboard_content_type !== null &&
              project.scoreboard_content_type !== undefined
                ? ` (ct ${project.scoreboard_content_type})`
                : null}
            </span>
          ) : null}
        </CardDescription>
      </CardHeader>
      {expanded ? (
        <CardContent className="space-y-4">
          {source ? <ScoreboardSourceBadge source={source} /> : null}

          <FileDropZone
            accept=".json,application/json"
            label={dropLabel}
            icon={<FileJson className="size-5" />}
            disabled={busy}
            onFile={onScoreboard}
          />

          {!isLocal && onlineReady ? (
            <OnlineMatchSearch
              busy={busy}
              onFetch={onFetchOnline}
              onError={setScoreboardError}
            />
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}

function ScoreboardSourceBadge({ source }: { source: ScoreboardSource }) {
  if (source.mode === "local") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-status-success/40 bg-status-success/5 px-3 py-2 text-xs">
        <HardDrive className="size-4 text-status-success" />
        <span>
          Loaded from local JSON, no network used.
          {source.local_match_json_path ? (
            <>
              {" "}
              <span className="font-mono text-muted-foreground" title={source.local_match_json_path}>
                {source.local_match_json_path.split("/").slice(-3).join("/")}
              </span>
            </>
          ) : null}
        </span>
      </div>
    );
  }
  if (!source.http_token_set) {
    // The auth-needed state is *not* a runtime error -- the user simply
    // hasn't configured a token yet. Render this as guidance and gate
    // search/fetch behind it so we don't fire requests we know will fail
    // (and surface a duplicate banner). The setup hint lives here only.
    return (
      <div className="space-y-1 rounded-md border border-status-warning/40 bg-status-warning/5 px-3 py-2 text-xs">
        <div className="flex items-center gap-2 font-medium">
          <AlertCircle className="size-4 text-status-warning" />
          <span>
            Set <code>SPLITSMITH_SSI_TOKEN</code> to enable live search.
          </span>
        </div>
        <p className="text-muted-foreground">
          Drop the line into <code>&lt;project&gt;/.env.local</code> (preferred) or
          <code> .env</code>, then restart the server. The drop-JSON path above
          works without any token.
        </p>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
      <Search className="size-4 text-muted-foreground" />
      <span>Online mode: queries hit the live SSI scoreboard.</span>
    </div>
  );
}

function OnlineMatchSearch({
  busy,
  onFetch,
  onError,
}: {
  busy: boolean;
  onFetch: (contentType: number, matchId: number) => void;
  onError: (e: ScoreboardErrorDetail | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ScoreboardMatchRef[] | null>(null);
  const [searching, setSearching] = useState(false);
  // Per #50: debounce typing at ~250ms so each keystroke doesn't fire a
  // request. Cleared on every keystroke so the trailing edge wins.
  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults(null);
      return;
    }
    const handle = window.setTimeout(async () => {
      setSearching(true);
      try {
        const refs = await api.searchScoreboardMatches(trimmed);
        setResults(refs);
        onError(null);
      } catch (e) {
        const detail = asScoreboardError(e);
        if (detail) onError(detail);
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => window.clearTimeout(handle);
  }, [query, onError]);

  return (
    <div className="space-y-2">
      <label className="flex flex-col gap-1 text-sm">
        <span className="flex items-center gap-1.5 font-medium">
          <Search className="size-3.5" />
          Search the live scoreboard
        </span>
        <span className="text-xs text-muted-foreground">
          Find a match by name, then "Fetch full match" to populate the stage list.
        </span>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={busy}
          placeholder="e.g. SPSK Open"
          className="flex h-8 rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </label>
      {searching ? (
        <p className="text-xs text-muted-foreground">Searching...</p>
      ) : null}
      {results && results.length > 0 ? (
        <ul className="max-h-72 space-y-1 overflow-y-auto rounded-md border border-border bg-background p-1">
          {results.map((m) => (
            <li
              key={`${m.content_type}-${m.id}`}
              className="flex items-start justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-accent/40"
            >
              <div className="min-w-0 text-xs">
                <div className="truncate font-medium">{m.name}</div>
                <div className="truncate text-muted-foreground">
                  {m.date.slice(0, 10)} &middot; {m.level} &middot;{" "}
                  {m.venue ?? "venue tba"}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  // Collapse the dropdown immediately so the user isn't
                  // staring at stale results while the populate request
                  // is in flight. The whole scoreboard card will collapse
                  // once project.scoreboard_match_id changes.
                  onFetch(m.content_type, m.id);
                  setQuery("");
                  setResults(null);
                }}
              >
                Fetch full match
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {results && results.length === 0 && query.trim().length >= 2 && !searching ? (
        <p className="text-xs text-muted-foreground">No matches found.</p>
      ) : null}
    </div>
  );
}

function ScoreboardErrorBanner({
  detail,
  onDismiss,
}: {
  detail: ScoreboardErrorDetail;
  onDismiss: () => void;
}) {
  let icon: React.ReactNode;
  let title: string;
  let body: React.ReactNode;
  if (detail.code === "scoreboard_auth") {
    icon = <AlertCircle className="size-4 text-status-warning" />;
    title = "Scoreboard rejected the bearer token.";
    body = (
      <>
        Check that <code>{detail.env_var}</code> is current (tokens can rotate
        or expire) and restart the server.{" "}
        <a
          href={detail.docs_url}
          target="_blank"
          rel="noreferrer"
          className="underline"
        >
          Docs
        </a>
        .
      </>
    );
  } else if (detail.code === "scoreboard_rate_limited") {
    icon = <AlertCircle className="size-4 text-status-warning" />;
    title = "Rate-limited by the scoreboard.";
    body = detail.retry_after !== null
      ? `Retry in ${Math.ceil(detail.retry_after)} seconds.`
      : "Wait a moment and retry.";
  } else {
    icon = <WifiOff className="size-4 text-status-warning" />;
    title = "Couldn't reach the scoreboard.";
    body = "Try the offline JSON path -- drop a file into the scoreboard zone above.";
  }
  return (
    <Card className="border-status-warning/50 bg-status-warning/5">
      <CardContent className="flex items-start gap-2 py-3 text-sm">
        {icon}
        <div className="flex-1">
          <div className="font-medium">{title}</div>
          <div className="text-xs text-muted-foreground">{body}</div>
        </div>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </CardContent>
    </Card>
  );
}

/** Heuristic: SSI v1 ``MatchData`` carries a top-level ``stages: []`` array
 *  alongside ``competitors: []``; the legacy ``examples/`` shape wraps both
 *  in ``{match: {...}, competitors: [...]}`` with per-competitor stages. The
 *  drop handler dispatches to the matching backend endpoint so users don't
 *  have to know which they have. */
function isSsiV1MatchData(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const obj = data as Record<string, unknown>;
  return (
    Array.isArray(obj.stages) &&
    Array.isArray(obj.competitors) &&
    typeof obj.stages_count !== "undefined"
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
  dragging,
  setDragging,
  onAssign,
  onRemove,
}: {
  project: MatchProject;
  busy: boolean;
  dragging: { video: StageVideo; stage: StageEntry | null } | null;
  setDragging: (d: { video: StageVideo; stage: StageEntry | null } | null) => void;
  onAssign: (videoPath: string, stage: number | null, role: VideoRole) => void;
  onRemove: (video: StageVideo, stage: StageEntry | null) => void;
}) {
  const [over, setOver] = useState(false);
  // The unassigned tray accepts drops only when the dragged video is currently
  // assigned to a stage; dragging within the tray is a no-op.
  const canAccept = dragging !== null && dragging.stage !== null;

  if (project.unassigned_videos.length === 0 && !canAccept) return null;

  return (
    <Card
      className={cn(
        canAccept && "border-dashed border-ring/60",
        canAccept && over && "border-ring bg-accent/30",
      )}
      onDragOver={(e) => {
        if (!canAccept) return;
        if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!canAccept) return;
        const path = e.dataTransfer.getData(DRAG_MIME);
        if (!path) return;
        e.preventDefault();
        setOver(false);
        onAssign(path, null, "secondary");
      }}
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <VideoIcon className="size-5" />
          Unassigned videos · {project.unassigned_videos.length}
        </CardTitle>
        <CardDescription>
          Drag onto a stage card to assign, or back here to unassign. Trash
          removes the video from the project (cache is cleared; the original
          source on USB / external storage is never touched).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {project.unassigned_videos.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Drop here to unassign the video you're dragging.
          </p>
        ) : null}
        {project.unassigned_videos.map((v) => (
          <DraggableVideoRow
            key={v.path}
            video={v}
            stage={null}
            setDragging={setDragging}
            className="rounded-md border border-border bg-muted/40 px-3 py-2"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <div className="flex min-w-0 items-center gap-2 font-mono text-xs">
                <VideoIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate" title={v.path}>{v.path}</span>
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
          </DraggableVideoRow>
        ))}
      </CardContent>
    </Card>
  );
}

function StagesSection({
  project,
  analysis,
  busy,
  dragging,
  setDragging,
  setBusy,
  setError,
  onProjectUpdate,
  onMove,
  onSetSkipped,
  onRemove,
}: {
  project: MatchProject;
  analysis: MatchAnalysis | null;
  busy: boolean;
  dragging: { video: StageVideo; stage: StageEntry | null } | null;
  setDragging: (d: { video: StageVideo; stage: StageEntry | null } | null) => void;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
  onSetSkipped: (stageNumber: number, skipped: boolean) => void;
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
            window={analysis?.stages.find((w) => w.stage_number === s.stage_number) ?? null}
            videoEntries={analysis?.videos ?? []}
            busy={busy}
            dragging={dragging}
            setDragging={setDragging}
            primaryCounts={primaryCounts}
            setBusy={setBusy}
            setError={setError}
            onProjectUpdate={onProjectUpdate}
            onMove={onMove}
            onSetSkipped={onSetSkipped}
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
  window: matchWindow,
  videoEntries,
  busy,
  dragging,
  setDragging,
  primaryCounts,
  setBusy,
  setError,
  onProjectUpdate,
  onMove,
  onSetSkipped,
  onRemove,
}: {
  stage: StageEntry;
  allStages: StageEntry[];
  window: StageMatchWindow | null;
  videoEntries: VideoMatchAnalysisEntry[];
  busy: boolean;
  dragging: { video: StageVideo; stage: StageEntry | null } | null;
  setDragging: (d: { video: StageVideo; stage: StageEntry | null } | null) => void;
  primaryCounts: Map<string, number>;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
  onProjectUpdate: (next: MatchProject) => void;
  onMove: (path: string, stage: number | null, role: VideoRole) => void;
  onSetSkipped: (stageNumber: number, skipped: boolean) => void;
  onRemove: (video: StageVideo, stage: StageEntry) => void;
}) {
  const [over, setOver] = useState(false);
  const primary = stage.videos.find((v) => v.role === "primary");
  const secondaries = stage.videos.filter((v) => v.role === "secondary");
  const ignored = stage.videos.filter((v) => v.role === "ignored");
  const hasConflict = primary && (primaryCounts.get(primary.path) ?? 0) > 1;
  // Accept drops only when the dragged video is from a different stage (or
  // from the unassigned tray). Dropping onto the source stage is a no-op so
  // we don't visually invite it.
  const canAccept =
    dragging !== null && dragging.stage?.stage_number !== stage.stage_number;

  return (
    <Card
      className={cn(
        hasConflict && "border-status-warning",
        canAccept && "border-dashed border-ring/50",
        canAccept && over && "border-ring bg-accent/30",
      )}
      onDragOver={(e) => {
        if (!canAccept) return;
        if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!canAccept) return;
        const path = e.dataTransfer.getData(DRAG_MIME);
        if (!path) return;
        e.preventDefault();
        setOver(false);
        // First video on a stage becomes primary (per #13 spec); otherwise
        // it lands as secondary so the user has to opt in to swapping primary.
        const role: VideoRole = primary ? "secondary" : "primary";
        onMove(path, stage.stage_number, role);
      }}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">
              Stage {stage.stage_number}: {stage.stage_name}
            </CardTitle>
            <CardDescription className="font-mono tabular-nums">
              {stage.time_seconds.toFixed(2)}s &middot;{" "}
              {stage.scorecard_updated_at
                ? new Date(stage.scorecard_updated_at).toLocaleString()
                : "no scorecard time"}
            </CardDescription>
          </div>
          <div className="flex flex-col items-end gap-1">
            <StatusGlyph stage={stage} />
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              disabled={busy}
              onClick={() => onSetSkipped(stage.stage_number, !stage.skipped)}
              title={
                stage.skipped
                  ? "Un-skip this stage so it counts toward ingest gating again"
                  : "Skip this stage; it won't block the next-step gate even without a primary"
              }
            >
              {stage.skipped ? "Un-skip" : "Skip stage"}
            </Button>
          </div>
        </div>
        <MatchWindowTimeline
          stage={stage}
          window={matchWindow}
          videoEntries={videoEntries}
        />
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {stage.videos.length === 0 ? (
          <p className="text-sm text-muted-foreground">No videos assigned.</p>
        ) : null}
        {primary ? (
          <>
            <VideoRow
              video={primary}
              stage={stage}
              setDragging={setDragging}
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
              video={primary}
              busy={busy}
              setBusy={setBusy}
              setError={setError}
              onProjectUpdate={onProjectUpdate}
            />
          </>
        ) : null}
        {secondaries.map((v) => (
          <div key={v.path} className="space-y-2">
            <VideoRow
              video={v}
              stage={stage}
              setDragging={setDragging}
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
            <BeepSection
              stageNumber={stage.stage_number}
              video={v}
              busy={busy}
              setBusy={setBusy}
              setError={setError}
              onProjectUpdate={onProjectUpdate}
            />
          </div>
        ))}
        {ignored.map((v) => (
          <VideoRow
            key={v.path}
            video={v}
            stage={stage}
            setDragging={setDragging}
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
  stage,
  setDragging,
  badge,
  actions,
}: {
  video: StageVideo;
  stage: StageEntry | null;
  setDragging: (d: { video: StageVideo; stage: StageEntry | null } | null) => void;
  badge: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <DraggableVideoRow
      video={video}
      stage={stage}
      setDragging={setDragging}
      className="rounded-md border border-border/60 bg-muted/20 px-2 py-1.5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
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
          ) : null}
        </div>
        <div className="flex gap-1">{actions}</div>
      </div>
    </DraggableVideoRow>
  );
}

/** Draggable wrapper with hover-thumbnail. Used for both stage video rows
 *  (the badge + filename + actions row inside StageCard) and unassigned-tray
 *  rows (the path + per-stage assign buttons). The `stage` is `null` when the
 *  source is the unassigned tray; `setDragging` lets the page surface a
 *  floating "remove from project" zone while the drag is active. */
function DraggableVideoRow({
  video,
  stage,
  setDragging,
  className,
  children,
}: {
  video: StageVideo;
  stage: StageEntry | null;
  setDragging: (d: { video: StageVideo; stage: StageEntry | null } | null) => void;
  className?: string;
  children: React.ReactNode;
}) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [thumb, setThumb] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  // Suppress the hover preview while dragging anything; the floating image
  // would otherwise track the cursor and obscure the drop target.
  const [dragInFlight, setDragInFlight] = useState(false);

  const ensureProbe = useCallback(async () => {
    if (thumb !== null || probing) return;
    setProbing(true);
    try {
      const r = await api.probeFile(video.path);
      setThumb(r.thumbnail_url);
    } catch {
      // Best effort; row still renders without preview.
    } finally {
      setProbing(false);
    }
  }, [thumb, probing, video.path]);

  return (
    <div
      ref={ref}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_MIME, video.path);
        e.dataTransfer.effectAllowed = "move";
        setDragInFlight(true);
        setRect(null);
        setDragging({ video, stage });
      }}
      onDragEnd={() => {
        setDragInFlight(false);
        setDragging(null);
      }}
      onMouseEnter={() => {
        if (dragInFlight) return;
        setRect(ref.current?.getBoundingClientRect() ?? null);
        void ensureProbe();
      }}
      onMouseLeave={() => setRect(null)}
      className={cn("cursor-grab active:cursor-grabbing", className)}
    >
      {children}
      {rect && thumb && !dragInFlight ? (
        <ThumbnailFloat anchor={rect} src={thumb} alt={video.path} />
      ) : null}
    </div>
  );
}

/** Fixed-position hover preview, mirrors the FolderPicker pattern: anchor to
 *  the row's right edge, flip left if it would overflow the viewport, clamp
 *  the vertical position so it never paints off-screen. */
function ThumbnailFloat({
  anchor,
  src,
  alt,
}: {
  anchor: DOMRect;
  src: string;
  alt: string;
}) {
  const W = 320;
  const H = 192;
  const margin = 8;
  const flipLeft = anchor.right + W + margin > window.innerWidth;
  const left = flipLeft
    ? Math.max(margin, anchor.left - W - margin)
    : anchor.right + margin;
  const desiredTop = anchor.top + anchor.height / 2 - H / 2;
  const top = Math.max(
    margin,
    Math.min(window.innerHeight - H - margin, desiredTop),
  );
  return (
    <div
      role="presentation"
      style={{ position: "fixed", top, left, width: W, zIndex: 50 }}
      className="pointer-events-none rounded-md border border-border bg-popover p-1 shadow-xl"
    >
      <img src={src} alt={`${alt} thumbnail`} className="w-full rounded" />
    </div>
  );
}

/** Match-window timeline for one stage. Pure renderer: every value comes
 *  from the backend's :func:`MatchProject.match_analysis` so the SPA never
 *  duplicates the heuristic.
 *
 *  Window is asymmetric -- the band ends at ``scorecard_updated_at`` (the
 *  upper bound) and extends ``tolerance_minutes`` to the left. Videos
 *  classified as ``contested`` or ``in_window`` for *this* stage get a tick
 *  inside the band; videos in another stage's window or orphaned still get
 *  a faint tick so the user can see neighbours visually. Videos without a
 *  timestamp are skipped entirely. */
function MatchWindowTimeline({
  stage,
  window: matchWindow,
  videoEntries,
}: {
  stage: StageEntry;
  window: StageMatchWindow | null;
  videoEntries: VideoMatchAnalysisEntry[];
}) {
  if (!matchWindow || !matchWindow.lower || !matchWindow.upper) return null;

  const lowerSec = new Date(matchWindow.lower).getTime() / 1000;
  const upperSec = new Date(matchWindow.upper).getTime() / 1000;
  if (!Number.isFinite(lowerSec) || !Number.isFinite(upperSec)) return null;
  const tolSec = matchWindow.tolerance_minutes * 60;

  const ticks = videoEntries
    .filter((e) => e.timestamp !== null)
    .map((e) => {
      const ts = new Date(e.timestamp as string).getTime() / 1000;
      const role = stage.videos.find((x) => x.path === e.path)?.role ?? null;
      const inThisStage = e.stage_numbers.includes(stage.stage_number);
      return {
        path: e.path,
        ts,
        role,
        inThisStage,
        contested: e.classification === "contested",
        otherStages: e.stage_numbers.filter((n) => n !== stage.stage_number),
      };
    });

  if (ticks.length === 0) return null;

  // Visible range: at least the window plus a half-tolerance on each side,
  // expanded if any tick falls outside.
  const tsValues = ticks.map((t) => t.ts);
  const lo = Math.min(lowerSec - 0.5 * tolSec, ...tsValues);
  const hi = Math.max(upperSec + 0.5 * tolSec, ...tsValues);
  const span = Math.max(hi - lo, 1);

  const pct = (t: number) => `${Math.max(0, Math.min(100, ((t - lo) / span) * 100))}%`;
  const winLowPct = ((lowerSec - lo) / span) * 100;
  const winHighPct = ((upperSec - lo) / span) * 100;
  const tickLabel = (t: (typeof ticks)[number]) => {
    const fname = t.path.split("/").pop();
    const time = new Date(t.ts * 1000).toLocaleTimeString();
    if (t.inThisStage) return `${fname} @ ${time} (${t.role ?? "unassigned"})`;
    if (t.contested) return `${fname} @ ${time} (contested: stages ${t.otherStages.join(", ")})`;
    if (t.otherStages.length) return `${fname} @ ${time} (in stage ${t.otherStages[0]})`;
    return `${fname} @ ${time} (orphan)`;
  };

  return (
    <div className="mt-2 select-none" aria-hidden>
      <div className="relative h-3 rounded-full border border-border bg-muted/30">
        <div
          className="absolute top-0 h-full bg-status-info/20"
          style={{
            left: `${Math.max(0, winLowPct)}%`,
            width: `${Math.min(100, winHighPct) - Math.max(0, winLowPct)}%`,
          }}
          title={`match window: ${matchWindow.tolerance_minutes} min before scorecard`}
        />
        <div
          className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-status-info"
          style={{ left: pct(upperSec) }}
          title={`scorecard: ${new Date(upperSec * 1000).toLocaleTimeString()}`}
        />
        {ticks.map((t) => (
          <div
            key={t.path}
            className={cn(
              "absolute top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-sm",
              t.inThisStage
                ? t.role === "primary"
                  ? "h-4 w-1 bg-status-info"
                  : "bg-foreground"
                : "bg-muted-foreground/40",
              t.contested && "outline outline-1 outline-status-warning",
            )}
            style={{ left: pct(t.ts) }}
            title={tickLabel(t)}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{new Date(lo * 1000).toLocaleTimeString()}</span>
        <span>scorecard - {matchWindow.tolerance_minutes} min ... scorecard</span>
        <span>{new Date(hi * 1000).toLocaleTimeString()}</span>
      </div>
    </div>
  );
}

/** Top-of-page status banner: lists everything that blocks the user from
 *  advancing to the audit screen. Hard blockers: video claimed as primary
 *  by two stages; stage with no primary that isn't explicitly skipped.
 *  Soft warnings (advisory, do not block): contested unassigned videos
 *  (the heuristic says they fit two stages' windows). Renders nothing
 *  blocking when ingest is ready. */
function ReadyBanner({
  project,
  analysis,
}: {
  project: MatchProject;
  analysis: MatchAnalysis | null;
}) {
  const primaryCounts = new Map<string, number>();
  for (const s of project.stages) {
    for (const v of s.videos) {
      if (v.role === "primary") {
        primaryCounts.set(v.path, (primaryCounts.get(v.path) ?? 0) + 1);
      }
    }
  }
  const conflictPaths = Array.from(primaryCounts.entries())
    .filter(([, n]) => n > 1)
    .map(([p]) => p);
  const stagesWithoutPrimary = project.stages.filter(
    (s) => !s.skipped && s.videos.find((v) => v.role === "primary") === undefined,
  );

  // Surface heuristic-detected ambiguity for unassigned videos: the
  // classifier says they fall in 2+ stages' windows, so the auto-assignment
  // would have to pick one. Advisory only -- the user can resolve by
  // dragging onto the right stage.
  const contestedUnassigned = (analysis?.videos ?? []).filter(
    (v) =>
      v.classification === "contested" &&
      project.unassigned_videos.some((u) => u.path === v.path),
  );

  const blocking = conflictPaths.length > 0 || stagesWithoutPrimary.length > 0;
  if (!blocking && contestedUnassigned.length === 0) {
    return (
      <Card className="border-status-success/40 bg-status-success/5">
        <CardContent className="flex items-center gap-2 py-3 text-sm">
          <CheckCircle2 className="size-4 text-status-success" />
          <span>
            Ingest looks good. Every stage has a primary (or is skipped); no
            video is claimed as primary by two stages.
          </span>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card
      className={cn(
        blocking
          ? "border-status-warning/50 bg-status-warning/5"
          : "border-status-info/40 bg-status-info/5",
      )}
    >
      <CardContent className="space-y-2 py-3 text-sm">
        <div className="flex items-center gap-2 font-medium">
          <AlertCircle
            className={cn(
              "size-4",
              blocking ? "text-status-warning" : "text-status-info",
            )}
          />
          <span>
            {blocking
              ? "Ingest is not ready to advance."
              : "Ingest is ready, with advisories from the match heuristic."}
          </span>
        </div>
        {conflictPaths.length > 0 ? (
          <div>
            <div className="text-xs font-semibold">
              Video claimed as primary by more than one stage:
            </div>
            <ul className="ml-5 list-disc text-xs text-muted-foreground">
              {conflictPaths.map((p) => (
                <li key={p} className="font-mono">{p}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {stagesWithoutPrimary.length > 0 ? (
          <div>
            <div className="text-xs font-semibold">
              Stages without a primary (skip or assign one):
            </div>
            <ul className="ml-5 list-disc text-xs text-muted-foreground">
              {stagesWithoutPrimary.map((s) => (
                <li key={s.stage_number}>
                  Stage {s.stage_number}: {s.stage_name}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {contestedUnassigned.length > 0 ? (
          <div>
            <div className="text-xs font-semibold">
              Unassigned videos that fit multiple stage windows
              (heuristic, advisory):
            </div>
            <ul className="ml-5 list-disc text-xs text-muted-foreground">
              {contestedUnassigned.map((v) => (
                <li key={v.path}>
                  <span className="font-mono">{v.path.split("/").pop()}</span>
                  {" -- candidates: "}
                  {v.stage_numbers.map((n) => `S${n}`).join(", ")}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Floating bottom-right drop zone visible only while a drag is in flight.
 *  Dropping here triggers the same removal dialog the trash button uses, so
 *  audited primaries still get the keep-audit-or-reset choice. */
function RemoveDropZone({
  dragging,
  busy,
  onDrop,
}: {
  dragging: { video: StageVideo; stage: StageEntry | null };
  busy: boolean;
  onDrop: () => void;
}) {
  const [over, setOver] = useState(false);
  const filename = dragging.video.path.split("/").pop() ?? dragging.video.path;
  return (
    <div
      role="region"
      aria-label="Remove from project"
      className={cn(
        "fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-lg border-2 border-dashed border-destructive/60 bg-destructive/10 px-4 py-3 shadow-lg",
        over && "border-destructive bg-destructive/25",
        busy && "opacity-50",
      )}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        const path = e.dataTransfer.getData(DRAG_MIME);
        if (!path) return;
        e.preventDefault();
        setOver(false);
        onDrop();
      }}
    >
      <Trash2 className="size-5 text-destructive" />
      <div className="flex flex-col text-xs">
        <span className="font-medium text-destructive">Drop to remove</span>
        <span className="font-mono text-[10px] text-muted-foreground">
          {filename}
        </span>
      </div>
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
