/**
 * PipelineDots -- a stage's progress as a row of 8 px dots (spec
 * 2026-09-13 s6). done = green fill, progress = amber fill, todo = hollow.
 * Every dot also carries data-state so the meaning survives greyscale.
 * Replaces TickStrip's check glyphs on Matches, Overview rows and the
 * sidebar as those surfaces are rebuilt.
 */
import { cn } from "@/lib/utils";

export type PipelineState = "todo" | "progress" | "done";

const DOT: Record<PipelineState, string> = {
  done: "bg-done",
  progress: "bg-live",
  todo: "border-[1.5px] border-rule-strong bg-transparent",
};

export interface PipelineDotsProps {
  states: PipelineState[];
  /** The whole row's meaning, e.g. "4 of 12 stages audited". */
  label: string;
  className?: string;
}

export function PipelineDots({ states, label, className }: PipelineDotsProps) {
  return (
    <span role="img" aria-label={label} className={cn("inline-flex items-center gap-1", className)}>
      {states.map((s, i) => (
        <i key={i} data-state={s} aria-hidden className={cn("inline-block size-2 rounded-full", DOT[s])} />
      ))}
    </span>
  );
}
