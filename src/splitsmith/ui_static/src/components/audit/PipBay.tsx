import { Maximize2, Move, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

export type PipCorner = "tl" | "tr" | "bl" | "br";
export type PipSize = "S" | "M" | "L";

/** Per-cam tile dimensions. The bay's outer width grows with the cam
 *  count up to `MAX_TILES_VISIBLE` so each cam gets its own readable
 *  tile -- matching the design where two cams sit side-by-side. Beyond
 *  that cap, VideoPanel's internal grid squeezes the extras. */
const TILE: Record<PipSize, { width: number; height: number }> = {
  S: { width: 240, height: 150 },
  M: { width: 320, height: 200 },
  L: { width: 420, height: 264 },
};
const HEADER_HEIGHT = 28;
const MAX_TILES_VISIBLE = 2;

export function pipBayDims(size: PipSize, camCount: number) {
  const t = TILE[size];
  const tiles = Math.min(Math.max(camCount, 1), MAX_TILES_VISIBLE);
  return { width: t.width * tiles, height: t.height + HEADER_HEIGHT };
}

/** Padding from the viewport edge. Top corners are pushed down to clear
 *  the MatchShell sticky header (~64px tall + breathing). Bottom corners
 *  are pushed up to clear the StageActionBar (~64px) AND give the
 *  JobsPanel FAB its own bottom-right slot underneath. */
const CORNER_INSET = 12;
const TOP_HEADER_CLEAR = 76;
const BOTTOM_BAR_CLEAR = 80;

export interface PipBayProps {
  corner: PipCorner;
  size: PipSize;
  camCount: number;
  /** Number of cams whose buzzer needs operator attention -- shown as
   *  the header CTA "{n} NEEDS SYNC" when > 0. Click invokes
   *  `onNeedsSyncClick` (typically opens sync mode for the first one). */
  needsSyncCount?: number;
  onNeedsSyncClick?: () => void;
  onHide: () => void;
  onCycleSize: () => void;
  onCycleCorner: () => void;
  children: React.ReactNode;
}

export function PipBay({
  corner,
  size,
  camCount,
  needsSyncCount = 0,
  onNeedsSyncClick,
  onHide,
  onCycleSize,
  onCycleCorner,
  children,
}: PipBayProps) {
  const { width, height } = pipBayDims(size, camCount);

  // z-index sits above the MatchShell sticky header (z-50) and the
  // JobsPanel FAB (z-40) so the bay always reads as the top layer of
  // the audit page. The JobsPanel drawer is z-[55]; we stay below it
  // so opening the jobs drawer still covers the bay.
  const positionStyle: React.CSSProperties = {
    position: "fixed",
    width,
    height,
    zIndex: 52,
    ...(corner === "tl" && { top: TOP_HEADER_CLEAR, left: CORNER_INSET }),
    ...(corner === "tr" && { top: TOP_HEADER_CLEAR, right: CORNER_INSET }),
    ...(corner === "bl" && { bottom: BOTTOM_BAR_CLEAR, left: CORNER_INSET }),
    ...(corner === "br" && { bottom: BOTTOM_BAR_CLEAR, right: CORNER_INSET }),
  };

  return (
    <div
      role="region"
      aria-label={`Camera bay (${camCount} cam${camCount === 1 ? "" : "s"})`}
      style={positionStyle}
      className="flex flex-col overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[0_24px_48px_-16px_rgba(0,0,0,0.7),0_0_0_1px_var(--color-rule-strong)_inset,0_0_24px_rgba(255,45,45,0.18)]"
    >
      {/* Slim header: drag handle + cam count + chrome controls */}
      <div className="flex shrink-0 items-center gap-2 border-b border-rule bg-surface-2 px-3 py-1.5">
        <span
          aria-hidden
          className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)]"
        />
        <span className="flex-1 truncate font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-ink-2">
          {camCount} cam{camCount === 1 ? "" : "s"}
        </span>
        {needsSyncCount > 0 ? (
          <button
            type="button"
            onClick={onNeedsSyncClick}
            title={`${needsSyncCount} cam${needsSyncCount === 1 ? "" : "s"} need sync`}
            className="mr-1 inline-flex items-center gap-1 rounded-full border border-live/40 bg-live/10 px-2 py-0.5 font-display text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-live shadow-[0_0_8px_var(--color-live-glow)]"
          >
            <span
              aria-hidden
              className="inline-flex size-3 items-center justify-center rounded-full bg-live text-[0.5625rem] font-extrabold leading-none text-bg"
            >
              !
            </span>
            {needsSyncCount} need{needsSyncCount === 1 ? "s" : ""} sync
          </button>
        ) : null}
        <PipHeaderButton onClick={onCycleSize} title="Cycle size (⌥V)">
          <Maximize2 className="size-3" aria-hidden />
        </PipHeaderButton>
        <PipHeaderButton onClick={onCycleCorner} title="Snap to next corner (D)">
          <Move className="size-3" aria-hidden />
        </PipHeaderButton>
        <PipHeaderButton onClick={onHide} title="Hide (V)">
          <Minus className="size-3" aria-hidden />
        </PipHeaderButton>
      </div>

      {/* Video tile area -- VideoPanel renders into this. flex-1 so it
          claims all remaining vertical room; min-h-0 lets nested grids
          shrink instead of overflowing. */}
      <div className="relative min-h-0 flex-1 overflow-hidden">{children}</div>
    </div>
  );
}

interface PipHeaderButtonProps {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}

function PipHeaderButton({ onClick, title, children }: PipHeaderButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cn(
        "inline-flex size-5 items-center justify-center rounded-[3px] border border-transparent text-muted",
        "transition-colors hover:border-rule hover:bg-surface-3 hover:text-ink-2",
      )}
    >
      {children}
    </button>
  );
}

/** Pixel footprint the bay occupies on a given corner. Used by the page
 *  to inset the shot stepper so the notes input doesn't end up under the
 *  bay when it's anchored to a bottom corner. */
export function pipFootprintWidth(size: PipSize, camCount: number): number {
  return pipBayDims(size, camCount).width;
}
