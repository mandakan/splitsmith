import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import { StageDot } from "@/components/ui/StageDot";
import type { StageEntry, StageStatus } from "@/lib/api";
import { deriveStageStatus } from "@/lib/stageStatus";

const COLLAPSE_KEY = "splitsmith:ingest:stage-reference:collapsed";

function readCollapsed(defaultCollapsed: boolean): boolean {
  if (typeof window === "undefined") return defaultCollapsed;
  const raw = window.localStorage.getItem(COLLAPSE_KEY);
  if (raw === "1") return true;
  if (raw === "0") return false;
  return defaultCollapsed;
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
 * StageReference -- the in-app copy of the SSI stage list, shown while
 * assigning videos so the operator never leaves the Ingest page. Read-only:
 * round count, paper/steel targets, assigned-video count, and lifecycle
 * status per stage. Sticky + collapsible; collapse state persists.
 */
export function StageReference({ stages }: { stages: StageEntry[] }) {
  const withFootage = stages.filter((s) => (s.videos?.length ?? 0) > 0).length;
  const remaining = stages.length - withFootage;
  const hasAnyRounds = stages.some((s) => s.stage_rounds?.expected != null);

  const [collapsed, setCollapsed] = useState(() =>
    readCollapsed(remaining === 0),
  );

  useEffect(() => {
    window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const toggle = useCallback(() => setCollapsed((c) => !c), []);

  if (stages.length === 0) return null;

  return (
    <div className="sticky top-0 z-10 border-b border-rule-strong bg-surface-2/95 backdrop-blur">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-3 px-5 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-led"
      >
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Stage reference
        </span>
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
          {withFootage} / {stages.length} have footage
          {remaining > 0 && <> &middot; {remaining} remaining</>}
        </span>
        <span aria-hidden className="ml-auto text-muted">
          {collapsed ? (
            <ChevronDown className="size-4" />
          ) : (
            <ChevronUp className="size-4" />
          )}
        </span>
      </button>

      {!collapsed && (
        <div className="max-h-[40vh] overflow-y-auto border-t border-rule">
          <table className="w-full border-collapse font-mono text-[0.6875rem]">
            <thead className="sticky top-0 bg-surface-2 text-muted">
              <tr className="text-left uppercase tracking-[0.08em]">
                <th className="px-5 py-1.5 font-medium">Stage</th>
                <th className="py-1.5 font-medium">Name</th>
                <th className="py-1.5 pr-4 text-right font-medium">Rounds</th>
                <th className="py-1.5 pr-4 font-medium">Targets</th>
                <th className="py-1.5 pr-4 text-right font-medium">Videos</th>
                <th className="px-5 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => {
                const status = s.status ?? deriveStageStatus(s);
                const count = s.videos?.length ?? 0;
                return (
                  <tr
                    key={s.stage_number}
                    className="border-t border-rule/60 text-ink-2 hover:bg-surface-3"
                  >
                    <td className="px-5 py-1.5 tabular-nums text-ink">
                      {String(s.stage_number).padStart(2, "0")}
                    </td>
                    <td className="py-1.5 pr-4 text-ink">{s.stage_name}</td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">
                      {roundsLabel(s)}
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums">
                      {targetsLabel(s)}
                    </td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">
                      {count === 0 ? (
                        <span className="text-muted">0</span>
                      ) : (
                        count
                      )}
                    </td>
                    <td className="px-5 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <StageDot status={status} />
                        <span className="uppercase tracking-[0.06em] text-muted">
                          {STATUS_LABEL[status]}
                        </span>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!hasAnyRounds && (
            <div className="border-t border-rule px-5 py-2 font-mono text-[0.625rem] text-muted">
              Round and target data unavailable -- re-sync this match from the
              scoreboard to populate it.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
