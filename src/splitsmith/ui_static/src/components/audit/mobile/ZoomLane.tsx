/**
 * The placement surface: a fixed lane with the playhead pinned dead
 * centre and a dashed +/- TARGET_BAND_S band around it. The band is
 * fixed in time, so zoom changes how wide it looks but never which
 * marker it selects. Rejected candidates exist only in here, and only
 * inside the band - the band is the scoping that replaces an opt-in
 * candidates mode.
 */
import { useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { TARGET_BAND_S } from "@/lib/audit-target";
import { GRAB_PX } from "@/components/audit/mobile/WrappedWaveform";

export type ZoomFactor = 2 | 3 | 5;
const ZOOMS: ZoomFactor[] = [2, 3, 5];

export interface ZoomLaneProps {
  peaks: number[];
  duration: number;
  rows: number;
  playhead: number;
  zoom: ZoomFactor;
  onZoomChange(z: ZoomFactor): void;
  markers: AuditMarker[];
  targetId: string | null;
  onTap(time: number): void;
  onGrabStart(): void;
  onJog(time: number): void;
  onGrabEnd(): void;
}

const keptColor = (m: AuditMarker, isTarget: boolean): string => {
  if (isTarget) return "var(--color-status-warning)";
  return m.kind === "manual" ? "var(--color-marker-manual)" : "var(--color-marker-detected)";
};

export function ZoomLane({
  peaks,
  duration,
  rows,
  playhead,
  zoom,
  onZoomChange,
  markers,
  targetId,
  onTap,
  onGrabStart,
  onJog,
  onGrabEnd,
}: ZoomLaneProps) {
  const gesture = useRef<{ pointerId: number; startX: number; startPlayhead: number; grabbed: boolean } | null>(null);
  const windowS = duration > 0 ? duration / rows / zoom : 0;
  const winStart = playhead - windowS / 2;

  const toX = (t: number) => ((t - winStart) / windowS) * 1000;
  const timeAt = (el: Element, clientX: number): number => {
    const rect = el.getBoundingClientRect();
    const fx = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
    return winStart + fx * windowS;
  };

  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    // Ignore a second concurrent pointer while a gesture is active
    if (gesture.current != null) return;
    gesture.current = { pointerId: e.pointerId, startX: e.clientX, startPlayhead: playhead, grabbed: false };
    if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    const dx = e.clientX - g.startX;
    if (!g.grabbed && Math.abs(dx) < GRAB_PX) return;
    if (!g.grabbed) {
      g.grabbed = true;
      onGrabStart();
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const pxPerS = rect.width > 0 ? rect.width / windowS : 1;
    onJog(g.startPlayhead - dx / pxPerS);
  };
  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
    else onTap(timeAt(e.currentTarget, e.clientX));
  };
  const cancel = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
  };

  if (duration <= 0 || peaks.length === 0) return <div className="h-20" />;

  const binDur = duration / peaks.length;
  const firstBin = Math.max(0, Math.floor(winStart / binDur));
  const lastBin = Math.min(peaks.length, Math.ceil((winStart + windowS) / binDur));
  const inWindow = (t: number) => t >= winStart && t <= winStart + windowS;

  return (
    <div
      data-testid="zoom-lane"
      className="relative h-20 touch-none border-y border-rule"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
    >
      <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
        <rect
          data-testid="target-band"
          x={toX(playhead - TARGET_BAND_S)}
          width={toX(playhead + TARGET_BAND_S) - toX(playhead - TARGET_BAND_S)}
          y={2}
          height={96}
          fill="none"
          stroke="var(--color-status-warning)"
          strokeOpacity={0.55}
          strokeDasharray="6 4"
        />
        {Array.from({ length: lastBin - firstBin }, (_, i) => {
          const bin = firstBin + i;
          const h = Math.max(2, peaks[bin] * 92);
          return (
            <rect
              key={bin}
              x={toX(bin * binDur)}
              width={Math.max(1.2, 1000 / ((lastBin - firstBin) || 1) - 0.4)}
              y={50 - h / 2}
              height={h}
              fill="var(--color-waveform-bar)"
            />
          );
        })}
        {markers
          .filter((m) => m.kind !== "rejected" && inWindow(m.time))
          .map((m) => (
            <line
              key={m.id}
              data-marker-id={m.id}
              data-target={m.id === targetId ? "true" : undefined}
              x1={toX(m.time)}
              x2={toX(m.time)}
              y1={6}
              y2={94}
              stroke={keptColor(m, m.id === targetId)}
              strokeWidth={m.id === targetId ? 6 : 3.5}
            />
          ))}
        {markers
          .filter((m) => m.kind === "rejected" && Math.abs(m.time - playhead) <= TARGET_BAND_S)
          .map((m) => (
            <g
              key={m.id}
              data-marker-id={m.id}
              data-target={m.id === targetId ? "true" : undefined}
              opacity={0.25 + 0.6 * (m.confidence ?? 0.2)}
            >
              <line
                x1={toX(m.time)}
                x2={toX(m.time)}
                y1={40}
                y2={94}
                stroke="var(--color-marker-rejected)"
                strokeWidth={3}
              />
              <circle cx={toX(m.time)} cy={34} r={7} fill="var(--color-marker-rejected)" />
            </g>
          ))}
        <line
          data-testid="lane-playhead"
          x1={500}
          x2={500}
          y1={0}
          y2={100}
          stroke="var(--color-waveform-playhead)"
          strokeWidth={3}
        />
      </svg>
      <div className="absolute right-1 top-1 flex gap-1">
        {ZOOMS.map((z) => (
          <button
            key={z}
            type="button"
            aria-pressed={z === zoom}
            onClick={() => onZoomChange(z)}
            onPointerDown={(e) => e.stopPropagation()}
            className={`min-h-8 rounded px-2 font-mono text-xs ${
              z === zoom ? "btn-led-fill" : "border border-rule opacity-70"
            }`}
          >
            {z}x
          </button>
        ))}
      </div>
    </div>
  );
}
