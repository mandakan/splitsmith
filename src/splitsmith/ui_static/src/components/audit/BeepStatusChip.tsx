import { forwardRef, useImperativeHandle, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export interface BeepStatusChipProps {
  /** Primary cam beep time on the audit timeline, in seconds. ``null`` when
   *  detection has not yet produced a beep. */
  beepTime: number | null;
  /** Detector confidence in [0, 1], or ``null`` if unknown (manual beep). */
  confidence: number | null;
  /** Optional explanation surfaced as a tooltip on the chip. When
   *  confidence < 0.85, the heuristic in Audit.tsx fills this with the
   *  reason ("First shot lands 5.10s after the beep ..."). The diagnostic
   *  ALSO drives a banner sibling (BeepAnomalyBanner) with the
   *  "Review this beep" deep-link; this chip itself is read-only. */
  diagnostic: string | null;
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
 * Confidence-aware beep status chip (read-only).
 *
 * Three tones:
 *
 *   - **done** (cyan beep tint) -- confidence is null or >= 0.85, or detection
 *     hasn't surfaced a confidence number.
 *   - **warn** (live amber) -- confidence < 0.85. The chip is amber, pulses
 *     once every 2.4s, and carries the diagnostic in its tooltip. The amber
 *     ``BeepAnomalyBanner`` rendered below the toolbar is the action surface.
 *   - **no_beep** (LED red) -- detection failed; no beep time. Banner sibling
 *     deep-links the user into /beep-review to set the beep manually.
 *
 * The chip is read-only; beep work (confirm, re-pick, override) lives in
 * /beep-review (#396). Audit's role is to flag wrong beeps via the
 * diagnostic banner and deep-link into the queue for the fix.
 */
export const BeepStatusChip = forwardRef<BeepStatusChipHandle, BeepStatusChipProps>(
  function BeepStatusChip({ beepTime, confidence, diagnostic }, ref) {
    type Tone = "done" | "warn" | "no_beep";
    let tone: Tone;
    let glyph: string;
    let suffix: string;

    const lowConfidence = confidence != null && confidence < 0.85;
    if (beepTime == null) {
      tone = "no_beep";
      glyph = "×";
      suffix = " · review";
    } else if (lowConfidence || diagnostic != null) {
      // Either the detector is uncertain *or* the post-audit heuristic
      // ("first shot lands 5.10s after the beep ...") thinks the beep is
      // on the wrong sound. Either way the operator needs to know.
      tone = "warn";
      glyph = "!";
      suffix = " · likely wrong";
    } else {
      tone = "done";
      glyph = "●";
      suffix = "";
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
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[0.6875rem] font-bold uppercase tracking-[0.06em] cursor-default",
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
      </div>
    );
  },
);
