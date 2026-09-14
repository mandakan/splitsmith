/**
 * Segmented -- one control for a small closed choice (spec 2026-09-13
 * s5: option cards become one row). Buttons with aria-pressed inside a
 * hairline track; the selected one lifts to surface-3. An option may
 * carry a tick hue and a disabled reason.
 */
import { cn } from "@/lib/utils";

import type { ChipTick } from "./Chip";

const TICK: Record<ChipTick, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
  muted: "bg-muted",
};

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  tick?: ChipTick;
  disabled?: boolean;
  /** Tooltip while disabled. */
  title?: string;
}

export interface SegmentedProps<T extends string> {
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
  className?: string;
}

export function Segmented<T extends string>({ value, options, onChange, label, disabled, className }: SegmentedProps<T>) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("inline-flex flex-wrap gap-0.5 rounded-md border border-rule-strong bg-surface-2 p-0.5", className)}
    >
      {options.map((opt) => {
        const on = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={on}
            disabled={disabled || opt.disabled}
            title={opt.disabled ? opt.title : undefined}
            onClick={() => onChange(opt.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
              on ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
            )}
          >
            {opt.tick ? <i aria-hidden className={cn("size-1.5 rounded-full", TICK[opt.tick])} /> : null}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
