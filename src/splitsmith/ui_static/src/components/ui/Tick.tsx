/**
 * Tick -- a single tick mark used in stage-progress strips.
 *
 * States:
 *  - todo: empty rule (greyscale)
 *  - done: filled with the accent (carries a check shape for non-color cue)
 *  - flagged: amber with a notch on top
 *  - current: ink-filled with a caret above
 *
 * TickStrip wraps an array of states with an aria-label that summarises the
 * row (e.g. "12 of 16 stages complete, 1 flagged").
 */

import { cn } from "@/lib/utils";

export type TickState = "todo" | "done" | "flagged" | "current";

interface TickProps {
  state: TickState;
  label?: string;
}

export function Tick({ state, label }: TickProps) {
  return (
    <span
      role="img"
      aria-label={label ?? state}
      className={cn("relative inline-block h-3 w-2 rounded-[2px]", {
        "bg-transparent border border-rule-strong": state === "todo",
        "bg-[color:var(--color-accent-mode)]": state === "done",
        "bg-live": state === "flagged",
        "bg-ink": state === "current",
      })}
    >
      {state === "flagged" && (
        <span
          aria-hidden
          className="absolute -top-1 left-1/2 size-1 -translate-x-1/2 rounded-[1px] bg-live"
        />
      )}
      {state === "current" && (
        <span
          aria-hidden
          className="absolute -top-1.5 left-1/2 -translate-x-1/2 border-x-[3px] border-b-[4px] border-x-transparent border-b-ink"
        />
      )}
      {state === "done" && (
        <span
          aria-hidden
          className="absolute inset-0 flex items-center justify-center text-[8px] font-bold leading-none text-bg"
        >
          ✓
        </span>
      )}
    </span>
  );
}

interface TickStripProps {
  states: TickState[];
  ariaLabel: string;
  className?: string;
}

export function TickStrip({ states, ariaLabel, className }: TickStripProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn("inline-flex items-end gap-1", className)}
    >
      {/*
        Per-tick label is POSITIONAL, never "Stage <n>". `states` is a
        progress array -- Pick.tsx builds it from stage_count +
        stages_audited and it carries no stage identity at all. Stage
        numbers stopped being 1..N when the stage-list editor shipped
        (#521): removing stage 3 from six leaves 1, 2, 4, 5, 6, so
        `i + 1` named the wrong stage to every screen reader from tick 3
        on. The group's ariaLabel carries the summary.
      */}
      {states.map((s, i) => (
        <Tick key={i} state={s} label={`${i + 1} of ${states.length}: ${s}`} />
      ))}
    </div>
  );
}
