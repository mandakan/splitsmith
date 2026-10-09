/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
import type { Anomaly } from "@/lib/anomalies";
import type { WaveformView } from "@/components/Waveform";
import { cn } from "@/lib/utils";

export interface AnomalyPinsProps {
  anomalies: Anomaly[];
  duration: number;
  onJump: (anomaly: Anomaly) => void;
  /** Geometry that maps time -> x: ``x = time / duration * contentWidth -
   *  scrollLeft``, kept half a pin inside ``[0, viewportWidth]``. The
   *  timeline band's Flags row always passes ``viewportWidth =
   *  contentWidth`` and ``scrollLeft = 0`` (the row is the band's own
   *  content, which scrolls the row for it), so every pin is already
   *  inside the viewport and only the edge clamp below ever applies. Null
   *  falls back to percentages of the overlay's width. */
  view?: WaveformView | null;
}

/** Half the pin glyph width -- used to keep a pin half inside the row's
 *  edges (see ``view`` above). */
const PIN_HALF_PX = 9;

/**
 * Anomaly pins for the Audit timeline band's Flags row. Each pin is
 * centred (`-translate-x-1/2 -translate-y-1/2`) on its x and on the
 * overlay's top edge, so the caller places a zero-height overlay across
 * the row's middle:
 *
 *     <div className="pointer-events-none absolute inset-x-0 top-1/2 z-10 h-0">
 *       <AnomalyPins view={{ contentWidth, viewportWidth: contentWidth, scrollLeft: 0 }} ... />
 *     </div>
 *
 * The row lives inside the band's zoomed content, so a ``view`` as wide
 * as the content places each pin at its content x and the band's scroll
 * carries it; nothing here tracks scroll. Pins are kept half a pin inside
 * the row's edges. z-10 lets a pin's glow paint over the audio row.
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
          // Half a pin in from each edge: a pin at t=0 is not half clipped,
          // and one at t=duration does not overhang (and widen the scroll).
          const clamped = Math.min(Math.max(x, PIN_HALF_PX), view.viewportWidth - PIN_HALF_PX);
          left = `${clamped}px`;
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
