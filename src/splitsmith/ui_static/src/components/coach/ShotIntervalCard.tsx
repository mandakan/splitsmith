/**
 * The active shot on Breakdown (#1371): its ordinal, split, time from the
 * beep and tier, and the interval-class control. Notes and flags are
 * review metadata and stay on Coach (epic #1370); Breakdown edits the
 * class only.
 */
import { IntervalClassControl } from "@/components/coach/IntervalClassControl";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import type { CoachIntervalClass, CoachShot } from "@/lib/api";
import type { GapTier } from "@/lib/splits";

export function ShotIntervalCard({
  shot,
  tier,
  onClassify,
  disabled = false,
}: {
  shot: CoachShot;
  tier: GapTier | null;
  onClassify: (cls: CoachIntervalClass) => void;
  disabled?: boolean;
}) {
  return (
    <section aria-label={`Shot ${shot.shot_number}`} className="rounded-[10px] border border-rule bg-surface px-3.5 py-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="numeral text-2xl leading-none text-ink">{String(shot.shot_number).padStart(2, "0")}</span>
        <span className="numeral text-md text-ink-2">
          {shot.split.toFixed(3)} split &middot; {shot.time_from_beep.toFixed(2)} from beep
        </span>
        {tier ? (
          <Chip tick={tier.label === "quick" ? "fire" : tier.label === "long" ? "reload" : "activation"} className="ml-auto">
            {tier.label}
          </Chip>
        ) : null}
      </div>
      <Label className="mt-3 block">Interval</Label>
      <IntervalClassControl value={shot.interval_class} onClassify={onClassify} disabled={disabled} className="mt-1.5" />
    </section>
  );
}
