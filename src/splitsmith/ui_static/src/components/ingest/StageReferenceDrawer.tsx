import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { StageDot } from "@/components/ui/StageDot";
import type { StageEntry, StageStatus } from "@/lib/api";
import { deriveStageStatus } from "@/lib/stageStatus";
import { pad2 } from "@/pages/ingest/model";

const COLLAPSE_KEY = "splitsmith:ingest:stage-reference:collapsed";

function readCollapsed(defaultCollapsed: boolean): boolean {
  if (typeof window === "undefined") return defaultCollapsed;
  const raw = window.localStorage.getItem(COLLAPSE_KEY);
  if (raw === "1") return true;
  if (raw === "0") return false;
  return defaultCollapsed;
}

/** Collapse state for the stage drawer, persisted across reloads. */
// eslint-disable-next-line react-refresh/only-export-components
export function useStageDrawerCollapsed(
  defaultCollapsed: boolean,
): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(defaultCollapsed));
  useEffect(() => {
    window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  const toggle = useCallback(() => setCollapsed((c) => !c), []);
  return [collapsed, toggle];
}

function roundsLabel(stage: StageEntry): string {
  const n = stage.stage_rounds?.expected;
  return n == null ? "--" : `${n}rd`;
}

function targetsLabel(stage: StageEntry): string {
  const paper = stage.stage_rounds?.paper_targets ?? null;
  const steel = stage.stage_rounds?.steel_targets ?? null;
  const parts: string[] = [];
  if (paper != null) parts.push(`${paper}P`);
  if (steel != null) parts.push(`${steel}S`);
  return parts.length ? parts.join(" ") : "--";
}

const STATUS_LABEL: Record<StageStatus, string> = {
  todo: "todo",
  partial: "partial",
  ready: "ready",
  in_progress: "detecting",
  audited: "audited",
  skipped: "skipped",
};

/**
 * StageReferenceDrawer -- the in-app SSI stage list, as a right-edge column
 * that collapses to a thin handle. It sits BESIDE the player (never over it)
 * so the operator can read stage names while scrubbing. When a clip is
 * selected, each stage row becomes a one-click assign target.
 */
export function StageReferenceDrawer({
  stages,
  collapsed,
  onToggle,
  canAssign,
  onAssignStage,
}: {
  stages: StageEntry[];
  collapsed: boolean;
  onToggle: () => void;
  canAssign: boolean;
  onAssignStage: (stageNumber: number) => void;
}) {
  if (stages.length === 0) return null;

  const withFootage = stages.filter((s) => (s.videos?.length ?? 0) > 0).length;
  const remaining = stages.length - withFootage;
  const hasAnyRounds = stages.some((s) => s.stage_rounds?.expected != null);

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={false}
        aria-label="Show stage reference"
        title="Show stage reference"
        className="flex h-full w-7 flex-col items-center gap-2 rounded-lg border border-rule-strong bg-surface-2 py-3 text-muted transition-colors hover:border-ink-2 hover:text-ink"
      >
        <ChevronLeft className="size-4" />
        <span
          className="font-display text-[0.625rem] font-bold uppercase tracking-[0.14em]"
          style={{ writingMode: "vertical-rl" }}
        >
          Stages
        </span>
      </button>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface-2">
      <div className="flex items-center gap-2 border-b border-rule px-3 py-2.5">
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Stage reference
        </span>
        <span className="font-mono text-[0.625rem] tabular-nums text-muted">
          {withFootage}/{stages.length}
          {remaining > 0 && <> &middot; {remaining} left</>}
        </span>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Hide stage reference"
          title="Hide stage reference"
          className="ml-auto rounded p-0.5 text-muted transition-colors hover:text-ink"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>

      {canAssign && (
        <div className="border-b border-rule bg-led/[0.06] px-3 py-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-led">
          Click a stage to assign the selected clip
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ul className="divide-y divide-rule/60">
          {stages.map((s) => {
            const status = s.status ?? deriveStageStatus(s);
            const count = s.videos?.length ?? 0;
            const rowClass =
              "flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-[0.6875rem] text-ink-2";
            const inner = (
              <>
                <span className="w-6 shrink-0 tabular-nums text-ink">
                  {pad2(s.stage_number)}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">
                  {s.stage_name}
                </span>
                <span className="shrink-0 tabular-nums text-muted">
                  {roundsLabel(s)} {targetsLabel(s)}
                </span>
                <span className="w-6 shrink-0 text-right tabular-nums">
                  {count === 0 ? (
                    <span className="text-muted">0</span>
                  ) : (
                    <span className="text-ink">{count}</span>
                  )}
                </span>
                <StageDot status={status} />
                <span className="sr-only">{STATUS_LABEL[status]}</span>
              </>
            );
            return (
              <li key={s.stage_number}>
                {canAssign ? (
                  <button
                    type="button"
                    onClick={() => onAssignStage(s.stage_number)}
                    className={`${rowClass} transition-colors hover:bg-led-tint hover:text-led`}
                  >
                    {inner}
                  </button>
                ) : (
                  <div className={rowClass}>{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
        {!hasAnyRounds && (
          <div className="border-t border-rule px-3 py-2 font-mono text-[0.625rem] text-muted">
            Round and target data unavailable -- re-sync this match from the
            scoreboard to populate it.
          </div>
        )}
      </div>
    </div>
  );
}
