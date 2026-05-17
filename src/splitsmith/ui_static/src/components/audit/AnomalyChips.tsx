import type { Anomaly } from "@/lib/anomalies";
import { cn } from "@/lib/utils";

export interface AnomalyChipsProps {
  anomalies: Anomaly[];
  onJump: (anomaly: Anomaly) => void;
}

/**
 * Compact horizontal anomaly summary strip. Sits above the waveform.
 * Each chip click jumps the playhead to the offending shot when one is
 * attached (stage-level anomalies are non-interactive context).
 */
export function AnomalyChips({ anomalies, onJump }: AnomalyChipsProps) {
  if (anomalies.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
        Anomalies
      </span>
      {anomalies.map((a, i) => {
        const isWarn = a.severity === "warn";
        const clickable = a.time != null;
        return (
          <button
            key={`${a.kind}-${a.shot_number ?? "stage"}-${i}`}
            type="button"
            onClick={clickable ? () => onJump(a) : undefined}
            disabled={!clickable}
            title={a.message}
            className={cn(
              "inline-flex max-w-[22rem] items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[0.625rem] font-semibold",
              isWarn
                ? "border-live/40 bg-live/10 text-live"
                : "border-beep/40 bg-beep/10 text-beep",
              clickable ? "cursor-pointer" : "cursor-default opacity-75",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "inline-flex size-3.5 items-center justify-center rounded-full text-[0.5625rem] font-extrabold",
                isWarn ? "bg-live text-bg" : "bg-beep text-bg",
              )}
            >
              !
            </span>
            <span className="truncate">
              {a.shot_number != null ? `Shot ${a.shot_number} · ` : ""}
              {summariseMessage(a.message)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Anomaly messages embed numbers we already encode via the chip tone +
 *  badge; trim to the actionable clause so the chip stays scannable. */
function summariseMessage(message: string): string {
  if (message.includes(": ")) {
    return message.split(": ").slice(-1)[0];
  }
  if (message.includes(" -- ")) {
    return message.split(" -- ").slice(-1)[0];
  }
  return message;
}
