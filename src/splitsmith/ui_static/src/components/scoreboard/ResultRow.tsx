/**
 * ResultRow -- one scoreboard.urdr.dev search result (#598).
 *
 * Shared by CreateMatch's from-scoreboard search step and
 * ConnectMatchDialog's event picker. ``selected`` is optional visual
 * state (CreateMatch highlights the picked row while the operator
 * fills in the rest of the form below it); callers that commit
 * immediately on click can simply always pass ``selected={false}``.
 */
import { Check } from "lucide-react";

import type { ScoreboardMatchRef } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ResultRow({
  result,
  selected,
  onSelect,
}: {
  result: ScoreboardMatchRef;
  selected: boolean;
  onSelect: () => void;
}) {
  // Per the design system: LED red is an *accent* (dots, hairlines, halos,
  // focus rings), not a body-text color. Keep the match name in `text-ink`
  // in both states; communicate selection with the LED strip + filled
  // check + tinted row background so contrast stays high.
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "relative grid w-full items-center gap-4 border-b border-rule px-4 py-3.5 text-left transition-colors last:border-b-0 hover:bg-surface-2 focus-visible:outline-none focus-visible:bg-surface-2",
        selected && "bg-led-tint",
      )}
      style={{ gridTemplateColumns: "28px 1fr 110px 90px 24px" }}
    >
      {selected && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_8px_var(--color-led-glow)]"
        />
      )}
      <span
        aria-hidden
        className={cn(
          "inline-flex size-6 items-center justify-center rounded-md border transition-colors",
          selected
            ? "border-led bg-led-tint text-led"
            : "border-rule bg-surface-3 text-subtle",
        )}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M3 21l9-15 9 15H3z" />
        </svg>
      </span>
      <div>
        <div className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
          {result.name}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-2 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          {[result.venue, result.region, result.discipline]
            .filter(Boolean)
            .map((s, i, arr) => (
              <span key={i}>
                {s}
                {i < arr.length - 1 && <span className="ml-2 text-whisper">·</span>}
              </span>
            ))}
        </div>
      </div>
      <div className="text-right font-mono text-xs tabular-nums text-ink-2">
        {result.date ?? ""}
      </div>
      <div>
        {result.level && (
          <span
            className={cn(
              "inline-block rounded px-2 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em]",
              "border border-rule-strong bg-surface-3 text-ink-2",
            )}
          >
            {result.level}
          </span>
        )}
      </div>
      {selected ? (
        <span
          aria-label="selected"
          className="inline-flex size-5 items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)]"
        >
          <Check className="size-3" strokeWidth={3} />
        </span>
      ) : (
        <span />
      )}
    </button>
  );
}
