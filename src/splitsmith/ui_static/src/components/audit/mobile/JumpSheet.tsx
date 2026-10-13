/**
 * The phone Audit's shooter and stage picker: a bottom sheet opened from
 * the page title. Shooters across the top (the current one chosen), that
 * shooter's stages below; picking a stage opens its audit. The rows come
 * from ``lib/mobileAuditNav.jumpShooters``.
 */
import { useEffect, useRef, useState } from "react";

import { Portal } from "@/components/ui/Portal";
import type { StageStatus } from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import type { JumpShooter } from "@/lib/mobileAuditNav";
import { cn } from "@/lib/utils";

const STATUS_WORDS: Record<StageStatus, string> = {
  todo: "No footage",
  partial: "Needs beep",
  ready: "Not audited",
  in_progress: "In progress",
  audited: "Audited",
  skipped: "Skipped",
};

export const JUMP_TITLE = "Choose shooter and stage";

export function JumpSheet({
  open,
  shooters,
  currentStage,
  stageNames,
  onPick,
  onClose,
}: {
  open: boolean;
  shooters: JumpShooter[];
  currentStage: number | null;
  /** Stage names by number, from the current shooter's project. */
  stageNames: Record<number, string>;
  onPick(slug: string, stage: number): void;
  onClose(): void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(open, panelRef, onClose);
  const current = shooters.find((s) => s.current)?.slug ?? shooters[0]?.slug ?? null;
  const [chosen, setChosen] = useState<string | null>(current);
  useEffect(() => {
    if (open) setChosen(current);
  }, [open, current]);

  if (!open) return null;
  const shooter = shooters.find((s) => s.slug === chosen) ?? null;

  return (
    <Portal>
      <div className="fixed inset-0 z-modal flex items-end bg-bg/70" onClick={onClose}>
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={JUMP_TITLE}
          tabIndex={-1}
          className="flex max-h-[80dvh] w-full flex-col gap-3 rounded-t-xl border-t border-rule bg-surface p-4 pb-6 outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="text-md font-medium text-ink">{JUMP_TITLE}</div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Shooter">
            {shooters.map((s) => (
              <button
                key={s.slug}
                type="button"
                aria-pressed={s.slug === chosen}
                onClick={() => setChosen(s.slug)}
                className={cn(
                  "min-h-11 rounded-md border px-3 text-sm",
                  s.slug === chosen ? "border-ink text-ink" : "border-rule text-ink-2",
                )}
              >
                {s.name}
              </button>
            ))}
          </div>
          <ul className="flex min-h-0 flex-col overflow-y-auto border-t border-rule" aria-label="Stages">
            {(shooter?.stages ?? []).map((st) => {
              const here = shooter?.current && st.stageNumber === currentStage;
              return (
                <li key={st.stageNumber} className="border-b border-rule">
                  <button
                    type="button"
                    disabled={here || st.status === "todo"}
                    onClick={() => shooter && onPick(shooter.slug, st.stageNumber)}
                    className="flex min-h-11 w-full items-center gap-3 px-1 text-left disabled:opacity-60"
                  >
                    <span className={cn("numeral font-mono text-sm", here ? "text-led" : "text-ink-2")}>
                      Stage {String(st.stageNumber).padStart(2, "0")}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">
                      {shooter?.current ? (stageNames[st.stageNumber] ?? "") : ""}
                    </span>
                    <span className="text-sm text-muted">{here ? "Open now" : STATUS_WORDS[st.status]}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </Portal>
  );
}
