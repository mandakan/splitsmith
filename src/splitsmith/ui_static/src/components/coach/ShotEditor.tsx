/**
 * ShotEditor -- the current shot on the Coach stage page (UX PR 7): the
 * ordinal, its split and time, the tier chip, one segmented control for
 * the interval class (the taxonomy tick on each option), the coaching
 * note, Flag and Save. Save is the page's one primary while the note is
 * dirty; the class and the flag write on click, as before.
 */
import { IntervalClassControl } from "@/components/coach/IntervalClassControl";
import { ShotHeading } from "@/components/coach/ShotHeading";
import { Button } from "@/components/ui/button";
import type { CoachIntervalClass, CoachShot } from "@/lib/api";
import type { GapTier } from "@/lib/splits";

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
      <ShotHeading shot={shot} tier={tier} />
      <IntervalClassControl value={shot.interval_class} onClassify={onClassify} disabled={disabled} className="mt-3" />
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
