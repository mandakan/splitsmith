/**
 * The selected region's card wired to the region hook, shared by Coach and
 * Breakdown (#1371). Kind changes, Keep and Delete commit through
 * ``useStageEvents.change`` exactly as the lane editor's own edits do; Done
 * and Delete drop the selection.
 */
import { EventCard } from "@/components/coach/EventCard";
import type { StageEvent } from "@/lib/api";
import { keepEvent, withKind } from "@/lib/events";
import type { StageEvents } from "@/lib/useStageEvents";

export function SelectedRegionCard({
  event,
  regions,
  compact = false,
}: {
  event: StageEvent;
  regions: StageEvents;
  compact?: boolean;
}) {
  const { events, change, select } = regions;
  return (
    <EventCard
      event={event}
      events={events}
      compact={compact}
      onKind={(kind) => {
        const next = withKind(events, event.id, kind);
        if (next) change(next, true);
      }}
      onKeep={() => change(keepEvent(events, event.id), true)}
      onDelete={() => {
        change(
          events.filter((e) => e.id !== event.id),
          true,
        );
        select(null);
      }}
      onDone={() => select(null)}
    />
  );
}
