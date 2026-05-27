/**
 * StatusPill -- dot + label, instrument-panel aesthetic.
 *
 * Variants:
 *  - in-progress: amber, pulses (paused under prefers-reduced-motion via the
 *    global motion block; the static frame is still complete -- the dot is
 *    visible without animation)
 *  - exported: green, solid
 *  - archived: cold grey, hollow
 *  - awaiting: muted cool, hollow dot -- "not started yet, ready for input"
 *    (e.g. a freshly-created match with no footage attached, #425). Visually
 *    distinct from "archived" (stale) and the destructive LED red.
 *  - beep: cyan, used in Developer surfaces
 *
 * A11y: always carries a non-color cue (label text + a shape variation for
 * archived). Use the optional `icon` slot to add a lucide glyph when needed.
 */

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const pillVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.14em]",
  {
    variants: {
      tone: {
        "in-progress":
          "border-live/40 bg-[color:var(--color-live-glow)] text-live",
        exported: "border-done/40 bg-[color:var(--color-done-glow)] text-done",
        archived: "border-rule bg-transparent text-cold",
        awaiting: "border-rule-strong bg-surface-2 text-muted",
        beep: "border-beep/40 bg-[color:var(--color-beep-tint)] text-beep",
        led: "border-led/40 bg-[color:var(--color-led-tint)] text-led",
      },
    },
    defaultVariants: { tone: "archived" },
  },
);

interface StatusPillProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof pillVariants> {
  icon?: React.ReactNode;
}

export function StatusPill({
  tone,
  icon,
  children,
  className,
  ...props
}: StatusPillProps) {
  const pulse = tone === "in-progress";
  return (
    <span className={cn(pillVariants({ tone }), className)} {...props}>
      <span
        aria-hidden
        className={cn(
          "inline-block size-1.5 rounded-full",
          tone === "in-progress" && "bg-live",
          tone === "exported" && "bg-done",
          tone === "archived" && "border border-cold bg-transparent",
          tone === "awaiting" && "border border-muted bg-transparent",
          tone === "beep" && "bg-beep",
          tone === "led" && "bg-led",
          pulse && "animate-pulse",
        )}
      />
      {icon}
      <span>{children}</span>
    </span>
  );
}

export { pillVariants };
