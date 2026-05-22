import { forwardRef, useImperativeHandle, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export interface BeepStatusChipProps {
  /** Primary cam beep time on the audit timeline, in seconds. ``null`` when
   *  detection has not yet produced a beep. */
  beepTime: number | null;
  /** Detector confidence in [0, 1], or ``null`` if unknown (manual beep). */
  confidence: number | null;
  /** Optional explanation surfaced as a tooltip on the chip + Re-pick CTA.
   *  When confidence < 0.85, the heuristic in Audit.tsx fills this with the
   *  reason ("First shot lands 5.10s after the beep ..."). */
  diagnostic: string | null;
  /** Open sync mode for the primary cam so the operator can re-pick the
   *  buzzer on the waveform. */
  onRePick: () => void;
}

/** Imperative API on the chip. Surfaces a single ``flash()`` that other
 *  audit surfaces (e.g. PrereqGate's beep row) can call to draw the eye
 *  to the chip without owning a duplicate beep-state widget themselves.
 *
 *  Beep state lives in exactly one place -- this chip. Any other surface
 *  that wants to point at "the beep is wrong" should call ``flash()``
 *  here instead of rendering its own affordance. */
export interface BeepStatusChipHandle {
  flash: () => void;
}

/**
 * Confidence-aware beep status chip + Re-pick action.
 *
 * Three tones:
 *
 *   - **done** (cyan beep tint) -- confidence is null or >= 0.85, or detection
 *     hasn't surfaced a confidence number. Re-pick stays quiet.
 *   - **warn** (live amber) -- confidence < 0.85. The chip is amber, pulses
 *     once every 2.4s, carries the diagnostic in its tooltip, and the Re-pick
 *     button promotes to the tier-2 .btn-led-outline treatment so it reads
 *     as the next action.
 *   - **no_beep** (LED red) -- detection failed; no beep time. Re-pick is
 *     louder still; the chip reads "no beep -- pick manually".
 *
 * The chip owns beep state -- the diagnostic banner that used to live
 * above the toolbar is gone, and the PrereqGate's beep row no longer
 * renders its own Re-pick (it pings this chip via ``flash()`` instead).
 * One mental model, attached to its trigger.
 */
export const BeepStatusChip = forwardRef<BeepStatusChipHandle, BeepStatusChipProps>(
  function BeepStatusChip({ beepTime, confidence, diagnostic, onRePick }, ref) {
    type Tone = "done" | "warn" | "no_beep";
    let tone: Tone;
    let glyph: string;
    let suffix: string;
    let repickLoud: boolean;

    const lowConfidence = confidence != null && confidence < 0.85;
    if (beepTime == null) {
      tone = "no_beep";
      glyph = "×";
      suffix = " · pick manually";
      repickLoud = true;
    } else if (lowConfidence || diagnostic != null) {
      // Either the detector is uncertain *or* the post-audit heuristic
      // ("first shot lands 5.10s after the beep ...") thinks the beep is
      // on the wrong sound. Either way the operator needs to know.
      tone = "warn";
      glyph = "!";
      suffix = " · likely wrong";
      repickLoud = true;
    } else {
      tone = "done";
      glyph = "●";
      suffix = "";
      repickLoud = false;
    }

    // Flash state. PrereqGate's beep row (and any future surface that
    // wants to draw attention to the chip) calls flash() to add a
    // short-lived halo + scroll the chip into view. We let the animation
    // run to completion before clearing so reduced-motion users at least
    // see the scroll + scale-1 frame.
    const [flashing, setFlashing] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);
    const timerRef = useRef<number | null>(null);
    useImperativeHandle(
      ref,
      () => ({
        flash: () => {
          if (timerRef.current != null) window.clearTimeout(timerRef.current);
          setFlashing(true);
          containerRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "nearest",
            inline: "nearest",
          });
          timerRef.current = window.setTimeout(() => {
            setFlashing(false);
            timerRef.current = null;
          }, 1400);
        },
      }),
      [],
    );

    // The ``no_beep`` chip used to use ``text-led`` for its 11px body
    // text; per the post-b3531b5 colour-discipline rule, body-size red
    // text reads better as ``--color-led-text`` (lighter pink). Keeps
    // --color-led reserved for accents and large display.
    const chipClasses = {
      done: "border-beep/40 bg-beep-tint text-beep",
      warn: "animate-pulse border-live/50 bg-live-tint text-live shadow-[inset_0_0_0_1px_var(--color-live-glow),0_0_14px_var(--color-live-glow)]",
      no_beep: "border-led/50 bg-led-tint text-led-text",
    }[tone];

    const dotClasses = {
      done: "bg-beep text-bg",
      warn: "bg-live text-bg",
      no_beep: "bg-led text-bg",
    }[tone];

    const tip = diagnostic ?? undefined;

    return (
      <div
        ref={containerRef}
        // Bump the stacking so PrereqGate's flash isn't drawn under the
        // toolbar or canvas chrome. The base toolbar is z-index 0; the
        // sticky breadcrumb above is z-20. We sit at z-10 by default and
        // pop to z-30 during a flash so the halo is unambiguously on top.
        className={cn(
          "relative inline-flex items-center gap-1.5",
          flashing
            ? "z-30 rounded-full ring-2 ring-led shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-led)_22%,transparent),0_0_28px_color-mix(in_srgb,var(--color-led)_35%,transparent)] motion-safe:animate-[splitsmith-badge-pulse_0.7s_ease-in-out_2]"
            : "z-10",
        )}
      >
        <span
          title={tip}
          role={diagnostic ? "note" : undefined}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[0.6875rem] font-bold uppercase tracking-[0.06em]",
            diagnostic ? "cursor-help" : "cursor-default",
            chipClasses,
          )}
        >
          <span
            aria-hidden
            className={cn(
              "inline-flex size-3.5 items-center justify-center rounded-full font-mono text-[0.5625rem] font-extrabold leading-none",
              dotClasses,
            )}
          >
            {glyph}
          </span>
          <span>
            {beepTime == null ? (
              "no beep"
            ) : (
              <>
                beep at{" "}
                <span className="font-mono tabular-nums">
                  {beepTime.toFixed(3)}s
                </span>
              </>
            )}
            {suffix ? (
              <span className="font-semibold opacity-85">{suffix}</span>
            ) : null}
          </span>
        </span>
        <button
          type="button"
          onClick={onRePick}
          title={tip ?? "Re-pick the buzzer on the waveform"}
          className={cn(
            "inline-flex items-center gap-1.5 rounded transition-colors",
            repickLoud
              ? "btn-led-outline px-3 py-1"
              : "border border-rule bg-surface-2 px-2.5 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-rule-strong hover:bg-surface-3 hover:text-ink",
          )}
        >
          Re-pick beep
        </button>
      </div>
    );
  },
);
