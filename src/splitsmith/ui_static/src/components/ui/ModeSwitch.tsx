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
  const padX = size === "sm" ? "px-3.5" : "px-4";
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
              // Geist medium uppercase (NOT condensed Antonio) at 12px
              // for active + 11px for inactive. Pairs with the deeper
              // accent fill below so cream text reaches readable
              // contrast even under colorblindness simulation.
              "inline-flex items-center justify-center rounded-full font-sans font-semibold uppercase tracking-[0.08em] transition-colors",
              height,
              padX,
              active
                ? "text-[0.75rem] text-ink shadow-[0_0_12px_var(--color-accent-mode-glow)]"
                : "text-[0.6875rem] text-muted hover:text-ink",
            )}
            style={
              active
                ? {
                    // Deeper red (#DC2626) in match mode, deeper cyan
                    // (#0891B2) in dev mode, so cream text pops. We
                    // inline the per-mode value because there's no
                    // --color-accent-mode-fill token yet -- if it
                    // becomes a pattern we'll promote it.
                    background:
                      mode === "developer"
                        ? "#0891B2"
                        : "var(--color-led-fill)",
                  }
                : undefined
            }
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
