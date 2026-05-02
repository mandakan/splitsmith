/**
 * Bottom-bar stepper for the audit screen (#15).
 *
 * Displays the current shot's position in the kept-shot sequence, the split
 * from the previous shot, the detector's confidence (when the shot came from
 * detection), and a per-shot notes field. Stepping with the arrows or
 * `M` / `Shift+M` (handled by the parent) jumps the playhead to that shot.
 *
 * "Shots" = detected (kept) + manual markers, sorted by time. Rejected
 * markers don't show up here -- they live in the right-side drawer.
 */

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MarkerGlyph } from "@/components/MarkerGlyph";
import { cn } from "@/lib/utils";
import type { AuditMarker } from "@/components/MarkerLayer";

interface ShotStepperProps {
  shots: AuditMarker[];
  currentIndex: number;
  onStep: (delta: number) => void;
  onNoteChange: (markerId: string, note: string) => void;
  className?: string;
}

export function ShotStepper({
  shots,
  currentIndex,
  onStep,
  onNoteChange,
  className,
}: ShotStepperProps) {
  const total = shots.length;
  const current = total > 0 ? shots[Math.min(Math.max(currentIndex, 0), total - 1)] : null;
  const previous =
    current != null && currentIndex > 0 && currentIndex < total ? shots[currentIndex - 1] : null;
  const split = previous && current ? current.time - previous.time : null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm",
        className,
      )}
      role="group"
      aria-label="Shot stepper"
    >
      <Button
        size="sm"
        variant="outline"
        disabled={total === 0 || currentIndex <= 0}
        onClick={() => onStep(-1)}
        aria-label="Previous shot (Shift+M)"
        title="Previous shot (Shift+M)"
      >
        <ChevronLeft className="size-4" />
      </Button>
      <span className="flex items-center gap-1.5 font-mono tabular-nums">
        {current ? (
          <MarkerGlyph kind={current.kind} size={12} />
        ) : (
          <span className="inline-block size-3" aria-hidden />
        )}
        shot {total === 0 ? 0 : currentIndex + 1} / {total}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={total === 0 || currentIndex >= total - 1}
        onClick={() => onStep(1)}
        aria-label="Next shot (M)"
        title="Next shot (M)"
      >
        <ChevronRight className="size-4" />
      </Button>

      {current ? (
        <>
          <span
            className="font-mono tabular-nums text-muted-foreground"
            aria-label="Time on audit timeline"
          >
            t {current.time.toFixed(3)}s
          </span>
          {split != null ? (
            <span className="font-mono tabular-nums" title="Split from previous kept shot">
              split {split.toFixed(3)}s
            </span>
          ) : null}
          {current.kind === "detected" && current.confidence != null ? (
            <span
              className="font-mono tabular-nums text-muted-foreground"
              title="Detector confidence"
            >
              conf {current.confidence.toFixed(2)}
            </span>
          ) : null}
        </>
      ) : (
        <span className="text-muted-foreground">No shots yet</span>
      )}

      <input
        type="text"
        value={current?.note ?? ""}
        onChange={(e) => current && onNoteChange(current.id, e.target.value)}
        placeholder={current ? "Notes for this shot" : ""}
        disabled={current == null}
        className={cn(
          "ml-auto h-8 min-w-48 flex-1 rounded-md border border-input bg-background px-2 py-1 text-xs",
          "shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:opacity-50",
        )}
        aria-label="Per-shot notes"
      />
    </div>
  );
}
