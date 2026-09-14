/**
 * ShotEditor -- the current shot on the Coach stage page (UX PR 7): the
 * ordinal, its split and time, the tier chip, one segmented control for
 * the interval class (the taxonomy tick on each option), the coaching
 * note, Flag and Save. Save is the page's one primary while the note is
 * dirty; the class and the flag write on click, as before.
 */
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import type { CoachIntervalClass, CoachShot } from "@/lib/api";
import type { GapTier } from "@/lib/splits";
import { BUDGET_LABEL, BUDGET_ORDER, BUDGET_TICK } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

const TICK_BG: Record<string, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
};

const CLASSES = BUDGET_ORDER.filter((c): c is CoachIntervalClass => c !== "unclassified");

export interface ShotEditorProps {
  shot: CoachShot;
  tier: GapTier | null;
  noteDraft: string;
  onNoteChange: (v: string) => void;
  onSave: () => void;
  onClassify: (cls: CoachIntervalClass) => void;
  onToggleFlag: () => void;
  disabled?: boolean;
}

export function ShotEditor({ shot, tier, noteDraft, onNoteChange, onSave, onClassify, onToggleFlag, disabled = false }: ShotEditorProps) {
  const dirty = noteDraft !== (shot.coaching_note ?? "");
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
      <div role="group" aria-label="Interval class" className="mt-3 inline-flex flex-wrap gap-0.5 rounded-md border border-rule-strong bg-surface-2 p-0.5">
        {CLASSES.map((cls) => {
          const on = shot.interval_class === cls;
          return (
            <button
              key={cls}
              type="button"
              aria-pressed={on}
              disabled={disabled}
              onClick={() => onClassify(cls)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                on ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
              )}
            >
              <i aria-hidden className={cn("size-1.5 rounded-full", TICK_BG[BUDGET_TICK[cls]])} />
              {BUDGET_LABEL[cls]}
            </button>
          );
        })}
      </div>
      <textarea
        value={noteDraft}
        onChange={(e) => onNoteChange(e.target.value)}
        placeholder="Coaching note for this shot"
        aria-label="Coaching note"
        disabled={disabled}
        className="mt-3 h-16 w-full resize-none rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
      />
      <div className="mt-2 flex items-center justify-end gap-2">
        <Button size="sm" onClick={onToggleFlag} aria-pressed={shot.improvement_flag} disabled={disabled}>
          {shot.improvement_flag ? "Flagged" : "Flag"}
        </Button>
        <Button size="sm" variant={dirty ? "primary" : "default"} onClick={onSave} disabled={disabled || !dirty}>
          Save
        </Button>
      </div>
    </section>
  );
}
