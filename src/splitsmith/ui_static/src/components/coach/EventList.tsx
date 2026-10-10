/**
 * EventList -- the read-only companion to the lanes (phone, or a match
 * mirrored from a desktop): one hairline row per region in time order. A
 * reload reads as its duration and exposed time, a movement or activation as
 * its range, a movement with the shots fired inside it.
 */
import { Chip } from "@/components/ui/Chip";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { reloadFigures } from "@/lib/events";

export interface EventListProps {
  events: StageEvent[];
  shots: { time_from_beep: number }[];
}

const f2 = (x: number) => x.toFixed(2);
const NAME: Record<StageEventKind, string> = { movement: "Movement", reload: "Reload", activation: "Activation" };

export function EventList({ events, shots }: EventListProps) {
  if (events.length === 0) return null;
  const figs = new Map(reloadFigures(events).map((f) => [f.eventId, f]));
  const sorted = [...events].sort((a, b) => a.start - b.start);
  return (
    <ul aria-label="Regions" className="divide-y divide-rule">
      {sorted.map((e) => {
        const fig = figs.get(e.id);
        const inside =
          e.kind === "movement" ? shots.filter((s) => e.start <= s.time_from_beep && s.time_from_beep <= e.end).length : 0;
        return (
          <li key={e.id} className="flex items-baseline justify-between gap-3 py-2">
            <Chip tick={e.kind}>{NAME[e.kind]}</Chip>
            <span className="numeral text-md text-ink-2">
              {fig ? (
                <>
                  {f2(fig.duration)}
                  {" · "}
                  <span className="text-ink">{f2(fig.exposed)} exposed</span>
                </>
              ) : (
                <>
                  {f2(e.start)}&ndash;{f2(e.end)}
                  {inside > 0 ? ` · ${inside} ${inside === 1 ? "shot" : "shots"}` : null}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
