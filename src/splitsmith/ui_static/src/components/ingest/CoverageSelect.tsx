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
        <div className="flex items-center gap-2">
          <span className="font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-muted">
            Suggested:
          </span>
          <button
            type="button"
            onClick={() => onChange(suggested!)}
            className="rounded border border-done/40 bg-done/10 px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-done hover:bg-done/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-done/60"
          >
            Use suggestion - stages {suggested!.join(", ")}
          </button>
        </div>
      )}
      <div
        role="group"
        aria-label="Stage coverage - click to toggle"
        className="flex flex-wrap gap-1.5"
      >
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
                "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 font-mono text-[0.625rem] font-bold tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-1",
                selected
                  ? "border-led-deep bg-led-tint text-led-text focus-visible:ring-led"
                  : "border-rule bg-surface-2 text-subtle hover:border-rule-strong hover:text-ink-2 focus-visible:border-led-deep focus-visible:ring-led/50",
              )}
            >
              {selected && (
                <span
                  aria-hidden
                  className="inline-flex size-[14px] shrink-0 items-center justify-center rounded-full bg-led text-[0.4375rem] font-bold text-ink"
                >
                  {order + 1}
                </span>
              )}
              <span>S{String(stage.stage_number).padStart(2, "0")}</span>
              <span className="max-w-[96px] truncate text-[0.5625rem] uppercase tracking-[0.06em] opacity-70">
                {stage.stage_name}
              </span>
            </button>
          );
        })}
      </div>
      {value.length > 0 && (
        <div className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
          {value.length === 1 ? "1 stage" : `${value.length} stages`} selected
          {" "}- order = shooting order
        </div>
      )}
    </div>
  );
}

function arraysEqual(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}
