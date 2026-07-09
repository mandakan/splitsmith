/**
 * ShotRuler - horizontal shot-dot timeline, colored by gap tier (neutral
 * when unjudged), click/tap seeks. Read-only; shared by Coach per-stage
 * and Results.
 */
import type { CoachShot } from "@/lib/api";
import {
  INTERVAL_LABEL,
  TIER_NEUTRAL_COLOR,
  type TierBaselines,
  gapTier,
} from "@/lib/splits";
import { cn } from "@/lib/utils";

interface ShotRulerProps {
  shots: CoachShot[];
  minAbs: number;
  span: number;
  activeShotNumber: number | null;
  onSeek: (shot: CoachShot) => void;
  baselines: TierBaselines | null;
}

export function ShotRuler({
  shots,
  minAbs,
  span,
  activeShotNumber,
  onSeek,
  baselines,
}: ShotRulerProps) {
  return (
    <div className="overflow-hidden rounded-xl border border-rule-strong bg-surface px-6 py-5">
      <div className="relative h-5">
        <span
          aria-hidden
          className="absolute inset-y-1/2 left-0 right-0 h-px -translate-y-1/2 bg-rule"
        />
        {shots.map((shot) => {
          const x = ((shot.time_absolute - minAbs) / span) * 100;
          const tier = gapTier(shot.split, shot.interval_class, baselines);
          const active = activeShotNumber === shot.shot_number;
          return (
            <button
              key={shot.shot_number}
              type="button"
              onClick={() => onSeek(shot)}
              title={`Shot ${shot.shot_number} - ${shot.split.toFixed(3)}s${
                shot.interval_class ? ` - ${INTERVAL_LABEL[shot.interval_class]}` : ""
              }`}
              aria-label={`Shot ${shot.shot_number}`}
              className={cn(
                "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all",
                active
                  ? "size-4 ring-2 ring-led ring-offset-2 ring-offset-surface shadow-[0_0_8px_var(--color-led-glow)]"
                  : "size-3 hover:size-3.5",
              )}
              style={{ left: `${x}%`, backgroundColor: tier?.color ?? TIER_NEUTRAL_COLOR }}
            />
          );
        })}
      </div>
    </div>
  );
}
