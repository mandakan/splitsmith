/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
/**
 * MultiCamColumn -- the Audit page's camera column. Audit (the top row's
 * left cell, the shared timeline, #1352) is its only renderer: the column
 * is `w-full` and the primary tile is a 16:9 box as wide as the column,
 * height-capped (the video letterboxes inside it). Secondary tiles keep
 * their fixed size.
 *
 * Replaces the floating a floating bay. Video lives in a fixed structural slot
 * so the waveform owns the left column and there's no overlap with the
 * jobs surface. The column is the same width regardless of cam count;
 * multicam fits within it (focus + thumbnails) rather than pushing the
 * waveform around.
 *
 * Layout strategy:
 *   1 cam  -> single 16:9 primary tile.
 *   2 cams -> 16:9 primary + 92h secondary strip below.
 *   3+ cams -> 16:9 primary + thumbnail row (~72h each) below.
 *
 * On lg the column fills Audit's bounded top row: every sibling of the
 * primary tile (header, strip or thumb row, sync row) is
 * `shrink-0`, so the tile is what flexes, and it never drops below
 * 200 px (Audit raises the row's floor when there is more than one
 * camera so the backstop is not what holds it).
 *
 * The "Focus / Grid" segmented control at the top hints that an equal
 * 2x2 grid mode is available -- the host owns the Grid modal (see
 * CamGridModal). The shared transport (play / pause / loop / step
 * frame) is the timeline band's header (#1359: the column's own footer
 * row repeated its play and clock), so the tile keeps that height.
 */

import { Maximize2 } from "lucide-react";
import type { ReactNode } from "react";

import { CamSyncPill, type CamSyncState } from "@/components/audit/CamSyncPill";
import type { StageVideo } from "@/lib/api";
import { cn } from "@/lib/utils";

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
  children,
  className,
}: MultiCamColumnProps) {
  const count = videos.length;
  if (count === 0) return null;
  const primary = videos[0];
  const secondaries = videos.slice(1);
  const primarySyncState = camSyncStates[0] ?? "no_beep";

  return (
    <aside
      aria-label={`Cameras (${count})`}
      className={cn("flex w-full min-w-0 shrink-0 flex-col gap-2 lg:h-full lg:min-h-0", className)}
    >
      {/* Column header: kicker + Focus/Grid segmented + cam-count tag */}
      <div className="flex shrink-0 items-center gap-2 px-0.5">
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
        data-testid="cam-primary-tile"
        className="relative aspect-video max-h-[max(240px,calc(100dvh-620px))] w-full overflow-hidden lg:aspect-auto lg:max-h-none lg:min-h-[200px] lg:flex-1 rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.02),0_18px_36px_-24px_rgba(0,0,0,0.7)] [&_video]:object-contain"
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
          data-testid="cam-thumb-row"
          className="grid shrink-0 gap-1.5"
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
    <div data-testid="cam-strip" className="relative h-[92px] w-full shrink-0 overflow-hidden rounded-2xl border border-rule bg-surface">
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
    <div data-testid="cam-sync-row" className="flex shrink-0 items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1.5">
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

