/**
 * CamGridModal -- fullscreen equal-grid view of all active cams.
 *
 * Triggered when the operator swaps MultiCamColumn's segmented control
 * to "Grid". The column remains the persistent control surface; the
 * actual side-by-side review happens in this modal so the audit canvas
 * stays clean while still offering an equal-attention multicam view.
 *
 * Layout:
 *   2 cams: 1x2 row.
 *   3 cams: 2x2 with one blank slot so labels stay aligned.
 *   4 cams: 2x2.
 *
 * Scrub is locked to the primary beep -- all cams share the playhead by
 * design. Clicking a tile promotes that cam to primary and returns to
 * focus mode.
 */

import { Pause, Play } from "lucide-react";
import { useRef, type ReactNode } from "react";

import { Portal } from "@/components/ui/Portal";
import type { StageVideo } from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";

export interface CamGridModalProps {
  videos: StageVideo[];
  primaryBeepTime: number | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  onTogglePlay: () => void;
  onClose: () => void;
  /** Promote a cam to primary (returns to focus mode after). */
  onPickFocus: (video: StageVideo) => void;
  /** Per-tile render slot -- the page owns the actual <video> elements
   *  and threads them in here. Receives the cam and its index. */
  renderTile: (video: StageVideo, index: number) => ReactNode;
}

export function CamGridModal({
  videos,
  primaryBeepTime,
  isPlaying,
  currentTime,
  duration,
  onTogglePlay,
  onClose,
  onPickFocus,
  renderTile,
}: CamGridModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const count = videos.length;
  const cols = count <= 2 ? count : 2;
  const rows = count <= 2 ? 1 : 2;
  const tiles: (StageVideo | null)[] = [...videos];
  while (tiles.length < cols * rows) tiles.push(null);

  const pct = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;

  return (
    <Portal>
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Camera grid"
      aria-modal="true"
      className="fixed inset-0 z-takeover flex flex-col"
      style={{
        background: "rgba(10,11,13,0.78)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-3.5 border-b border-rule-strong bg-[color-mix(in_srgb,var(--color-surface)_70%,transparent)] px-5 py-3.5">
        <span
          aria-hidden
          className="inline-flex size-[22px] items-center justify-center rounded border border-led/40 bg-led-tint text-led"
        >
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="3" y="3" width="7" height="7" rx="1.2"/>
            <rect x="14" y="3" width="7" height="7" rx="1.2"/>
            <rect x="3" y="14" width="7" height="7" rx="1.2"/>
            <rect x="14" y="14" width="7" height="7" rx="1.2"/>
          </svg>
        </span>
        <span className="font-display text-[0.875rem] font-bold uppercase tracking-[0.06em] text-ink">
          Camera grid
        </span>
        <span className="font-mono text-[0.6875rem] font-bold uppercase tracking-[0.1em] tabular-nums text-subtle">
          {count} cams · all synced
        </span>
        <span className="flex-1" />
        <span className="font-mono text-[0.8125rem] font-bold tabular-nums text-ink">
          {currentTime.toFixed(3)}
          <span className="text-subtle"> / {duration.toFixed(2)}s</span>
        </span>
        <button
          type="button"
          onClick={onClose}
          title="Return to focus (Esc)"
          className="inline-flex items-center gap-2 rounded-md border border-rule bg-surface-2 px-3 py-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-3"
        >
          Focus
          <span className="rounded border border-rule bg-surface-3 px-1.5 py-px font-mono text-[0.5625rem] font-bold text-muted">
            ESC
          </span>
        </button>
      </div>

      {/* Tiles */}
      <div
        className="grid min-h-0 flex-1 gap-3.5 p-5"
        style={{
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
        }}
      >
        {tiles.map((cam, i) =>
          cam ? (
            <CamGridTile
              key={cam.video_id}
              cam={cam}
              index={i}
              primaryBeepTime={primaryBeepTime}
              onPickFocus={() => onPickFocus(cam)}
            >
              {renderTile(cam, i)}
            </CamGridTile>
          ) : (
            <div
              key={`empty-${i}`}
              className="relative rounded-2xl border border-dashed border-rule-strong bg-[color-mix(in_srgb,var(--color-surface)_30%,transparent)]"
            />
          ),
        )}
      </div>

      {/* Shared transport footer */}
      <div className="flex items-center gap-3.5 border-t border-rule-strong bg-[color-mix(in_srgb,var(--color-surface)_70%,transparent)] px-5 py-3">
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label={isPlaying ? "Pause" : "Play"}
          title={isPlaying ? "Pause (Space)" : "Play (Space)"}
          className="inline-flex size-9 items-center justify-center rounded-full border-0 bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_14px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
        >
          {isPlaying ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
        </button>
        <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
          <span
            className="absolute inset-y-0 left-0 bg-led shadow-[0_0_6px_var(--color-led-glow)]"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.1em] text-subtle">
          Scrub locked to primary beep · click any tile to focus
        </span>
      </div>
    </div>
    </Portal>
  );
}

function CamGridTile({
  cam,
  index,
  primaryBeepTime,
  onPickFocus,
  children,
}: {
  cam: StageVideo;
  index: number;
  primaryBeepTime: number | null;
  onPickFocus: () => void;
  children: ReactNode;
}) {
  const delta =
    primaryBeepTime != null && cam.beep_time != null && index > 0
      ? cam.beep_time - primaryBeepTime
      : null;
  return (
    <button
      type="button"
      onClick={onPickFocus}
      title={index === 0 ? "Primary" : `Focus Cam ${index + 1}`}
      className={cn(
        "group relative overflow-hidden rounded-2xl border border-rule-strong bg-surface text-left transition-shadow",
        "hover:shadow-[0_0_0_1px_var(--color-led),0_0_24px_var(--color-led-glow)]",
      )}
    >
      <span className="absolute left-3.5 top-3 z-[2] inline-flex items-center gap-2 font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-led">
        <span
          aria-hidden
          className="inline-block size-2 rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
        />
        {index === 0 ? "Primary" : `Cam ${index + 1}`}
        <span className="font-display text-[0.6875rem] font-semibold tracking-[0.06em] text-ink-2">
          · {cam.role === "primary" ? "Primary" : "Secondary"}
        </span>
        {delta != null && Math.abs(delta) > 0.0005 ? (
          <span className="rounded-full border border-rule bg-bg/70 px-1.5 py-px font-mono text-[0.625rem] tabular-nums text-ink-2">
            Δ {(delta >= 0 ? "+" : "") + delta.toFixed(3)}s
          </span>
        ) : null}
      </span>
      <span className="absolute right-3.5 top-3 z-[2] inline-flex items-center gap-1.5 rounded-full border border-rule bg-bg/60 px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 opacity-0 transition-opacity group-hover:opacity-100">
        Click to focus
      </span>
      <div className="absolute inset-0">{children}</div>
    </button>
  );
}
