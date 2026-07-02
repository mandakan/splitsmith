import { ArrowRight, CheckCircle2, Loader2, Undo2 } from "lucide-react";

import { Kbd } from "@/components/ui/Kbd";
import type { ShooterListEntry, StageStatus } from "@/lib/api";
import type { AuditNextStep } from "@/lib/audit-next-step";
import { modKeyGlyph, modKeyLabel } from "@/lib/platform";
import { isTerminal } from "@/lib/stageStatus";
import { cn } from "@/lib/utils";

export interface StageActionBarStage {
  stageNumber: number;
  stageName: string;
  status: StageStatus;
}

export interface StageActionBarProps {
  shooters: ShooterListEntry[];
  activeSlug: string | undefined;
  activeStage: number | null;
  stages: StageActionBarStage[];
  step: AuditNextStep;
  dirty: boolean;
  saving: boolean;
  justSaved: boolean;
  canUndo: boolean;
  onSave: () => void;
  onUndo: () => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function shooterInitials(name: string): string {
  return name
    .split(" ")
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function StageActionBar({
  shooters,
  activeSlug,
  activeStage,
  stages,
  step,
  dirty,
  saving,
  justSaved,
  canUndo,
  onSave,
  onUndo,
}: StageActionBarProps) {
  const stageIdx = activeStage != null ? stages.findIndex((s) => s.stageNumber === activeStage) : -1;
  const shooterIdx = activeSlug != null ? shooters.findIndex((s) => s.slug === activeSlug) : -1;
  const shooter = shooterIdx >= 0 ? shooters[shooterIdx] : null;
  const stage = stageIdx >= 0 ? stages[stageIdx] : null;
  const isFinish = step.kind === "finish";
  const isReview = stage != null && isTerminal(stage.status);
  const mod = modKeyLabel();
  const modGlyph = modKeyGlyph();

  return (
    <div
      role="region"
      aria-label="Stage actions"
      // Spans the CONTENT column only (left offset = live sidebar width):
      // covering the sidebar hid its Jobs rail behind the bar.
      className="fixed bottom-0 left-[var(--shell-sidebar-w,0px)] right-0 z-chrome flex items-stretch gap-0 border-t border-rule-strong bg-bg/95 px-5 py-3 shadow-[0_-16px_32px_-16px_rgba(0,0,0,0.6)] backdrop-blur"
    >
      {/* Left -- progress block */}
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <div
            className={cn(
              "font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em]",
              isReview ? "text-done" : "text-subtle",
            )}
          >
            {stage != null && shooters.length > 0
              ? `Stage ${stageIdx + 1} of ${stages.length} · Shooter ${shooterIdx + 1} of ${shooters.length}`
              : stage != null
                ? `Stage ${stageIdx + 1} of ${stages.length}`
                : ""}
            {isReview ? <span className="ml-1.5">· Review</span> : null}
          </div>
          <div className="inline-flex items-center gap-2.5 overflow-hidden whitespace-nowrap font-display text-base font-bold uppercase tracking-[-0.01em] text-ink">
            {shooter != null && shooters.length > 1 ? (
              <>
                <span
                  aria-hidden
                  className="inline-flex size-[18px] items-center justify-center rounded-full bg-led font-mono text-[0.5625rem] font-extrabold text-bg"
                >
                  {shooterInitials(shooter.name)}
                </span>
                <span className="truncate">{shooter.name}</span>
                <span className="text-rule-strong">/</span>
              </>
            ) : null}
            {stage != null && activeStage != null ? (
              <span className="inline-flex min-w-0 items-center gap-2 truncate text-ink-2">
                <span className="tabular-nums">{pad2(activeStage)}</span>
                {stage.stageName ? (
                  <span className="truncate text-ink-2/80">{stage.stageName}</span>
                ) : null}
                {isTerminal(stage.status) ? (
                  <span aria-hidden className="text-done">·</span>
                ) : null}
              </span>
            ) : null}
          </div>
        </div>

        {stages.length > 0 ? (
          <div className="hidden items-center gap-[3px] sm:flex" aria-hidden>
            {stages.map((s) => {
              const isActive = s.stageNumber === activeStage;
              const isDone = isTerminal(s.status);
              return (
                <span
                  key={s.stageNumber}
                  className={cn(
                    "inline-block h-[5px] w-[22px] rounded-[1px]",
                    isActive
                      ? "bg-led shadow-[0_0_6px_var(--color-led-glow)]"
                      : isDone
                        ? "bg-done shadow-[0_0_4px_var(--color-done-glow)]"
                        : "bg-rule-strong opacity-60",
                  )}
                />
              );
            })}
          </div>
        ) : null}

        {dirty ? (
          <span className="inline-flex items-center gap-1.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-live">
            <span
              aria-hidden
              className="inline-block size-1.5 animate-pulse rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]"
            />
            Unsaved changes
          </span>
        ) : justSaved ? (
          <span className="inline-flex items-center gap-1.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-done">
            <CheckCircle2 className="size-3" aria-hidden />
            Saved
          </span>
        ) : null}
      </div>

      {/* Right -- actions */}
      <div className="flex items-center gap-2.5">
        <button
          type="button"
          onClick={onUndo}
          disabled={!canUndo}
          aria-label={`Undo (${mod}+Z)`}
          title={`Undo (${mod}+Z)`}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule bg-surface-2 px-3.5 py-2.5 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-rule-strong hover:bg-surface-3 hover:text-ink disabled:opacity-40"
        >
          <Undo2 className="size-3" aria-hidden />
          Undo
          <Kbd size="sm" className="ml-1">
            {modGlyph}Z
          </Kbd>
        </button>

        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          aria-label={step.label}
          title={`${step.label} (${mod}+Enter)`}
          className={cn(
            "inline-flex items-center gap-2.5 rounded-md border-0 py-2.5 pl-4 pr-4 font-display text-[0.8125rem] font-bold uppercase tracking-[0.08em]",
            "disabled:opacity-60",
            isFinish
              ? "bg-done text-bg shadow-[0_0_0_1px_var(--color-done),0_0_22px_var(--color-done-glow)]"
              : "bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_22px_var(--color-led-glow)]",
          )}
        >
          <span className="flex flex-col items-start gap-px">
            <span>{step.label}</span>
            <span className="font-mono text-[0.5625rem] font-semibold tracking-[0.02em] opacity-80">
              {step.sublabel}
            </span>
          </span>
          <span className="inline-flex items-center gap-1">
            <Kbd
              size="sm"
              className="border border-white/20 bg-black/25 text-inherit"
            >
              {modGlyph}↵
            </Kbd>
            {saving ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowRight className="size-3.5" aria-hidden strokeWidth={2.5} />
            )}
          </span>
        </button>
      </div>
    </div>
  );
}
