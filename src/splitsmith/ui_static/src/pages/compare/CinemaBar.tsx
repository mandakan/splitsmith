/**
 * The control bar in Compare's cinema mode: play, the stage and time, a
 * scrubber, mute all, the leaderboard toggle and the way out. It floats
 * over the bottom of the videos and fades with the rest of the chrome
 * while playback runs and the pointer rests.
 */
import { ListOrdered, Pause, Play, Volume2, VolumeX, X } from "lucide-react";

import { cn } from "@/lib/utils";

const ICON_BTN =
  "inline-flex size-9 flex-none items-center justify-center rounded-md border border-rule bg-surface-3 text-ink-2 transition-colors hover:bg-surface-4 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led";

export function CinemaBar({
  visible,
  stageLabel,
  timeSinceBeep,
  maxTime,
  isPlaying,
  onTogglePlay,
  onScrub,
  allMuted,
  onToggleAll,
  boardShown,
  onToggleBoard,
  onExit,
}: {
  visible: boolean;
  stageLabel: string;
  timeSinceBeep: number;
  maxTime: number;
  isPlaying: boolean;
  onTogglePlay: () => void;
  onScrub: (tsb: number) => void;
  allMuted: boolean;
  onToggleAll: () => void;
  boardShown: boolean;
  onToggleBoard: () => void;
  onExit: () => void;
}) {
  const t = Math.max(0, Math.min(maxTime, timeSinceBeep));
  return (
    <div
      data-testid="cinema-bar"
      className={cn(
        "absolute bottom-4 left-1/2 flex w-full max-w-3xl -translate-x-1/2 items-center gap-2.5 rounded-xl border border-rule-strong bg-surface/85 px-3 py-2 backdrop-blur transition-opacity duration-300",
        visible ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <button
        type="button"
        onClick={onTogglePlay}
        aria-label={isPlaying ? "Pause" : "Play"}
        className="inline-flex size-9 flex-none items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led)] transition-colors hover:bg-led-soft"
      >
        {isPlaying ? <Pause className="size-4" /> : <Play className="size-4" />}
      </button>
      <span className="numeral flex-none whitespace-nowrap text-sm text-ink-2">
        {stageLabel} &middot; {t.toFixed(2)} / {maxTime.toFixed(2)}s
      </span>
      <input
        type="range"
        aria-label="Scrub time since beep"
        className="min-w-24 flex-1 accent-led"
        min={0}
        max={maxTime}
        step={0.01}
        value={t}
        onChange={(e) => onScrub(parseFloat(e.target.value))}
      />
      <button
        type="button"
        onClick={onToggleAll}
        aria-label={allMuted ? "Unmute all" : "Mute all"}
        title={allMuted ? "Unmute all" : "Mute all"}
        className={ICON_BTN}
      >
        {allMuted ? (
          <VolumeX className="size-4" />
        ) : (
          <Volume2 className="size-4" />
        )}
      </button>
      <button
        type="button"
        onClick={onToggleBoard}
        aria-pressed={boardShown}
        aria-label={boardShown ? "Hide leaderboard" : "Show leaderboard"}
        title={`${boardShown ? "Hide" : "Show"} leaderboard (L)`}
        className={cn(ICON_BTN, boardShown && "text-ink")}
      >
        <ListOrdered className="size-4" />
      </button>
      <button
        type="button"
        onClick={onExit}
        aria-label="Exit cinema"
        title="Exit cinema (Esc)"
        className={ICON_BTN}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
