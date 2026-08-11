/**
 * ReclassifySheet - the mobile interval-reclassify bottom sheet
 * (slice 5 of the mobile operator surfaces program). Wraps
 * MobileConfirmSheet with a radio chip-group of the six interval
 * classes plus the optional coaching note. Owns only draft state; the
 * caller owns the write (and should remount this keyed by shot number
 * so drafts never leak between shots).
 *
 * Selection is not color-only: the picked chip gets a ring, bold text
 * and aria-checked. Reload/activation are manual-only classes the auto
 * rule never assigns - offering them here is the point of the surface.
 */
import { useState } from "react";

import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import type { CoachIntervalClass, CoachShot, CoachShotPatch } from "@/lib/api";
import { buildCoachPatch } from "@/lib/coachPatch";
import { INTERVAL_LABEL, INTERVAL_TONE } from "@/lib/splits";
import { cn } from "@/lib/utils";

const CLASSES = Object.keys(INTERVAL_LABEL) as CoachIntervalClass[];

export function ReclassifySheet({
  shot,
  busy,
  onApply,
  onCancel,
}: {
  shot: CoachShot | null;
  busy: boolean;
  onApply: (shot: CoachShot, patch: CoachShotPatch) => void;
  onCancel: () => void;
}) {
  const [selected, setSelected] = useState<CoachIntervalClass | null>(
    shot?.interval_class ?? null,
  );
  const [note, setNote] = useState(shot?.coaching_note ?? "");

  if (!shot) return null;

  const apply = () => {
    if (busy) return;
    const patch = buildCoachPatch(shot, { intervalClass: selected, note });
    if (!patch) {
      onCancel();
      return;
    }
    onApply(shot, patch);
  };

  return (
    <MobileConfirmSheet
      open
      title={`Shot ${shot.shot_number} - ${shot.split.toFixed(3)}s`}
      confirmLabel="Apply"
      onConfirm={apply}
      onCancel={onCancel}
      body={
        <span className="block">
          <span role="radiogroup" aria-label="Interval class" className="mb-4 flex flex-wrap gap-2">
            {CLASSES.map((c) => {
              const picked = selected === c;
              return (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={picked}
                  onClick={() => setSelected(c)}
                  className={cn(
                    "min-h-11 rounded border px-3 font-mono text-xs uppercase focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
                    INTERVAL_TONE[c],
                    picked ? "font-bold ring-2 ring-led" : "opacity-70",
                  )}
                >
                  {INTERVAL_LABEL[c]}
                </button>
              );
            })}
          </span>
          <label className="block">
            <span className="mb-1 block text-xs uppercase tracking-[0.06em] text-muted">
              Coaching note (optional)
            </span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="w-full rounded border border-rule bg-surface-2 p-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
            />
          </label>
        </span>
      }
    />
  );
}
