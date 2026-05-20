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

/**
 * Confidence-aware beep status chip + Re-pick action.
 *
 * Three tones:
 *
 *   - **done** (cyan beep tint) -- confidence is null or >= 0.85, or detection
 *     hasn't surfaced a confidence number. Re-pick stays quiet.
 *   - **warn** (live amber) -- confidence < 0.85. The chip is amber, pulses
 *     once every 2.4s, carries the diagnostic in its tooltip, and the Re-pick
 *     button promotes to a louder LED-tint border so it reads as the next
 *     action.
 *   - **no_beep** (LED red) -- detection failed; no beep time. Re-pick is
 *     louder still; the chip reads "no beep -- pick manually".
 *
 * The diagnostic text used to live in a separate banner above the toolbar.
 * Per the chat-locked design, the chip now owns it: tooltip on the chip,
 * tooltip on the Re-pick button. One mental model, attached to its trigger.
 */
export function BeepStatusChip({
  beepTime,
  confidence,
  diagnostic,
  onRePick,
}: BeepStatusChipProps) {
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

  const chipClasses = {
    done: "border-beep/40 bg-beep-tint text-beep",
    warn: "animate-pulse border-live/50 bg-live-tint text-live shadow-[inset_0_0_0_1px_var(--color-live-glow),0_0_14px_var(--color-live-glow)]",
    no_beep: "border-led/50 bg-led-tint text-led",
  }[tone];

  const dotClasses = {
    done: "bg-beep text-bg",
    warn: "bg-live text-bg",
    no_beep: "bg-led text-bg",
  }[tone];

  const tip = diagnostic ?? undefined;

  return (
    <div className="inline-flex items-center gap-1.5">
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
          "inline-flex items-center gap-1.5 rounded font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] transition-colors",
          repickLoud
            ? "border border-led/70 bg-led-tint px-3 py-1 text-led shadow-[0_0_12px_color-mix(in_srgb,var(--color-led)_22%,transparent)] hover:bg-led/20"
            : "border border-rule bg-surface-2 px-2.5 py-1 text-ink-2 hover:border-rule-strong hover:bg-surface-3 hover:text-ink",
        )}
      >
        Re-pick beep
      </button>
    </div>
  );
}
