/**
 * Segmented control that flips the global mode between Match and Developer.
 * Match uses LED red accent; Developer uses cyan. Implemented as a small
 * role="radiogroup" without an external dep -- two buttons is enough for two
 * mutually exclusive choices and avoids pulling in Radix Tabs.
 */

import * as React from "react";

import { useMode, type Mode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const OPTIONS: { value: Mode; label: string }[] = [
  { value: "match", label: "Match" },
  { value: "developer", label: "Developer" },
];

interface ModeSwitchProps extends React.HTMLAttributes<HTMLDivElement> {
  size?: "sm" | "md";
}

export function ModeSwitch({ size = "md", className, ...props }: ModeSwitchProps) {
  const { mode, setMode } = useMode();
  const padX = size === "sm" ? "px-3" : "px-4";
  const height = size === "sm" ? "h-7" : "h-9";

  return (
    <div
      role="radiogroup"
      aria-label="Mode"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full border border-rule bg-surface-2 p-0.5",
        className,
      )}
      {...props}
    >
      {OPTIONS.map((opt) => {
        const active = mode === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setMode(opt.value)}
            className={cn(
              "inline-flex items-center justify-center rounded-full font-display text-xs font-semibold uppercase tracking-[0.14em] transition-colors",
              height,
              padX,
              active
                ? "bg-[color:var(--color-accent-mode)] text-bg shadow-[0_0_12px_var(--color-accent-mode-glow)]"
                : "text-muted hover:text-ink",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
