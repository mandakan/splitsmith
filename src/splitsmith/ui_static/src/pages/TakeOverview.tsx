/**
 * Take overview (/match/:matchId/take/:slug/:filename) - clip-level review
 * of how one long single-take recording was carved into per-stage beep
 * search windows.
 *
 * Surfaces, per covered stage: the beep search window (shaded region on
 * the whole-file envelope, draggable edges), the detected beep + its
 * confidence, the detection status, and beep conflicts (two stages whose
 * beeps landed too close together). Dragging a window edge arms a
 * per-stage "Re-run detection" action that PUTs the manual window and
 * re-queues detection.
 *
 * Review state is the backend's ``beep_reviewed`` field, rendered
 * read-only here - confirming a beep happens on the beep-review page,
 * which each stage row links into. This page adds NO parallel confirmed
 * state.
 *
 * Peaks may 202 in hosted mode while the worker computes them:
 * ``active_job: true`` polls with a notice; ``active_job: false`` stops
 * polling and offers a manual detection re-run instead of spinning
 * forever.
 *
 * Mounted under <MatchShell />.
 */

import {
  AlertTriangle,
  ArrowRight,
  Check,
  Loader2,
  Pencil,
  RefreshCw,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CoverageSelect } from "@/components/ingest/CoverageSelect";
import { Waveform } from "@/components/Waveform";
import { Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { StatusPill } from "@/components/ui/StatusPill";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  type PeaksResult,
  type StageEntry,
  type TakeOverview as TakeOverviewData,
  type TakeOverviewStage,
} from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

/** Poll cadence while worker-side detection / peaks generation runs. */
const POLL_INTERVAL_MS = 3000;
/** Narrowest permitted beep-search window while dragging an edge. */
const MIN_WINDOW_S = 2;
/** Coarse drag grid - beep windows are search bounds, not beep times. */
const WINDOW_SNAP_S = 0.5;
/** Keyboard nudge steps for a focused window handle. */
const NUDGE_S = 1;
const FINE_NUDGE_S = 0.1;

type TimeWindow = [number, number];

type PeaksState =
  | { kind: "loading" }
  | { kind: "ready"; peaks: PeaksResult }
  | { kind: "pending"; activeJob: boolean }
  | { kind: "error"; message: string };

export function TakeOverview() {
  const { slug, filename } = useParams<{ slug: string; filename: string }>();
  if (!slug || !filename) return null;
  return <TakeOverviewInner key={`${slug}::${filename}`} slug={slug} filename={filename} />;
}

function TakeOverviewInner({ slug, filename }: { slug: string; filename: string }) {
  const href = useMatchHref();
  const confirm = useConfirm();
  const [overview, setOverview] = useState<TakeOverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [peaksState, setPeaksState] = useState<PeaksState>({ kind: "loading" });
  // All match stages, for the coverage editor (the overview only carries
  // the covered ones).
  const [allStages, setAllStages] = useState<StageEntry[]>([]);
  // Locally edited beep windows, keyed by stage number. Editing never
  // writes to the server on its own - "Re-run detection" applies.
  const [draftWindows, setDraftWindows] = useState<Record<number, TimeWindow>>({});
  const [applyingStage, setApplyingStage] = useState<number | null>(null);
  const [queueBusy, setQueueBusy] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [coverageBusy, setCoverageBusy] = useState(false);

  const loadOverview = useCallback(async () => {
    try {
      const o = await api.takeOverview(slug, filename);
      setOverview(o);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [slug, filename]);

  const loadPeaks = useCallback(async () => {
    try {
      const r = await api.takePeaks(slug, filename);
      if ("pending" in r) {
        setPeaksState({ kind: "pending", activeJob: r.active_job });
      } else {
        setPeaksState({ kind: "ready", peaks: r });
      }
    } catch (e) {
      setPeaksState({
        kind: "error",
        message: e instanceof ApiError ? e.detail : String(e),
      });
    }
  }, [slug, filename]);

  useEffect(() => {
    void loadOverview();
    void loadPeaks();
  }, [loadOverview, loadPeaks]);

  useEffect(() => {
    let alive = true;
    void api
      .getProject(slug)
      .then((p) => {
        if (alive) setAllStages(p.stages);
      })
      .catch(() => {
        /* coverage editor just won't open without stages */
      });
    return () => {
      alive = false;
    };
  }, [slug]);

  // Poll while detection runs (any stage still "pending") or while the
  // worker is generating peaks. When peaks are pending WITHOUT an active
  // job, do NOT poll - the notice offers a manual re-run instead.
  const detectionPending =
    overview?.stages.some((s) => s.status === "pending") ?? false;
  const peaksGenerating = peaksState.kind === "pending" && peaksState.activeJob;
  const peaksReady = peaksState.kind === "ready";
  useEffect(() => {
    if (!detectionPending && !peaksGenerating) return;
    const t = window.setInterval(() => {
      void loadOverview();
      if (!peaksReady) void loadPeaks();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [detectionPending, peaksGenerating, peaksReady, loadOverview, loadPeaks]);

  const stages = useMemo(() => overview?.stages ?? [], [overview]);
  const conflictSet = useMemo(
    () => new Set(overview?.conflicts ?? []),
    [overview],
  );

  const duration =
    (peaksState.kind === "ready" ? peaksState.peaks.duration : null) ??
    overview?.duration_seconds ??
    0;

  const effectiveWindow = useCallback(
    (s: TakeOverviewStage): TimeWindow | null =>
      draftWindows[s.stage_number] ?? s.beep_window,
    [draftWindows],
  );

  const isDirty = useCallback(
    (s: TakeOverviewStage): boolean => {
      const d = draftWindows[s.stage_number];
      if (d == null) return false;
      const w = s.beep_window;
      return w == null || d[0] !== w[0] || d[1] !== w[1];
    },
    [draftWindows],
  );

  const setDraft = useCallback((stageNumber: number, w: TimeWindow) => {
    setDraftWindows((m) => ({ ...m, [stageNumber]: w }));
  }, []);

  const discardDraft = useCallback((stageNumber: number) => {
    setDraftWindows((m) => {
      const next = { ...m };
      delete next[stageNumber];
      return next;
    });
  }, []);

  async function applyWindow(s: TakeOverviewStage) {
    const w = draftWindows[s.stage_number];
    if (w == null || applyingStage != null) return;
    const ok = await confirm({
      title: `Re-run detection on stage ${pad2(s.stage_number)}?`,
      body: `The new search window (${formatClock(w[0])} - ${formatClock(w[1])}) replaces the current beep and discards this stage's trim and shot audit. Detection re-runs inside the window.`,
      confirmLabel: "Re-run detection",
    });
    if (!ok.confirmed) return;
    setApplyingStage(s.stage_number);
    try {
      await api.setBeepWindow(slug, s.stage_number, s.video_id, {
        start_s: w[0],
        end_s: w[1],
      });
      discardDraft(s.stage_number);
      await loadOverview();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setApplyingStage(null);
    }
  }

  // Manual re-run affordance for the "peaks pending, no active job" state.
  // Prefers a stage without a beep (nothing to lose); falls back to a
  // confirm-gated force re-detect on the first stage when every stage
  // already has one.
  async function runDetection() {
    if (stages.length === 0) return;
    const target = stages.find((st) => st.status !== "found");
    const s = target ?? stages[0];
    if (target == null) {
      const ok = await confirm({
        title: "Re-run detection to generate the waveform?",
        body: `Every stage on this take already has a beep. Re-running detection on stage ${pad2(s.stage_number)} discards its beep, trim, and shot audit.`,
        confirmLabel: "Re-run detection",
      });
      if (!ok.confirmed) return;
    }
    setQueueBusy(true);
    try {
      await api.detectBeepForVideo(slug, s.stage_number, s.video_id, target == null);
      setPeaksState((p) => (p.kind === "ready" ? p : { kind: "pending", activeJob: true }));
      await loadOverview();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setQueueBusy(false);
    }
  }

  async function applyCoverage(covers: number[]) {
    setCoverageBusy(true);
    try {
      await api.setRawVideoCoverage(slug, {
        filename,
        covers_stages: covers,
      });
      setCoverageOpen(false);
      await loadOverview();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setCoverageBusy(false);
    }
  }

  const rv = overview?.raw_video ?? null;

  return (
    <div className="px-7 py-5">
      {/* Head */}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Kicker className="mb-2">Take overview &middot; multi-stage clip</Kicker>
          <h1 className="mb-2 max-w-full truncate font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            {filename}
          </h1>
          <p className="max-w-[44rem] text-sm text-muted">
            One recording, {stages.length}{" "}
            {stages.length === 1 ? "stage" : "stages"}. Check that each
            stage's beep search window sits over the right part of the file
            - drag a window's edges and re-run detection when it doesn't.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle tabular-nums">
            {duration > 0 && <span>{formatClock(duration)} total</span>}
            {rv != null && rv.size_bytes > 0 && (
              <span>{formatSize(rv.size_bytes)}</span>
            )}
            <Link
              to={href("ingest", slug)}
              className="inline-flex items-center gap-1 text-subtle underline-offset-2 hover:text-ink-2 hover:underline"
            >
              Back to videos
            </Link>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setCoverageOpen(true)}
          disabled={allStages.length === 0}
        >
          <Pencil className="size-3.5" />
          <span className="font-display uppercase tracking-[0.08em]">
            Edit coverage
          </span>
        </Button>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      )}

      {/* Conflict banner - color is not the only carrier: icon + text. */}
      {overview != null && overview.conflicts.length > 0 && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-live/40 bg-live/[0.08] px-4 py-3 text-[0.8125rem] text-ink-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-live" />
          <div>
            <b className="font-bold text-live">
              Stages {overview.conflicts.map(pad2).join(", ")} share a beep
            </b>{" "}
            - their detected beeps sit close enough that two stages may have
            latched onto the same tone. Narrow the windows below and re-run
            detection.
          </div>
        </div>
      )}

      {/* Waveform panel */}
      <section className="mb-5 rounded-xl border border-rule-strong bg-surface p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <Kicker>Whole-file envelope</Kicker>
          {detectionPending && (
            <span className="inline-flex items-center gap-1.5 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-live">
              <Loader2 aria-hidden className="size-3 animate-spin" />
              Detection running
            </span>
          )}
        </div>
        {peaksState.kind === "ready" && duration > 0 ? (
          <Waveform
            peaks={peaksState.peaks.peaks}
            duration={duration}
            currentTime={0}
            onScrub={() => {}}
            height={180}
            ariaLabel={`Whole-file waveform for ${filename}`}
          >
            <WindowLayer
              stages={stages}
              duration={duration}
              conflictSet={conflictSet}
              windowFor={effectiveWindow}
              dirtyFor={isDirty}
              onWindowChange={setDraft}
            />
          </Waveform>
        ) : peaksState.kind === "loading" ? (
          <WaveformNotice role="status">
            <Loader2 aria-hidden className="size-4 animate-spin text-beep" />
            Loading waveform...
          </WaveformNotice>
        ) : peaksState.kind === "pending" && peaksState.activeJob ? (
          <WaveformNotice role="status">
            <Loader2 aria-hidden className="size-4 animate-spin text-beep" />
            The waveform is being generated on the worker - detection is
            running. This page refreshes on its own.
          </WaveformNotice>
        ) : peaksState.kind === "pending" ? (
          <WaveformNotice>
            <AlertTriangle aria-hidden className="size-4 text-live" />
            <span>
              No waveform yet and no detection job is running. Run detection
              to generate it.
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void runDetection()}
              disabled={queueBusy || stages.length === 0}
            >
              <RefreshCw className={cn("size-3.5", queueBusy && "animate-spin")} />
              <span className="font-display uppercase tracking-[0.08em]">
                {queueBusy ? "Queuing..." : "Run detection"}
              </span>
            </Button>
          </WaveformNotice>
        ) : peaksState.kind === "error" ? (
          <WaveformNotice>
            <AlertTriangle aria-hidden className="size-4 text-led" />
            Waveform unavailable: {peaksState.message}
          </WaveformNotice>
        ) : (
          <WaveformNotice>
            <AlertTriangle aria-hidden className="size-4 text-led" />
            Waveform unavailable: the file reports no duration.
          </WaveformNotice>
        )}
      </section>

      {/* Per-stage rows */}
      <section className="flex flex-col gap-2.5">
        {overview == null && error == null && (
          <div className="rounded-xl border border-rule-strong bg-surface px-4 py-6 text-center font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
            Loading take...
          </div>
        )}
        {overview != null && stages.length === 0 && (
          <div className="rounded-xl border border-dashed border-rule-strong bg-surface-2/40 px-4 py-6 text-center text-sm text-muted">
            No stage videos are registered for this take yet - use Edit
            coverage to declare which stages it covers.
          </div>
        )}
        {stages.map((s) => (
          <StageRow
            key={s.stage_number}
            stage={s}
            window={effectiveWindow(s)}
            dirty={isDirty(s)}
            conflictsWith={
              conflictSet.has(s.stage_number)
                ? (overview?.conflicts ?? []).filter((n) => n !== s.stage_number)
                : []
            }
            applying={applyingStage === s.stage_number}
            onDiscard={() => discardDraft(s.stage_number)}
            onApply={() => void applyWindow(s)}
            reviewHref={`${href("beep-review")}?focus=${encodeURIComponent(
              `${slug}::${s.stage_number}::${s.video_id}`,
            )}`}
          />
        ))}
      </section>

      {coverageOpen && rv != null && (
        <CoverageDialog
          filename={filename}
          stages={allStages}
          initial={rv.covers_stages}
          busy={coverageBusy}
          onApply={(covers) => void applyCoverage(covers)}
          onClose={() => {
            if (!coverageBusy) setCoverageOpen(false);
          }}
        />
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Waveform overlay: per-stage windows + beep markers                         */
/* -------------------------------------------------------------------------- */

function WindowLayer({
  stages,
  duration,
  conflictSet,
  windowFor,
  dirtyFor,
  onWindowChange,
}: {
  stages: TakeOverviewStage[];
  duration: number;
  conflictSet: Set<number>;
  windowFor: (s: TakeOverviewStage) => TimeWindow | null;
  dirtyFor: (s: TakeOverviewStage) => boolean;
  onWindowChange: (stageNumber: number, w: TimeWindow) => void;
}) {
  const layerRef = useRef<HTMLDivElement | null>(null);
  // Drag state in a ref so live window updates don't reset the gesture
  // (same pattern as MarkerLayer).
  const dragRef = useRef<{
    pointerId: number;
    element: HTMLButtonElement;
    stageNumber: number;
    edge: "start" | "end";
    startWindow: TimeWindow;
  } | null>(null);

  const timeFromClientX = useCallback(
    (clientX: number): number => {
      const el = layerRef.current;
      if (!el || duration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return 0;
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(ratio, 0), 1) * duration;
    },
    [duration],
  );

  const moveEdge = useCallback(
    (w: TimeWindow, edge: "start" | "end", t: number): TimeWindow => {
      const snapped = Math.round(t / WINDOW_SNAP_S) * WINDOW_SNAP_S;
      if (edge === "start") {
        const start = Math.max(0, Math.min(snapped, w[1] - MIN_WINDOW_S));
        return [start, w[1]];
      }
      const end = Math.min(duration, Math.max(snapped, w[0] + MIN_WINDOW_S));
      return [w[0], end];
    },
    [duration],
  );

  const handlePointerDown = useCallback(
    (
      e: React.PointerEvent<HTMLButtonElement>,
      stageNumber: number,
      edge: "start" | "end",
      w: TimeWindow,
    ) => {
      if (e.button !== 0) return;
      // Keep the press away from the waveform's scrub slider.
      e.stopPropagation();
      e.preventDefault();
      const el = e.currentTarget;
      el.setPointerCapture(e.pointerId);
      dragRef.current = {
        pointerId: e.pointerId,
        element: el,
        stageNumber,
        edge,
        startWindow: w,
      };
    },
    [],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, w: TimeWindow) => {
      const drag = dragRef.current;
      if (drag?.pointerId !== e.pointerId) return;
      onWindowChange(
        drag.stageNumber,
        moveEdge(w, drag.edge, timeFromClientX(e.clientX)),
      );
    },
    [moveEdge, timeFromClientX, onWindowChange],
  );

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== e.pointerId) return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    dragRef.current = null;
  }, []);

  // Escape cancels an in-flight drag and restores the pre-drag window.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const drag = dragRef.current;
      if (e.key !== "Escape" || !drag) return;
      e.preventDefault();
      if (drag.element.hasPointerCapture(drag.pointerId)) {
        drag.element.releasePointerCapture(drag.pointerId);
      }
      onWindowChange(drag.stageNumber, drag.startWindow);
      dragRef.current = null;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onWindowChange]);

  const handleKeyDown = useCallback(
    (
      e: React.KeyboardEvent<HTMLButtonElement>,
      stageNumber: number,
      edge: "start" | "end",
      w: TimeWindow,
    ) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const dir = e.key === "ArrowRight" ? 1 : -1;
      const step = e.shiftKey ? FINE_NUDGE_S : NUDGE_S;
      const t = (edge === "start" ? w[0] : w[1]) + dir * step;
      onWindowChange(stageNumber, moveEdge(w, edge, t));
    },
    [moveEdge, onWindowChange],
  );

  if (duration <= 0) return null;

  return (
    <div ref={layerRef} className="pointer-events-none absolute inset-0">
      {stages.map((s, i) => {
        const w = windowFor(s);
        if (w == null) return null;
        const left = (Math.max(0, w[0]) / duration) * 100;
        const width = (Math.max(0, w[1] - w[0]) / duration) * 100;
        const dirty = dirtyFor(s);
        const conflict = conflictSet.has(s.stage_number);
        // Alternate tints so adjacent windows read apart; the in-region
        // label carries the identity (color is never the sole carrier).
        const even = i % 2 === 0;
        return (
          <div
            key={s.stage_number}
            className={cn(
              "absolute inset-y-0 border-x",
              dirty
                ? "border-led/70 bg-led/10"
                : even
                  ? "border-beep/40 bg-beep/10"
                  : "border-live/40 bg-live/10",
            )}
            style={{ left: `${left}%`, width: `${width}%` }}
          >
            <span
              className={cn(
                "absolute left-1 top-1 max-w-[calc(100%-8px)] truncate rounded px-1 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.06em]",
                dirty
                  ? "bg-led/20 text-led"
                  : even
                    ? "bg-beep/20 text-beep"
                    : "bg-live/20 text-live",
              )}
            >
              S{pad2(s.stage_number)} {s.stage_name}
              {dirty && " - edited"}
              {conflict && !dirty && " - conflict"}
            </span>
            <WindowHandle
              edge="start"
              label={`Stage ${pad2(s.stage_number)} window start, ${formatClock(w[0])} - drag or use arrow keys`}
              onPointerDown={(e) => handlePointerDown(e, s.stage_number, "start", w)}
              onPointerMove={(e) => handlePointerMove(e, w)}
              onPointerUp={handlePointerUp}
              onKeyDown={(e) => handleKeyDown(e, s.stage_number, "start", w)}
            />
            <WindowHandle
              edge="end"
              label={`Stage ${pad2(s.stage_number)} window end, ${formatClock(w[1])} - drag or use arrow keys`}
              onPointerDown={(e) => handlePointerDown(e, s.stage_number, "end", w)}
              onPointerMove={(e) => handlePointerMove(e, w)}
              onPointerUp={handlePointerUp}
              onKeyDown={(e) => handleKeyDown(e, s.stage_number, "end", w)}
            />
          </div>
        );
      })}
      {/* Beep markers, positioned on the full-file scale (independent of
          any in-progress window edit) so a beep never disappears while
          its window is being dragged. */}
      {stages.map((s) => {
        if (s.beep_time == null || s.beep_time > duration) return null;
        const label = `Stage ${pad2(s.stage_number)} beep at ${formatClock(s.beep_time)}${
          s.beep_confidence != null
            ? `, confidence ${(s.beep_confidence * 100).toFixed(0)}%`
            : ""
        }`;
        return (
          <span
            key={`beep-${s.stage_number}`}
            role="img"
            aria-label={label}
            title={label}
            className="pointer-events-auto absolute bottom-0 top-6 w-px bg-done shadow-[0_0_6px_var(--color-done-glow)]"
            style={{ left: `${(s.beep_time / duration) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

function WindowHandle({
  edge,
  label,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onKeyDown,
}: {
  edge: "start" | "end";
  label: string;
  onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      data-audit-marker
      aria-label={label}
      title={label}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
      className={cn(
        "group pointer-events-auto absolute inset-y-0 w-2.5 cursor-ew-resize touch-none outline-none",
        edge === "start" ? "left-0 -translate-x-1/2" : "right-0 translate-x-1/2",
        "focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-ink-2/60 group-hover:bg-ink group-focus-visible:bg-ink"
      />
      <span
        aria-hidden
        className="absolute top-1/2 left-1/2 h-6 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-rule-strong bg-surface-3 group-hover:bg-surface-2"
      />
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Stage rows                                                                 */
/* -------------------------------------------------------------------------- */

function StageRow({
  stage,
  window: w,
  dirty,
  conflictsWith,
  applying,
  onDiscard,
  onApply,
  reviewHref,
}: {
  stage: TakeOverviewStage;
  window: TimeWindow | null;
  dirty: boolean;
  conflictsWith: number[];
  applying: boolean;
  onDiscard: () => void;
  onApply: () => void;
  reviewHref: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-rule-strong bg-surface px-4 py-3">
      <div className="min-w-[180px]">
        <div className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
          Stage {pad2(stage.stage_number)}
        </div>
        <div className="truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {stage.stage_name}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {stage.status === "found" ? (
          <StatusPill tone="exported">Found</StatusPill>
        ) : stage.status === "none" ? (
          <StatusPill tone="led">No beep</StatusPill>
        ) : (
          <StatusPill tone="in-progress">Pending</StatusPill>
        )}
        {conflictsWith.length > 0 && (
          <StatusPill tone="led" icon={<AlertTriangle aria-hidden className="size-3" />}>
            Shares a beep with stage {conflictsWith.map(pad2).join(", ")}
          </StatusPill>
        )}
        {stage.status === "found" &&
          (stage.beep_reviewed ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-done/40 bg-done/10 px-2 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-done">
              <Check aria-hidden className="size-3" /> Reviewed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full border border-rule-strong bg-surface-2 px-2 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted">
              Unreviewed
            </span>
          ))}
      </div>

      <div className="font-mono text-[0.6875rem] tabular-nums text-ink-2">
        {stage.beep_time != null ? (
          <>
            beep {formatClock(stage.beep_time)}
            {stage.beep_confidence != null && (
              <span className="text-muted">
                {" "}
                &middot; {(stage.beep_confidence * 100).toFixed(0)}%
              </span>
            )}
          </>
        ) : (
          <span className="text-muted">no beep yet</span>
        )}
      </div>

      <div className="font-mono text-[0.6875rem] tabular-nums text-muted">
        {w != null ? (
          <>
            window {formatClock(w[0])} - {formatClock(w[1])}
            <span className="text-subtle">
              {" "}
              &middot; {dirty ? "edited" : (stage.beep_window_source ?? "auto")}
            </span>
          </>
        ) : (
          "no search window"
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        {dirty && (
          <>
            <button
              type="button"
              onClick={onDiscard}
              disabled={applying}
              className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 transition-colors hover:bg-surface-3 disabled:opacity-50"
            >
              <Undo2 aria-hidden className="size-3" /> Discard
            </button>
            <button
              type="button"
              onClick={onApply}
              disabled={applying}
              className="inline-flex items-center gap-1.5 rounded-md border border-led/60 bg-led/10 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-led transition-colors hover:bg-led/20 disabled:opacity-50"
            >
              {applying ? (
                <Loader2 aria-hidden className="size-3 animate-spin" />
              ) : (
                <RefreshCw aria-hidden className="size-3" />
              )}
              {applying ? "Queuing..." : "Re-run detection"}
            </button>
          </>
        )}
        <Link
          to={reviewHref}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 transition-colors hover:border-beep/60 hover:bg-beep/10 hover:text-beep"
        >
          Review beep <ArrowRight aria-hidden className="size-3" />
        </Link>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Coverage dialog                                                            */
/* -------------------------------------------------------------------------- */

function CoverageDialog({
  filename,
  stages,
  initial,
  busy,
  onApply,
  onClose,
}: {
  filename: string;
  stages: StageEntry[];
  initial: number[];
  busy: boolean;
  onApply: (covers: number[]) => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState<number[]>(initial);
  useDialogFocus(true, panelRef, onClose, { disableEscape: busy });
  const unchanged =
    draft.length === initial.length && draft.every((v, i) => v === initial[i]);

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="take-coverage-title"
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4"
        onClick={onClose}
      >
        <div
          ref={panelRef}
          onClick={(e) => e.stopPropagation()}
          className="flex max-h-[85vh] w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-xl border border-rule-strong bg-surface p-5 shadow-[0_8px_32px_-4px_rgba(0,0,0,0.6)]"
        >
          <div>
            <h2
              id="take-coverage-title"
              className="font-display text-xl font-bold uppercase tracking-tight text-ink"
            >
              Edit coverage
            </h2>
            <p className="mt-1 text-[0.8125rem] leading-relaxed text-muted">
              Which stages does{" "}
              <code className="rounded border border-rule bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
                {filename}
              </code>{" "}
              cover? Removing a stage deletes its video entry for this take
              and invalidates its trim cache; adding one queues beep
              detection.
            </p>
          </div>
          <CoverageSelect stages={stages} value={draft} onChange={setDraft} />
          <div className="flex items-center justify-end gap-2 border-t border-rule pt-3">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => onApply(draft)}
              disabled={busy || unchanged}
              className="bg-led-fill text-ink hover:bg-led"
            >
              {busy && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              {busy ? "Saving..." : "Apply coverage"}
            </Button>
          </div>
        </div>
      </div>
    </Portal>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function WaveformNotice({
  role,
  children,
}: {
  role?: "status";
  children: React.ReactNode;
}) {
  return (
    <div
      role={role}
      className="flex min-h-[180px] flex-wrap items-center justify-center gap-3 rounded-md bg-surface-2/60 px-4 py-6 text-center font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted"
    >
      {children}
    </div>
  );
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** m:ss for coarse take-level positions (a 30-minute file doesn't need
 *  millisecond labels). */
function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
