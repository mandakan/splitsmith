/**
 * CurrentShotLine -- the one row about the current shot under the
 * transport (UX PR 5, spec s4.4): prev / next, ordinal, time, confidence,
 * split, the flag text when the shot is flagged, Reject and Add shot
 * here on the right, and the note field on a second line. Replaces
 * ShotStepper and the anomaly chip row on the Audit page.
 */
import { ChevronLeft, ChevronRight } from "lucide-react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";

export interface CurrentShotLineProps {
  shots: AuditMarker[];
  currentIndex: number;
  onStep: (delta: number) => void;
  flag: string | null;
  onNoteChange: (markerId: string, note: string) => void;
  onReject: () => void;
  onAddHere: () => void;
  /** False when the playhead already sits on a shot (K would toggle it off). */
  canAddHere: boolean;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <span className="numeral whitespace-nowrap text-muted">
      {label} <b className="font-medium text-ink">{value}</b>
    </span>
  );
}

export function CurrentShotLine({ shots, currentIndex, onStep, flag, onNoteChange, onReject, onAddHere, canAddHere }: CurrentShotLineProps) {
  const total = shots.length;
  const idx = total > 0 ? Math.min(Math.max(currentIndex, 0), total - 1) : -1;
  const current = idx >= 0 ? shots[idx] : null;
  const previous = idx > 0 ? shots[idx - 1] : null;
  const split = current ? current.time - (previous?.time ?? 0) : null;

  return (
    <div className="border-t border-rule-strong bg-surface-2 px-3 py-2 text-md text-ink-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="inline-flex gap-1">
          <Button type="button" size="sm" onClick={() => onStep(-1)} disabled={idx <= 0} aria-label="Previous shot">
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <Button type="button" size="sm" onClick={() => onStep(1)} disabled={idx < 0 || idx >= total - 1} aria-label="Next shot">
            <ChevronRight className="size-4" aria-hidden />
          </Button>
        </span>
        {current ? (
          <>
            <Figure label="shot" value={`${pad2(idx + 1)} / ${total}`} />
            <Figure label="t" value={current.time.toFixed(2)} />
            <Figure label="conf" value={current.confidence != null ? current.confidence.toFixed(2) : "—"} />
            <Figure label="split" value={split != null ? split.toFixed(3) : "—"} />
            {flag ? (
              <span className="text-live">
                <i aria-hidden className="mr-1.5 inline-block size-2 rounded-full bg-live align-middle" />
                {flag}
              </span>
            ) : null}
          </>
        ) : (
          <span className="text-muted">No shot at the playhead</span>
        )}
        <span className="ml-auto inline-flex gap-2">
          <Button type="button" size="sm" onClick={onReject} disabled={!current}>
            Reject <Kbd size="sm">R</Kbd>
          </Button>
          <Button type="button" size="sm" onClick={onAddHere} disabled={!canAddHere}>
            Add shot here <Kbd size="sm">K</Kbd>
          </Button>
        </span>
      </div>
      {current ? (
        <input
          type="text"
          value={current.note}
          onChange={(e) => onNoteChange(current.id, e.target.value)}
          placeholder="Notes for this shot"
          aria-label="Notes for this shot"
          className="mt-2 w-full rounded-md border border-rule bg-surface px-2.5 py-1 text-sm text-ink placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        />
      ) : null}
    </div>
  );
}
