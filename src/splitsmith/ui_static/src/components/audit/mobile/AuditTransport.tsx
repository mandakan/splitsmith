import { Pause, Play, Repeat } from "lucide-react";

import type { PlaybackSpeed } from "@/lib/useAuditPlayback";

const SPEEDS: PlaybackSpeed[] = [1, 0.5, 0.25];

export interface AuditTransportProps {
  playing: boolean;
  onPlayPause(): void;
  loopActive: boolean;
  onLoopToggle(): void;
  speed: PlaybackSpeed;
  onSpeedChange(s: PlaybackSpeed): void;
}

export function AuditTransport({
  playing,
  onPlayPause,
  loopActive,
  onLoopToggle,
  speed,
  onSpeedChange,
}: AuditTransportProps) {
  return (
    <div className="flex items-center gap-2 px-2 py-1">
      <button
        type="button"
        aria-label={playing ? "Pause" : "Play"}
        onClick={onPlayPause}
        className="btn-led-fill flex min-h-11 min-w-11 items-center justify-center rounded-md"
      >
        {playing ? <Pause className="size-5" aria-hidden /> : <Play className="size-5" aria-hidden />}
      </button>
      <button
        type="button"
        aria-label="Loop"
        aria-pressed={loopActive}
        onClick={onLoopToggle}
        className={`flex min-h-11 min-w-11 items-center justify-center rounded-md border border-rule ${
          loopActive ? "text-[var(--color-waveform-beep)]" : "opacity-70"
        }`}
      >
        <Repeat className="size-5" aria-hidden />
      </button>
      <div className="ml-auto flex gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={s === speed}
            onClick={() => onSpeedChange(s)}
            className={`min-h-11 rounded px-2 font-mono text-xs ${
              s === speed ? "btn-led-fill" : "border border-rule opacity-70"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>
    </div>
  );
}
