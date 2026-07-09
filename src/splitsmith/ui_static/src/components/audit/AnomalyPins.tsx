import type { Anomaly } from "@/lib/anomalies";
import type { WaveformView } from "@/components/Waveform";
import { cn } from "@/lib/utils";

export interface AnomalyPinsProps {
  anomalies: Anomaly[];
  duration: number;
  onJump: (anomaly: Anomaly) => void;
  /** Scroll-host geometry from ``<Waveform onViewChange>``. Under zoom the
   *  waveform content is wider than the visible window and scrolls inside
   *  its own host; this overlay sits *outside* that host (it would be
   *  clipped by its overflow-y-hidden), so it needs the geometry to map
   *  time -> viewport-x. Null (pre-measure) falls back to fit-mode
   *  percentages, which are only correct at fit zoom. */
  view?: WaveformView | null;
}

/** Half the pin glyph width -- a pin whose center is within this margin of
 *  the visible window's edge is still partially visible, so keep it. */
const PIN_HALF_PX = 9;

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
 * The wrapper's left edge must align with the scroll host's left edge
 * (inset-x-4 vs the Waveform wrapper's px-4) so the pixel positions
 * computed from ``view`` line up with the bars. Pins scrolled out of the
 * visible window are dropped, mirroring the scroll host's own clipping.
 *
 * Stage-level anomalies (count band, no shots) have no `time` and are
 * filtered out here -- those still surface in the chip strip above the
 * waveform via <AnomalyChips>.
 */
export function AnomalyPins({ anomalies, duration, onJump, view }: AnomalyPinsProps) {
  if (duration <= 0) return null;
  const pinned = anomalies.filter((a) => a.time != null);
  if (pinned.length === 0) return null;
  return (
    <>
      {pinned.map((a, i) => {
        const isWarn = a.severity === "warn";
        let left: string;
        if (view && view.viewportWidth > 0) {
          const x = ((a.time as number) / duration) * view.contentWidth - view.scrollLeft;
          if (x < -PIN_HALF_PX || x > view.viewportWidth + PIN_HALF_PX) return null;
          left = `${x}px`;
        } else {
          left = `${((a.time as number) / duration) * 100}%`;
        }
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
            style={{ left, top: 0 }}
          >
            !
          </button>
        );
      })}
    </>
  );
}
