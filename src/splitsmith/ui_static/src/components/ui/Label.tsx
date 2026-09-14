/**
 * Label -- the one tracked-caps style in the app (spec 2026-09-13 s5, type
 * ladder). Mono 500 at 11 px, 0.08 em, uppercase. One to three words:
 * table headers, stat labels, section names. Never a sentence -- that is
 * Meta (Geist 12) or Body. Replaces Kicker, which allowed any length and
 * a 0.18 em tracking that made sentences unreadable.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export interface LabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: "muted" | "accent" | "live" | "done" | "ink" | "subtle";
}

const TONE: Record<NonNullable<LabelProps["tone"]>, string> = {
  muted: "text-muted",
  accent: "text-[color:var(--color-accent-mode)]",
  live: "text-live",
  done: "text-done",
  ink: "text-ink-2",
  subtle: "text-subtle",
};

const warned = new Set<string>();

export function Label({ tone = "muted", className, children, ...props }: LabelProps) {
  if (import.meta.env.DEV && typeof children === "string") {
    const words = children.trim().split(/\s+/).length;
    if (words > 3 && !warned.has(children)) {
      warned.add(children);
      console.warn(
        `Label: "${children}" is ${words} words; Label carries at most three words. Use Meta or Body.`,
      );
    }
  }
  return (
    <span
      className={cn(
        "font-mono text-[11px] font-medium uppercase tracking-[0.08em] tabular-nums",
        TONE[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
