/**
 * A shot card's heading line, on Breakdown's ShotIntervalCard (#1371): the
 * ordinal, its split and time from the beep,
 * and the tier chip (quick / typical / long, in the budget ticks).
 */
import { Chip, type ChipTick } from "@/components/ui/Chip";
import type { CoachShot } from "@/lib/api";
import type { GapTier } from "@/lib/splits";

const TIER_TICK: Record<GapTier["label"], ChipTick> = { quick: "fire", typical: "activation", long: "reload" };

export function ShotHeading({ shot, tier }: { shot: CoachShot; tier: GapTier | null }) {
  return (
    <div className="flex flex-wrap items-baseline gap-3">
      <span className="numeral text-2xl leading-none text-ink">{String(shot.shot_number).padStart(2, "0")}</span>
      <span className="numeral text-md text-ink-2">
        {shot.split.toFixed(3)} split &middot; {shot.time_from_beep.toFixed(2)} from beep
      </span>
      {tier ? (
        <Chip tick={TIER_TICK[tier.label]} className="ml-auto">
          {tier.label}
        </Chip>
      ) : null}
    </div>
  );
}
