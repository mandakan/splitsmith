/**
 * ContextBar -- thin bar under the shell carrying breadcrumb + ambient
 * state. The mode-aware accent powers the leading pulse glyph so Match and
 * Developer surfaces feel distinct without needing different chrome.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

interface ContextBarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** The trailing slot is right-aligned (utility chips, stats, etc.) */
  trailing?: React.ReactNode;
}

export function ContextBar({
  trailing,
  children,
  className,
  ...props
}: ContextBarProps) {
  return (
    <div
      className={cn(
        "flex h-10 items-center justify-between gap-4 border-b border-rule bg-surface/60 px-6 backdrop-blur",
        className,
      )}
      {...props}
    >
      <div className="flex items-center gap-2 text-sm text-muted">
        <span
          aria-hidden
          className="inline-block size-1.5 rounded-full bg-[color:var(--color-accent-mode)] shadow-[0_0_8px_var(--color-accent-mode-glow)]"
        />
        {children}
      </div>
      {trailing && <div className="flex items-center gap-3">{trailing}</div>}
    </div>
  );
}

export function Breadcrumb({
  items,
  className,
}: {
  items: { label: string; href?: string }[];
  className?: string;
}) {
  return (
    <nav aria-label="Breadcrumb" className={cn("flex items-center gap-1.5 text-sm", className)}>
      {items.map((item, i) => {
        const last = i === items.length - 1;
        return (
          <React.Fragment key={i}>
            {item.href && !last ? (
              <a href={item.href} className="text-muted hover:text-ink">
                {item.label}
              </a>
            ) : (
              <span className={last ? "text-ink" : "text-muted"}>{item.label}</span>
            )}
            {!last && (
              <span aria-hidden className="text-whisper">
                /
              </span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
}
