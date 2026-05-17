import type { Anomaly } from "@/lib/anomalies";
import { cn } from "@/lib/utils";

export interface AnomalyPinsProps {
  anomalies: Anomaly[];
  duration: number;
  onJump: (anomaly: Anomaly) => void;
}

/**
 * Anomaly pins for the waveform timeline. Renders as an absolute overlay
 * with zero height anchored to the *top edge* of the bars wrapper, so
 * each pin (with `-translate-y-1/2`) straddles the border between the
 * legend header above and the waveform bars below -- exactly the design.
 *
 * Caller positions the overlay; this component just lays out the pin
 * buttons inside it. Expected wrapper:
 *
 *     <div className="pointer-events-none absolute inset-x-4 top-0 h-0 z-10">
 *       <AnomalyPins ... />
 *     </div>
 *
 * Stage-level anomalies (count band, no shots) have no `time` and are
 * filtered out here -- those still surface in the chip strip above the
 * waveform via <AnomalyChips>.
 *
 * Known limitation: at zoom > fit the Waveform content scrolls inside
 * its own scroll host; this overlay sits outside that host, so the pin
 * X positions drift from the bar positions when scrolled. The chip
 * strip above the waveform stays correct (it doesn't reference X).
 */
export function AnomalyPins({ anomalies, duration, onJump }: AnomalyPinsProps) {
  if (duration <= 0) return null;
  const pinned = anomalies.filter((a) => a.time != null);
  if (pinned.length === 0) return null;
  return (
    <>
      {pinned.map((a, i) => {
        const leftPct = ((a.time as number) / duration) * 100;
        const isWarn = a.severity === "warn";
        return (
          <button
            key={`${a.kind}-${a.shot_number ?? "stage"}-${i}`}
            type="button"
            onClick={() => onJump(a)}
            title={a.message}
            aria-label={a.message}
            className={cn(
              "pointer-events-auto absolute inline-flex size-[18px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-[1.5px] border-bg font-mono text-[0.6875rem] font-extrabold leading-none",
              isWarn
                ? "bg-live text-bg shadow-[0_0_10px_var(--color-live-glow)]"
                : "bg-beep text-bg shadow-[0_0_10px_var(--color-beep-glow)]",
            )}
            style={{ left: `${leftPct}%`, top: 0 }}
          >
            !
          </button>
        );
      })}
    </>
  );
}
