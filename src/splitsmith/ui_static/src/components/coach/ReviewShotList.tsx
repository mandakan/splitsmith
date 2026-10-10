/**
 * ReviewShotList -- the shot list on the Coach review page (#1374): the
 * same vocabulary as every shot table (ordinal, time, tiered split, the
 * interval as a chip, read-only here), the flag, and the shot's note. The
 * current shot's row opens its note under it: a textarea that saves after a
 * pause and on blur, and Flag. Rows are 40 px touch targets and nothing
 * hides behind a hover, so the list works on a tablet.
 *
 * Interval classes are Breakdown's to change; this list only shows them.
 */
import { useEffect, useRef } from "react";

import { SaveNotice } from "@/components/coach/SaveNotice";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import type { CoachShot } from "@/lib/api";
import { nearestScrollTop } from "@/lib/breakdown";
import { shotOrdinal } from "@/lib/coachReview";
import { gapTier, type TierBaselines } from "@/lib/splits";
import { BUDGET_LABEL, BUDGET_TICK } from "@/lib/timeBudget";
import { useNoteAutosave } from "@/lib/useNoteAutosave";
import { cn } from "@/lib/utils";

const GRID = "grid grid-cols-[28px_52px_58px_minmax(0,1fr)] items-center gap-2";
const TIER_TEXT = { quick: "text-done", typical: "text-ink", long: "text-live" } as const;

export interface ReviewShotListProps {
  shots: CoachShot[];
  activeShotNumber: number | null;
  baselines: TierBaselines | null;
  onSelect: (shot: CoachShot) => void;
  /** Write the shot's note; rejects on failure (the row says so). */
  onSaveNote: (shot: CoachShot, text: string) => Promise<void>;
  onToggleFlag: (shot: CoachShot) => void;
  /** Reload the stage after a conflict; the shot's note there, or null. */
  onReloadNote: (shot: CoachShot) => Promise<string | null>;
  /** No review capability: notes and flags show, nothing edits. */
  readOnly?: boolean;
  className?: string;
}

export function ReviewShotList({
  shots,
  activeShotNumber,
  baselines,
  onSelect,
  onSaveNote,
  onToggleFlag,
  onReloadNote,
  readOnly = false,
  className,
}: ReviewShotListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (activeShotNumber == null) return;
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-shot-number="${activeShotNumber}"]`)?.parentElement;
    if (!list || !row) return;
    // Scroll the list alone: scrollIntoView moves every scrollable ancestor
    // too, which opened a deep-linked page scrolled past its header.
    list.scrollTop = nearestScrollTop(list.scrollTop, list.clientHeight, row.offsetTop, row.offsetHeight);
  }, [activeShotNumber]);
  const flagged = shots.filter((s) => s.improvement_flag).length;
  return (
    <section aria-label="Shots" className={cn("overflow-hidden rounded-[10px] border border-rule bg-surface", className)}>
      <div className="flex items-center justify-between gap-3 border-b border-rule-strong px-3 py-2">
        <Label>Shots &middot; {shots.length}</Label>
        {flagged > 0 ? <Label tone="subtle">{flagged} flagged</Label> : null}
      </div>
      <div ref={listRef} className="relative max-h-[70vh] overflow-y-auto">
        {shots.map((shot) => {
          const tier = gapTier(shot.split, shot.interval_class, baselines);
          const active = shot.shot_number === activeShotNumber;
          return (
            <div key={shot.id ?? `n${shot.shot_number}`} className="border-b border-rule last:border-b-0">
              <button
                type="button"
                data-shot-number={shot.shot_number}
                aria-current={active ? "true" : undefined}
                onClick={() => onSelect(shot)}
                className={cn(
                  GRID,
                  "numeral min-h-10 w-full px-3 py-1.5 text-left text-sm text-ink-2 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                  active && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
                )}
              >
                <span className="text-muted">{shotOrdinal(shot.shot_number)}</span>
                <span className="text-right">{shot.time_from_beep.toFixed(2)}</span>
                <span className={cn("text-right font-medium", tier ? TIER_TEXT[tier.label] : "text-ink")}>{shot.split.toFixed(3)}</span>
                <span className="flex min-w-0 items-center gap-1.5 font-sans">
                  {shot.interval_class ? <Chip tick={BUDGET_TICK[shot.interval_class]}>{BUDGET_LABEL[shot.interval_class]}</Chip> : null}
                  {shot.improvement_flag ? <Chip tone="warn">Flagged</Chip> : null}
                  {!active && shot.coaching_note ? (
                    <span className="min-w-0 truncate text-muted" title={shot.coaching_note}>
                      {shot.coaching_note}
                    </span>
                  ) : null}
                </span>
              </button>
              {active ? (
                <ShotNote
                  key={shot.id ?? `n${shot.shot_number}`}
                  shot={shot}
                  onSave={onSaveNote}
                  onReload={onReloadNote}
                  onToggleFlag={onToggleFlag}
                  readOnly={readOnly}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ShotNote({
  shot,
  onSave,
  onReload,
  onToggleFlag,
  readOnly,
}: {
  shot: CoachShot;
  onSave: (shot: CoachShot, text: string) => Promise<void>;
  onReload: (shot: CoachShot) => Promise<string | null>;
  onToggleFlag: (shot: CoachShot) => void;
  readOnly: boolean;
}) {
  const note = useNoteAutosave({
    serverValue: shot.coaching_note ?? "",
    save: (text) => onSave(shot, text),
    reload: () => onReload(shot),
  });
  const label = `Note on shot ${shotOrdinal(shot.shot_number)}`;
  if (readOnly) {
    return shot.coaching_note ? <p className="px-3 pb-2.5 pl-[46px] text-md text-ink-2">{shot.coaching_note}</p> : null;
  }
  return (
    <div className="bg-surface-2 px-3 pb-2.5 pt-1">
      <textarea
        value={note.draft}
        onChange={(e) => note.onChange(e.target.value)}
        onBlur={note.flush}
        placeholder="What to keep or change on this shot"
        aria-label={label}
        rows={2}
        className="w-full resize-none rounded-md border border-rule-strong bg-surface px-2.5 py-2 text-md text-ink placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
      />
      <div className="mt-1.5 flex items-center gap-2">
        <span className="min-w-0 flex-1 text-sm text-subtle" aria-live="polite">
          {note.saving ? "Saving…" : null}
        </span>
        <Button type="button" size="lg" aria-pressed={shot.improvement_flag} onClick={() => onToggleFlag(shot)}>
          {shot.improvement_flag ? "Flagged" : "Flag"}
        </Button>
      </div>
      {note.issue ? (
        <SaveNotice
          issue={note.issue}
          busy={note.saving}
          onRetry={note.retry}
          onDismiss={note.dismiss}
          subject="note change"
          testId="note-save-notice"
        />
      ) : null}
    </div>
  );
}
