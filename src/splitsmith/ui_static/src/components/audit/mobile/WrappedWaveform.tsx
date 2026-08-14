/**
 * The stage's waveform wrapped into stacked rows like a text editor
 * wraps a long line: whole stage on one screen, playhead sweeping row
 * to row, nothing scrolls. Rejected candidates are deliberately absent
 * here - they surface only inside the zoom lane's target band.
 */
import { useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import type { LoopRegion } from "@/lib/useAuditPlayback";

export const DEFAULT_ROWS = 11;
export const GRAB_PX = 6;

export interface WrappedWaveformProps {
  peaks: number[];
  duration: number;
  rows?: number;
  playhead: number;
  markers: AuditMarker[];
  targetId: string | null;
  loop: LoopRegion | null;
  onTap(time: number): void;
  onGrabStart(): void;
  onScrub(time: number): void;
  onGrabEnd(): void;
}

function formatRowStart(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

const markerColor = (m: AuditMarker, isTarget: boolean): string => {
  if (isTarget) return "var(--color-status-warning)";
  return m.kind === "manual" ? "var(--color-marker-manual)" : "var(--color-marker-detected)";
};

export function WrappedWaveform({
  peaks,
  duration,
  rows = DEFAULT_ROWS,
  playhead,
  markers,
  targetId,
  loop,
  onTap,
  onGrabStart,
  onScrub,
  onGrabEnd,
}: WrappedWaveformProps) {
  const gesture = useRef<{ pointerId: number; startX: number; grabbed: boolean } | null>(null);
  const rowDur = duration > 0 ? duration / rows : 0;
  const binsPerRow = Math.ceil(peaks.length / rows);

  const timeAt = (row: number, el: Element, clientX: number): number => {
    const rect = el.getBoundingClientRect();
    const fx = rect.width > 0 ? Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)) : 0;
    return (row + fx) * rowDur;
  };

  const down = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    // Ignore new pointers if a gesture is already in progress with a different ID
    if (gesture.current != null && gesture.current.pointerId !== e.pointerId) return;
    gesture.current = { pointerId: e.pointerId, startX: e.clientX, grabbed: false };
    if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    if (!g.grabbed && Math.abs(e.clientX - g.startX) < GRAB_PX) return;
    if (!g.grabbed) {
      g.grabbed = true;
      onGrabStart();
    }
    onScrub(timeAt(row, e.currentTarget, e.clientX));
  };
  const up = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
    else onTap(timeAt(row, e.currentTarget, e.clientX));
  };
  const cancel = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
  };

  if (duration <= 0 || peaks.length === 0) return <div className="flex-1" />;
  const playRow = Math.min(rows - 1, Math.floor(playhead / rowDur));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-px" data-testid="wrapped-waveform">
      {Array.from({ length: rows }, (_, r) => {
        const rowStart = r * rowDur;
        const rowPeaks = peaks.slice(r * binsPerRow, (r + 1) * binsPerRow);
        const rowMarkers = markers.filter((m) => m.time >= rowStart && m.time < rowStart + rowDur);
        const loopIn =
          loop != null && loop.start < rowStart + rowDur && loop.end > rowStart ? loop : null;
        const toX = (t: number) => ((t - rowStart) / rowDur) * 1000;
        return (
          <div key={r} className="flex min-h-0 flex-1 items-stretch gap-1">
            <span className="w-8 shrink-0 self-center text-right font-mono text-[10px] text-muted">
              {formatRowStart(rowStart)}
            </span>
            <div
              data-testid="wave-row"
              className="relative min-w-0 flex-1 touch-none"
              onPointerDown={down(r)}
              onPointerMove={move(r)}
              onPointerUp={up(r)}
              onPointerCancel={cancel}
            >
              <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
                {loopIn != null && (
                  <rect
                    x={toX(Math.max(loopIn.start, rowStart))}
                    width={
                      toX(Math.min(loopIn.end, rowStart + rowDur)) -
                      toX(Math.max(loopIn.start, rowStart))
                    }
                    y={0}
                    height={100}
                    fill="var(--color-waveform-loop)"
                  />
                )}
                {rowPeaks.map((p, i) => {
                  const h = Math.max(2, p * 96);
                  return (
                    <rect
                      key={i}
                      x={(i / rowPeaks.length) * 1000}
                      width={Math.max(1, 1000 / rowPeaks.length - 0.4)}
                      y={50 - h / 2}
                      height={h}
                      fill="var(--color-waveform-bar)"
                    />
                  );
                })}
                {rowMarkers.map((m) => (
                  <line
                    key={m.id}
                    data-marker-id={m.id}
                    data-target={m.id === targetId ? "true" : undefined}
                    x1={toX(m.time)}
                    x2={toX(m.time)}
                    y1={4}
                    y2={96}
                    stroke={markerColor(m, m.id === targetId)}
                    strokeWidth={m.id === targetId ? 5 : 3}
                  />
                ))}
                {r === playRow && (
                  <line
                    x1={toX(playhead)}
                    x2={toX(playhead)}
                    y1={0}
                    y2={100}
                    stroke="var(--color-waveform-playhead)"
                    strokeWidth={3}
                  />
                )}
              </svg>
            </div>
          </div>
        );
      })}
    </div>
  );
}
