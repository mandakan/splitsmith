/**
 * ShotTimerShell -- the redesign-era app chrome. Brand on the left, mode
 * switch in the center, utility + profile + heartbeat on the right, with a
 * thin mode-accent rule under the shell.
 *
 * The existing `components/AppShell.tsx` (production navigation chrome)
 * stays in place until surface issues #322/#323 migrate the picker and home
 * pages over to this new shell. This file is the foundational primitive
 * those surfaces will compose from.
 */

import * as React from "react";

import { Brand } from "@/components/ui/Brand";
import { ModeSwitch } from "@/components/ui/ModeSwitch";
import { cn } from "@/lib/utils";

interface ShotTimerShellProps {
  /** Right-aligned slot (utility chips, profile avatar, jobs, etc.) */
  utility?: React.ReactNode;
  /** Optional context bar rendered immediately below the shell. */
  context?: React.ReactNode;
  /** Optional brand serial line (Vol. 01 · Ed. 04 etc.) */
  serial?: React.ReactNode;
  /** Show the heartbeat indicator next to utility. */
  heartbeat?: boolean;
  children?: React.ReactNode;
  className?: string;
}

export function ShotTimerShell({
  utility,
  context,
  serial,
  heartbeat = true,
  children,
  className,
}: ShotTimerShellProps) {
  return (
    <div className={cn("min-h-screen bg-bg text-ink", className)}>
      <header className="relative">
        <div className="flex h-16 items-center justify-between gap-6 border-b border-rule bg-bg/80 px-6 backdrop-blur">
          <Brand serial={serial} />
          <ModeSwitch />
          <div className="flex items-center gap-3">
            {heartbeat && <Heartbeat />}
            {utility}
          </div>
        </div>
        {/* Mode-accent rule. Hairline under the shell, color flips with mode. */}
        <div
          aria-hidden
          className="h-px w-full bg-[color:var(--color-accent-mode)]"
          style={{ boxShadow: "0 0 12px var(--color-accent-mode-glow)" }}
        />
      </header>
      {context}
      <main>{children}</main>
    </div>
  );
}

function Heartbeat() {
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-xs uppercase tracking-[0.16em] text-muted">
      <span
        aria-hidden
        className="inline-block size-1.5 rounded-full bg-done shadow-[0_0_6px_var(--color-done-glow)] motion-safe:animate-pulse"
      />
      live
    </span>
  );
}
