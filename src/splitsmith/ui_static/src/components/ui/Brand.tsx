/**
 * Brand mark + wordmark. The chronograph crosshair glyph paired with the
 * Splitsmith wordmark in Antonio. Optional `serial` slot for a kicker line
 * (e.g. "Vol. 01 · Ed. 04") rendered in mono.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

interface BrandProps extends React.HTMLAttributes<HTMLDivElement> {
  /** `compact` is the mark alone; `bar` is the mark plus the wordmark at
   *  top-bar size (the share surface's bar; GlobalBar draws the same). */
  variant?: "default" | "compact" | "bar";
  serial?: React.ReactNode;
}

export function Brand({ variant = "default", serial, className, ...props }: BrandProps) {
  const compact = variant === "compact";
  if (variant === "bar") {
    return (
      <div className={cn("flex items-center gap-2.5", className)} {...props}>
        <BrandMark className="size-6" />
        <span className="font-display text-base font-bold uppercase tracking-tight text-ink">Splitsmith</span>
      </div>
    );
  }
  return (
    <div className={cn("flex items-center gap-3", className)} {...props}>
      <BrandMark className={compact ? "size-6" : "size-8"} />
      {!compact && (
        <div className="leading-none">
          <div className="font-display text-2xl font-bold uppercase tracking-tight text-ink">
            Splitsmith
          </div>
          {serial && (
            <div className="mt-1 font-mono text-xs uppercase tracking-[0.18em] text-muted">
              {serial}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden
      className={cn("text-led", className)}
      style={{ filter: "drop-shadow(0 0 6px var(--color-led-glow))" }}
    >
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="3.5" fill="currentColor" />
      <line x1="16" y1="1" x2="16" y2="6" stroke="currentColor" strokeWidth="1.5" />
      <line x1="16" y1="26" x2="16" y2="31" stroke="currentColor" strokeWidth="1.5" />
      <line x1="1" y1="16" x2="6" y2="16" stroke="currentColor" strokeWidth="1.5" />
      <line x1="26" y1="16" x2="31" y2="16" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
