/**
 * MultiCamColumn -- the 380px docked right column on the Audit page.
 *
 * Replaces the floating a floating bay. Video lives in a fixed structural slot
 * so the waveform owns the left column and there's no overlap with the
 * jobs surface. The column is the same width regardless of cam count;
 * multicam fits within it (focus + thumbnails) rather than pushing the
 * waveform around.
 *
 * Layout strategy:
 *   1 cam  -> single 380x220 primary tile.
 *   2 cams -> 380x180 primary + 92h secondary strip below.
 *   3+ cams -> 380x180 primary + thumbnail row (~72h each) below.
 *
 * The "Focus / Grid" segmented control at the top hints that an equal
 * 2x2 grid mode is available -- the host owns the Grid modal (see
 * CamGridModal). The shared transport (play / pause / loop / step
 * frame) lives in the column footer; this is the single source of
 * playback truth for the operator.
 */

import { Maximize2, Pause, Play, Plus, Repeat, X } from "lucide-react";
import type { ReactNode } from "react";

import { CamSyncPill, type CamSyncState } from "@/components/audit/CamSyncPill";
import type { StageVideo } from "@/lib/api";
import { cn } from "@/lib/utils";

const COLUMN_WIDTH = 380;
const PRIMARY_HEIGHT_SOLO = 220;
const PRIMARY_HEIGHT_MULTI = 180;

export type CamLayout = "focus" | "grid";

export interface MultiCamColumnProps {
  videos: StageVideo[];
  activeIndex: number;
  onActiveIndexChange: (i: number) => void;
  camSyncStates: CamSyncState[];
  primaryBeepTime: number | null;
  onStartSync: (video: StageVideo) => void;
  /** Promote a secondary cam to primary. */
  onPromote: (video: StageVideo) => void;
  /** Open the fullscreen grid review modal. Only meaningful when
   *  ``videos.length >= 2``. */
  layout: CamLayout;
  onLayoutChange: (layout: CamLayout) => void;
  /** Shared transport state -- rendered in the column footer. */
  isPlaying: boolean;
  loopMode: boolean;
  currentTime: number;
  duration: number;
  onTogglePlay: () => void;
  onToggleLoop: () => void;
  onStepFrame: (dir: -1 | 1) => void;
  /** The primary video element renders here (passed in so the page can
   *  own the <video> ref + secondary refs map). */
  children: ReactNode;
  className?: string;
}

export function MultiCamColumn({
  videos,
  activeIndex,
  onActiveIndexChange: _onActiveIndexChange,
  camSyncStates,
  primaryBeepTime,
  onStartSync,
  onPromote,
  layout,
  onLayoutChange,
  isPlaying,
  loopMode,
  currentTime,
  duration,
  onTogglePlay,
  onToggleLoop,
  onStepFrame,
  children,
  className,
}: MultiCamColumnProps) {
  const count = videos.length;
  if (count === 0) return null;
  const primary = videos[0];
  const secondaries = videos.slice(1);
  const primaryHeight = count === 1 ? PRIMARY_HEIGHT_SOLO : PRIMARY_HEIGHT_MULTI;
  const primarySyncState = camSyncStates[0] ?? "no_beep";

  return (
    <aside
      aria-label={`Cameras (${count})`}
      style={{ width: COLUMN_WIDTH }}
      className={cn(
        "flex shrink-0 flex-col gap-2",
        className,
      )}
    >
      {/* Column header: kicker + Focus/Grid segmented + cam-count tag */}
      <div className="flex items-center gap-2 px-0.5">
        <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] tabular-nums text-subtle">
          Cameras · {pad2(count)}
        </span>
        <span aria-hidden className="h-px flex-1 bg-rule" />
        {count >= 2 ? (
          <div className="inline-flex rounded-full border border-rule bg-surface-2 p-0.5">
            {(["focus", "grid"] as const).map((opt) => {
              const active = layout === opt;
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() => onLayoutChange(opt)}
                  className={cn(
                    "rounded-full px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em] transition-colors",
                    active
                      ? "bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_8px_var(--color-led-glow)]"
                      : "text-muted hover:text-ink",
                  )}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* Primary tile. The actual <video> renders via {children} so the
          Audit page keeps owning the ref + secondary plumbing. */}
      <div
        style={{ height: primaryHeight }}
        className="relative overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.02),0_18px_36px_-24px_rgba(0,0,0,0.7)]"
      >
        <span
          className="absolute left-2.5 top-2 z-[2] inline-flex items-center gap-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-led-text"
        >
          <span
            aria-hidden
            className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)]"
          />
          {primary.role === "primary" ? "Primary" : `Cam ${activeIndex + 1}`}
        </span>
        <span className="absolute right-2 top-2 z-[2]">
          <CamSyncPill
            state={primarySyncState}
            beepTime={primary.beep_time}
            beepConfidence={primary.beep_confidence}
            offsetSeconds={null}
            size="xs"
            onClick={() => onStartSync(primary)}
          />
        </span>
        <div className="absolute inset-0">{children}</div>
      </div>

      {/* Secondaries: count===2 -> strip; count>=3 -> thumb grid. */}
      {count === 2 ? (
        <CamStrip
          cam={secondaries[0]}
          index={1}
          syncState={camSyncStates[1] ?? "no_beep"}
          primaryBeepTime={primaryBeepTime}
          onPromote={() => onPromote(secondaries[0])}
          onStartSync={() => onStartSync(secondaries[0])}
        />
      ) : null}
      {count >= 3 ? (
        <div
          className="grid gap-1.5"
          style={{ gridTemplateColumns: `repeat(${secondaries.length}, minmax(0, 1fr))` }}
        >
          {secondaries.map((cam, i) => (
            <CamThumb
              key={cam.video_id}
              cam={cam}
              index={i + 1}
              syncState={camSyncStates[i + 1] ?? "no_beep"}
              primaryBeepTime={primaryBeepTime}
              onPromote={() => onPromote(cam)}
              onStartSync={() => onStartSync(cam)}
            />
          ))}
        </div>
      ) : null}

      {/* Sync row -- only meaningful when more than one cam is wired. */}
      {count >= 2 ? (
        <CamSyncRow
          videos={videos}
          primaryBeepTime={primaryBeepTime}
        />
      ) : null}

      {/* Transport footer. Single source of playback truth for the
          audit page -- it used to live in a floating bay; now it docks here so
          the operator can scrub without ever leaving the column. */}
      <div className="flex items-center gap-2 rounded-md border border-rule bg-surface-2 px-2.5 py-1.5">
        <button
          type="button"
          onClick={onTogglePlay}
          title={isPlaying ? "Pause (Space)" : "Play (Space)"}
          aria-label={isPlaying ? "Pause (Space)" : "Play (Space)"}
          className="inline-flex size-6 items-center justify-center rounded-full border-0 bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
        >
          {isPlaying ? <Pause className="size-3" /> : <Play className="size-3" />}
        </button>
        <span className="font-mono text-[0.6875rem] tabular-nums text-ink-2">
          {currentTime.toFixed(3)}
          <span className="text-subtle">/{duration.toFixed(2)}s</span>
        </span>
        <span
          aria-hidden
          className="ml-auto font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-subtle"
        >
          all cams · linked
        </span>
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleLoop}
            aria-pressed={loopMode}
            title="Loop (R)"
            aria-label={loopMode ? "Loop on (R)" : "Loop off (R)"}
            className={cn(
              "inline-flex size-[22px] items-center justify-center rounded-sm border transition-colors",
              loopMode
                ? "border-led bg-led/10 text-led shadow-[0_0_8px_var(--color-led-glow)]"
                : "border-rule bg-transparent text-muted hover:border-rule-strong hover:text-ink-2",
            )}
          >
            <Repeat className="size-3" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onStepFrame(-1)}
            title="Step frame back (Shift+Left)"
            aria-label="Step frame back"
            className="inline-flex size-[22px] items-center justify-center rounded-sm border border-rule font-mono text-[0.625rem] font-bold text-muted transition-colors hover:border-rule-strong hover:text-ink-2"
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() => onStepFrame(1)}
            title="Step frame forward (Shift+Right)"
            aria-label="Step frame forward"
            className="inline-flex size-[22px] items-center justify-center rounded-sm border border-rule font-mono text-[0.625rem] font-bold text-muted transition-colors hover:border-rule-strong hover:text-ink-2"
          >
            ›
          </button>
        </div>
      </div>
    </aside>
  );
}

/* -------------------------------------------------------------------------- */
/* CamStrip -- slim 92h secondary tile (count === 2)                          */
/* -------------------------------------------------------------------------- */

interface CamStripProps {
  cam: StageVideo;
  index: number;
  syncState: CamSyncState;
  primaryBeepTime: number | null;
  onPromote: () => void;
  onStartSync: () => void;
}

function CamStrip({
  cam,
  index,
  syncState,
  primaryBeepTime,
  onPromote,
  onStartSync,
}: CamStripProps) {
  const delta =
    primaryBeepTime != null && cam.beep_time != null
      ? cam.beep_time - primaryBeepTime
      : null;
  return (
    <div className="relative h-[92px] w-full overflow-hidden rounded-2xl border border-rule bg-surface">
      <span className="absolute left-2 top-1.5 z-[2] inline-flex items-center gap-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-ink-2">
        <span
          aria-hidden
          className="inline-block size-[5px] rounded-full bg-ink-2 shadow-[0_0_6px_rgba(255,255,255,0.2)]"
        />
        Cam {index + 1} · Secondary
        {delta != null && Math.abs(delta) > 0.0005 ? (
          <span className="ml-1 rounded-full border border-rule bg-bg/70 px-1.5 py-px font-mono text-[0.5625rem] tabular-nums text-ink-2">
            Δ {fmtDelta(delta)}
          </span>
        ) : null}
      </span>
      <span className="absolute right-1.5 top-1.5 z-[2] inline-flex items-center gap-1">
        <CamSyncPill
          state={syncState}
          beepTime={cam.beep_time}
          beepConfidence={cam.beep_confidence}
          offsetSeconds={delta}
          size="xs"
          onClick={onStartSync}
        />
      </span>
      <div className="absolute inset-0 flex items-center justify-center text-subtle">
        <Maximize2 className="size-4" aria-hidden />
      </div>
      <div className="absolute inset-x-2 bottom-1.5 flex items-center gap-1.5">
        <span
          aria-hidden
          className="relative h-[2px] flex-1 overflow-hidden rounded-[1px] bg-white/10"
        />
        <button
          type="button"
          onClick={onPromote}
          title="Promote to primary"
          className="rounded-full border border-rule bg-bg/70 px-2 py-0.5 font-display text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-3"
        >
          Focus
        </button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* CamThumb -- 72h thumbnail (count >= 3)                                     */
/* -------------------------------------------------------------------------- */

interface CamThumbProps {
  cam: StageVideo;
  index: number;
  syncState: CamSyncState;
  primaryBeepTime: number | null;
  onPromote: () => void;
  onStartSync: () => void;
}

function CamThumb({
  cam,
  index,
  syncState,
  primaryBeepTime,
  onPromote,
  onStartSync,
}: CamThumbProps) {
  const delta =
    primaryBeepTime != null && cam.beep_time != null
      ? cam.beep_time - primaryBeepTime
      : null;
  return (
    <button
      type="button"
      onClick={onPromote}
      title={`Focus Cam ${index + 1}`}
      className="relative h-[72px] overflow-hidden rounded-md border border-rule bg-surface text-left transition-colors hover:bg-surface-2"
    >
      <span className="absolute left-1.5 top-1 inline-flex items-center gap-1 font-mono text-[0.5rem] font-bold uppercase tracking-[0.1em] text-ink-2">
        <span
          aria-hidden
          className="inline-block size-1 rounded-full bg-ink-2 shadow-[0_0_4px_rgba(255,255,255,0.2)]"
        />
        Cam {index + 1}
      </span>
      <span className="absolute inset-0 flex items-center justify-center text-subtle">
        <Maximize2 className="size-3.5" aria-hidden />
      </span>
      <span className="absolute inset-x-1.5 bottom-1 flex items-center justify-between font-mono text-[0.5rem] font-bold uppercase tracking-[0.06em] text-ink-2">
        <span>Secondary</span>
        {delta != null && Math.abs(delta) > 0.0005 ? (
          <span className="text-subtle">Δ {fmtDeltaShort(delta)}</span>
        ) : null}
      </span>
      {/* Sync pill rides the top-right corner; clicking it shouldn't also
          fire the parent's onPromote, so swallow propagation. */}
      <span
        className="absolute right-1 top-1"
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <CamSyncPill
          state={syncState}
          beepTime={cam.beep_time}
          beepConfidence={cam.beep_confidence}
          offsetSeconds={delta}
          size="xs"
          onClick={onStartSync}
        />
      </span>
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* CamSyncRow -- "Synced to primary · max Δ +xs" + Re-sync                    */
/* -------------------------------------------------------------------------- */

function CamSyncRow({
  videos,
  primaryBeepTime,
}: {
  videos: StageVideo[];
  primaryBeepTime: number | null;
}) {
  const deltas: number[] = [];
  if (primaryBeepTime != null) {
    for (let i = 1; i < videos.length; i++) {
      const t = videos[i].beep_time;
      if (t != null) deltas.push(t - primaryBeepTime);
    }
  }
  const absMax = deltas.length
    ? deltas.reduce((m, d) => (Math.abs(d) > Math.abs(m) ? d : m), 0)
    : 0;
  return (
    <div className="flex items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1.5">
      <span
        aria-hidden
        className="inline-block size-1.5 rounded-full bg-done shadow-[0_0_6px_var(--color-done-glow)]"
      />
      <span className="font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2">
        Synced to primary beep
      </span>
      <span className="ml-auto font-mono text-[0.6875rem] tabular-nums text-subtle">
        max Δ {deltas.length ? fmtDelta(absMax) : "+0.000s"}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* helpers                                                                    */
/* -------------------------------------------------------------------------- */

function fmtDelta(d: number): string {
  return (d >= 0 ? "+" : "") + d.toFixed(3) + "s";
}
function fmtDeltaShort(d: number): string {
  return (d >= 0 ? "+" : "") + d.toFixed(2) + "s";
}
function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/* Keep imports used by the JSX (lucide tree-shake hint). */
void Plus;
void X;
