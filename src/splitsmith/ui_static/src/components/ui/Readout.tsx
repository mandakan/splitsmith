/**
 * Readout -- telemetry-style numeric cell. Label small-caps mono kicker over
 * a big mono value. Variants govern the tint of the value:
 *  - default: ink
 *  - live: amber
 *  - done: green
 *  - led: LED red (used for hero / leader readouts)
 *  - mode: follows the page accent
 */

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { Kicker } from "@/components/ui/Kicker";
import { cn } from "@/lib/utils";

const readoutValueVariants = cva("font-mono font-semibold tabular-nums leading-none", {
  variants: {
    tone: {
      default: "text-ink",
      live: "text-live",
      done: "text-done",
      led: "text-led",
      mode: "text-[color:var(--color-accent-mode)]",
      muted: "text-muted",
    },
    size: {
      sm: "text-lg",
      md: "text-2xl",
      lg: "text-4xl",
      xl: "text-5xl",
    },
  },
  defaultVariants: { tone: "default", size: "md" },
});

interface ReadoutProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof readoutValueVariants> {
  label: React.ReactNode;
  value: React.ReactNode;
  unit?: React.ReactNode;
  /** Optional second line under the value (delta, prior, etc.) */
  trailing?: React.ReactNode;
}

export function Readout({
  label,
  value,
  unit,
  trailing,
  tone,
  size,
  className,
  ...props
}: ReadoutProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)} {...props}>
      <Kicker tone="muted">{label}</Kicker>
      <div className="flex items-baseline gap-1.5">
        <span className={cn(readoutValueVariants({ tone, size }))}>{value}</span>
        {unit && (
          <span className="font-mono text-xs uppercase tracking-wider text-muted">
            {unit}
          </span>
        )}
      </div>
      {trailing && (
        <div className="font-mono text-xs text-muted tabular-nums">{trailing}</div>
      )}
    </div>
  );
}

export { readoutValueVariants };
