/**
 * Chip -- neutral outline pill, Geist 12, with an optional 6 px tick that
 * carries a hue (spec 2026-09-13 s5: one hue per meaning, in one place per
 * row). Interval types use `tick`; states use `tone`. Never a coloured
 * fill -- the four filled interval chips on the shot rows were one of the
 * two colour systems the review found competing on every row.
 */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const chip = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] leading-[1.4]",
  {
    variants: {
      tone: {
        neutral: "border-rule-strong text-ink-2",
        warn: "border-live/45 text-live",
        ok: "border-done/45 text-done",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type ChipTick =
  | "draw"
  | "movement"
  | "transition"
  | "fire"
  | "reload"
  | "activation"
  | "muted";

const TICK: Record<ChipTick, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
  muted: "bg-muted",
};

export interface ChipProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof chip> {
  tick?: ChipTick;
}

export function Chip({ tick, tone, className, children, ...props }: ChipProps) {
  return (
    <span className={cn(chip({ tone }), className)} {...props}>
      {tick ? (
        <i data-tick aria-hidden className={cn("size-1.5 shrink-0 rounded-full", TICK[tick])} />
      ) : null}
      {children}
    </span>
  );
}
