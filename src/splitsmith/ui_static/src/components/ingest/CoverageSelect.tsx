/**
 * Controlled multi-select of the match's stages for raw-video coverage
 * declaration (multi-stage takes). Chips show the stage number and name;
 * clicking toggles the stage in/out of the selection. Selection order is
 * preserved and shown as ordinal badges because declared order equals
 * shooting order for scoreboard-less sequential-mode projects.
 *
 * A "Use suggestion" affordance appears when ``suggested`` is non-empty
 * and differs from the current value.
 */

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface StageRef {
  stage_number: number;
  stage_name: string;
}

interface CoverageSelectProps {
  stages: StageRef[];
  value: number[];
  onChange: (v: number[]) => void;
  /** Server-computed coverage suggestion from span heuristics.
   *  Shown as a shortcut when non-empty and different from the current
   *  value. */
  suggested?: number[];
}

export function CoverageSelect({ stages, value, onChange, suggested }: CoverageSelectProps) {
  const hasSuggestion =
    suggested != null &&
    suggested.length > 0 &&
    !arraysEqual(suggested, value);

  function toggle(stageNumber: number) {
    if (value.includes(stageNumber)) {
      onChange(value.filter((n) => n !== stageNumber));
    } else {
      onChange([...value, stageNumber]);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent, stageNumber: number) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle(stageNumber);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {hasSuggestion && (
        <Button type="button" size="sm" onClick={() => onChange(suggested!)} className="self-start">
          Use suggestion &middot; stages {suggested!.join(", ")}
        </Button>
      )}
      <div role="group" aria-label="Stage coverage - click to toggle" className="flex flex-wrap gap-1.5">
        {stages.map((stage) => {
          const selected = value.includes(stage.stage_number);
          const order = value.indexOf(stage.stage_number);
          return (
            <button
              key={stage.stage_number}
              type="button"
              role="checkbox"
              aria-checked={selected}
              aria-label={
                selected
                  ? `Stage ${stage.stage_number} ${stage.stage_name} - position ${order + 1} in shooting order - click to remove`
                  : `Stage ${stage.stage_number} ${stage.stage_name} - click to add`
              }
              onClick={() => toggle(stage.stage_number)}
              onKeyDown={(e) => handleKeyDown(e, stage.stage_number)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
                selected ? "border-ink-2 text-ink" : "border-rule-strong text-muted hover:text-ink-2",
              )}
            >
              {selected ? (
                <span aria-hidden className="inline-grid size-4 shrink-0 place-items-center rounded-full bg-led text-xs text-ink">
                  {order + 1}
                </span>
              ) : null}
              <span>{String(stage.stage_number).padStart(2, "0")}</span>
              <span className="max-w-[96px] truncate font-sans text-sm">{stage.stage_name}</span>
            </button>
          );
        })}
      </div>
      {value.length > 0 && (
        <div className="text-sm text-muted">
          {value.length === 1 ? "1 stage" : `${value.length} stages`} selected; the order is the shooting order.
        </div>
      )}
    </div>
  );
}

function arraysEqual(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}
