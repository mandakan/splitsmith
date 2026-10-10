/**
 * Breakdown's inspector (#1372, epic #1370): what is selected, beside the
 * video.
 *
 * - A region: its card, the compact one at every height. The active shot
 *   folds to one summary line in the header row (a click opens it,
 *   dropping the region), and the shot list gets what height is left,
 *   down to its header.
 * - Otherwise the active shot: its interval class, the shot list under it.
 * - The shot list's section folds to its header and scrolls inside itself.
 * - The whole inspector folds to a rail that names the selection, giving
 *   the viewer the width.
 *
 * Both folds are per browser and shared by every stage (lib/breakdownPrefs).
 */
import { AlertTriangle, ChevronDown, PanelRightClose, PanelRightOpen } from "lucide-react";
import type { Ref } from "react";

import { CoachShotTable } from "@/components/coach/CoachShotTable";
import { EventList } from "@/components/coach/EventList";
import { SaveNotice } from "@/components/coach/SaveNotice";
import { SelectedRegionCard } from "@/components/coach/SelectedRegionCard";
import { ShotIntervalCard } from "@/components/coach/ShotIntervalCard";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import type { CoachShot, StageEvent } from "@/lib/api";
import { useInspectorFolded, useShotsFolded } from "@/lib/breakdownPrefs";
import { gapTier } from "@/lib/splits";
import { BUDGET_LABEL, BUDGET_TICK } from "@/lib/timeBudget";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<StageEvent["kind"], string> = { movement: "Movement", reload: "Reload", activation: "Activation" };
// The lanes' hues (LaneEditor), so the rail's tick reads as the lane it came from.
const KIND_TICK: Record<StageEvent["kind"], string> = { movement: "bg-beep", reload: "bg-live", activation: "bg-ink-2" };

const pad2 = (n: number) => String(n).padStart(2, "0");

export interface BreakdownInspectorProps {
  ws: StageWorkspace;
  view: StageView;
  /** A short window: a shorter floor for the shot list. */
  compact: boolean;
  /** The scrolling column, for the page's scroll-to-top on a new selection. */
  scrollRef?: Ref<HTMLElement>;
}

export function BreakdownInspector({ ws, view, compact, scrollRef }: BreakdownInspectorProps) {
  const [folded, setFolded] = useInspectorFolded();
  const [shotsFolded, setShotsFolded] = useShotsFolded();
  const { coach, regions, baselines } = ws;
  const { activeShot, selectedEvent, eventsReadOnly } = view;
  if (!coach) return null;

  if (folded) {
    const what = selectedEvent ? KIND_LABEL[selectedEvent.kind] : activeShot ? `Shot ${pad2(activeShot.shot_number)}` : null;
    return (
      <aside aria-label="Inspector" data-folded="true" className="flex min-h-0 flex-col items-center gap-3 py-2">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          aria-label="Unfold inspector"
          aria-expanded={false}
          onClick={() => setFolded(false)}
        >
          <PanelRightOpen className="size-4" aria-hidden />
        </Button>
        {regions.issue ? <AlertTriangle aria-label="A region did not save" className="size-4 text-destructive" /> : null}
        {what ? (
          <div data-testid="inspector-rail-selection" className="flex flex-col items-center gap-2">
            <i
              aria-hidden
              className={cn("size-1.5 rounded-full", selectedEvent ? KIND_TICK[selectedEvent.kind] : "bg-muted")}
            />
            <Label tone="ink" className="[writing-mode:vertical-rl]">
              {what}
            </Label>
          </div>
        ) : null}
      </aside>
    );
  }

  const selectShot = (shot: CoachShot) => ws.seekToShot(shot);
  return (
    <aside
      ref={scrollRef}
      aria-label="Inspector"
      // Scrolls only when the card and the folded rows do not all fit; the
      // card leads, so its actions are the last thing to leave the view.
      className="flex min-h-0 flex-col gap-1.5 overflow-y-auto px-3 py-1.5"
    >
      <div className="flex h-7 shrink-0 items-center gap-2">
        {/* With a region selected the active shot folds into this row, so
            the card below loses no height to it. */}
        {selectedEvent && activeShot ? (
          <ShotSummary shot={activeShot} onOpen={() => regions.select(null)} />
        ) : selectedEvent ? (
          <span className="flex-1" />
        ) : (
          <Label className="flex-1">Shot</Label>
        )}
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7"
          aria-label="Fold inspector"
          aria-expanded
          onClick={() => setFolded(true)}
        >
          <PanelRightClose className="size-4" aria-hidden />
        </Button>
      </div>
      <div className="shrink-0">
        {selectedEvent ? (
          <SelectedRegionCard event={selectedEvent} regions={regions} compact />
        ) : activeShot ? (
          <ShotIntervalCard
            shot={activeShot}
            tier={gapTier(activeShot.split, activeShot.interval_class, baselines)}
            onClassify={(cls) => void ws.patchShot(activeShot, { interval_class: cls, interval_class_source: "manual" })}
          />
        ) : null}
      </div>
      {/* Under the card, so a failed save never pushes Delete and Done down. */}
      {regions.issue ? (
        <div className="shrink-0">
          <SaveNotice issue={regions.issue} busy={regions.busy} onRetry={regions.retry} onDismiss={regions.dismiss} />
        </div>
      ) : null}
      {eventsReadOnly ? (
        <div className="max-h-[40%] shrink-0 overflow-y-auto">
          <EventList events={regions.events} shots={coach.shots} />
        </div>
      ) : null}
      <CoachShotTable
        fill
        folded={shotsFolded}
        // Folded: just the header. With a region selected the list yields
        // its height to the card, down to its header row.
        className={cn(
          shotsFolded ? "shrink-0" : "flex-1",
          !shotsFolded && (selectedEvent ? "min-h-[34px]" : compact ? "min-h-24" : "min-h-40"),
        )}
        header={
          <button
            type="button"
            aria-expanded={!shotsFolded}
            onClick={() => setShotsFolded(!shotsFolded)}
            className={cn(
              "flex h-8 w-full shrink-0 items-center gap-2 px-3 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
              !shotsFolded && "border-b border-rule",
            )}
          >
            <Label>Shots</Label>
            <span className="numeral text-sm text-muted">{coach.shots.length}</span>
            <ChevronDown aria-hidden className={cn("ml-auto size-4 text-muted transition-transform", shotsFolded && "-rotate-90")} />
          </button>
        }
        shots={coach.shots}
        activeShotNumber={ws.activeShotNumber}
        baselines={baselines}
        onSelect={selectShot}
      />
    </aside>
  );
}

/** The active shot folded to one line while a region is selected; a click
 *  opens it (drops the region, as picking a shot does). */
function ShotSummary({ shot, onOpen }: { shot: CoachShot; onOpen: () => void }) {
  const cls = shot.interval_class;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open shot ${pad2(shot.shot_number)}`}
      className="flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md border border-rule bg-surface px-2.5 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led"
    >
      <Label>Shot</Label>
      <span className="numeral text-sm text-ink-2">
        {pad2(shot.shot_number)} &middot; {shot.split.toFixed(2)}
      </span>
      {cls ? <Chip tick={BUDGET_TICK[cls]}>{BUDGET_LABEL[cls]}</Chip> : null}
      <span className="ml-auto text-sm text-muted">Open</span>
    </button>
  );
}
