/**
 * Keyboard shortcut chip. Mono, uppercase, instrument-panel aesthetic.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

interface KbdProps extends React.HTMLAttributes<HTMLElement> {
  size?: "sm" | "md";
}

export function Kbd({ size = "sm", className, children, ...props }: KbdProps) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center justify-center rounded-sm border border-rule bg-surface-3 px-1.5 font-mono uppercase tracking-wide text-ink-2",
        size === "sm" ? "h-5 min-w-5 text-[0.625rem]" : "h-6 min-w-6 text-xs",
        className,
      )}
      {...props}
    >
      {children}
    </kbd>
  );
}
