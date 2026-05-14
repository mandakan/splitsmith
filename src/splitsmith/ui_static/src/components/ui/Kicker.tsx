/**
 * Kicker -- small-caps mono accent label. Defaults to the page accent (LED
 * red in Match mode, cyan in Developer mode) via the `--color-accent-mode`
 * variable from styles/index.css.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

interface KickerProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: "accent" | "muted" | "live" | "done";
}

const TONE_CLASS: Record<NonNullable<KickerProps["tone"]>, string> = {
  accent: "text-[color:var(--color-accent-mode)]",
  muted: "text-muted",
  live: "text-live",
  done: "text-done",
};

export function Kicker({
  tone = "accent",
  className,
  children,
  ...props
}: KickerProps) {
  return (
    <span
      className={cn(
        "font-mono text-xs uppercase tracking-[0.18em] tabular-nums",
        TONE_CLASS[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
