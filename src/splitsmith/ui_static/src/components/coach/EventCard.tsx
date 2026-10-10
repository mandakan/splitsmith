/**
 * EventCard -- the selected region on the Coach stage page (spec
 * 2026-10-08). Replaces ShotEditor while a region is selected: one card
 * level per view. A kind whose lane the region would overlap is disabled
 * rather than written and refused. Keep confirms an auto proposal
 * (``source`` -> ``manual``, nothing else), which is what lets it reach
 * the rendered and exported outputs (confirmed regions only).
 */
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { Segmented, type SegmentedOption } from "@/components/ui/Segmented";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { enclosingMovement, reloadFigures, withKind } from "@/lib/events";
import { cn } from "@/lib/utils";

const KINDS: readonly { value: StageEventKind; label: string }[] = [
  { value: "movement", label: "Movement" },
  { value: "reload", label: "Reload" },
  { value: "activation", label: "Activation" },
];

export interface EventCardProps {
  event: StageEvent;
  events: StageEvent[];
  onKind: (kind: StageEventKind) => void;
  onKeep: () => void;
  onDelete: () => void;
  onDone: () => void;
  /** A short window (Breakdown, #1371): ~30 px rows, the kind control on
   *  the label's line, smaller figures, so the whole card fits beside the
   *  video. */
  compact?: boolean;
}

const f2 = (x: number) => x.toFixed(2);

export function EventCard({ event, events, onKind, onKeep, onDelete, onDone, compact = false }: EventCardProps) {
  const Row = compact ? CompactRow : FullRow;
  const Num = compact ? CompactNum : FullNum;
  const during = event.kind === "reload" ? enclosingMovement(event, events) : null;
  // The reload's time no movement covers: its whole duration standing.
  const exposed = event.kind === "reload" ? (reloadFigures(events).find((f) => f.eventId === event.id)?.exposed ?? null) : null;
  const options: SegmentedOption<StageEventKind>[] = KINDS.map((k) => {
    const blocked = k.value !== event.kind && withKind(events, event.id, k.value) === null;
    return { ...k, tick: k.value, disabled: blocked, title: blocked ? "Overlaps a region in that lane" : undefined };
  });
  return (
    <section
      aria-label="Region"
      className={cn("rounded-[10px] border border-rule bg-surface", compact ? "px-3 py-2" : "px-3.5 py-3")}
    >
      <div className={cn("flex items-center justify-between", compact ? "flex-nowrap gap-2" : "flex-wrap gap-3")}>
        <Label>Region</Label>
        <Segmented
          value={event.kind}
          options={options}
          onChange={onKind}
          label="Region kind"
          className={compact ? "flex-nowrap [&>button]:px-2" : undefined}
        />
      </div>
      <dl className={cn("divide-y divide-rule", compact ? "mt-1" : "mt-3")}>
        <Row k="Start">
          <Num>{f2(event.start)}</Num>
        </Row>
        <Row k="End">
          <Num>{f2(event.end)}</Num>
        </Row>
        <Row k="Duration">
          <Num>{f2(event.end - event.start)}</Num>
        </Row>
        {event.kind === "reload" ? (
          <Row k="During">
            {during ? (
              <Chip tick="movement">
                <span className="numeral">
                  Movement {f2(during.start)}&ndash;{f2(during.end)}
                </span>
              </Chip>
            ) : (
              <span className="text-md text-muted">Standing</span>
            )}
          </Row>
        ) : null}
        {exposed !== null ? (
          <Row k="Exposed">
            <Num>{f2(exposed)}</Num>
          </Row>
        ) : null}
        <Row k="Source">
          <Chip tick={event.source === "auto" ? "muted" : "neutral"}>{event.source === "auto" ? "Proposed" : "Manual"}</Chip>
        </Row>
      </dl>
      <div className={cn("flex items-center justify-end gap-2", compact ? "mt-1.5" : "mt-3")}>
        {event.source === "auto" ? (
          <Button size="sm" onClick={onKeep}>
            Keep
          </Button>
        ) : null}
        <Button size="sm" variant="destructive" onClick={onDelete}>
          Delete
        </Button>
        <Button size="sm" onClick={onDone}>
          Done
        </Button>
      </div>
    </section>
  );
}

function FullRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <dt className="text-md text-muted">{k}</dt>
      <dd className="m-0">{children}</dd>
    </div>
  );
}

function CompactRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex h-[30px] items-center justify-between gap-3">
      <dt className="text-md text-muted">{k}</dt>
      <dd className="m-0">{children}</dd>
    </div>
  );
}

function FullNum({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cn("numeral text-lg text-ink", className)}>{children}</span>;
}

function CompactNum({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cn("numeral text-md text-ink", className)}>{children}</span>;
}
