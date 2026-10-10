/**
 * StageNoteCard -- the stage note on the Coach review page (#1376): free
 * text about the whole stage, saved after a pause in typing and on blur
 * (``useNoteAutosave``), with the inline save notice when a save did not
 * land. Read-only without the review capability.
 */
import { SaveNotice } from "@/components/coach/SaveNotice";
import { Label } from "@/components/ui/Label";
import { useNoteAutosave } from "@/lib/useNoteAutosave";
import { cn } from "@/lib/utils";

export interface StageNoteCardProps {
  /** The note as the latest coach payload has it (null: none). */
  note: string | null;
  save: (text: string) => Promise<void>;
  /** After a 409: reload the stage, its note ("" for none), or null when the reload failed. */
  reload: () => Promise<string | null>;
  readOnly?: boolean;
  className?: string;
}

export function StageNoteCard({ note, save, reload, readOnly = false, className }: StageNoteCardProps) {
  const auto = useNoteAutosave({ serverValue: note ?? "", save, reload });
  return (
    <section aria-label="Stage note" className={cn("rounded-[10px] border border-rule bg-surface px-3 py-2.5", className)}>
      <div className="flex items-center justify-between gap-3">
        <Label>Stage note</Label>
        <span className="text-sm text-subtle" aria-live="polite">
          {auto.saving ? "Saving…" : null}
        </span>
      </div>
      {readOnly ? (
        <p className="mt-2 whitespace-pre-wrap text-md text-ink-2">{note || "No note on this stage."}</p>
      ) : (
        <textarea
          value={auto.draft}
          onChange={(e) => auto.onChange(e.target.value)}
          onBlur={auto.flush}
          aria-label="Stage note"
          placeholder="What to take from this stage"
          rows={3}
          className="mt-2 w-full resize-y rounded-md border border-rule-strong bg-surface-2 px-2.5 py-2 text-md text-ink placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        />
      )}
      {auto.issue ? (
        <SaveNotice
          issue={auto.issue}
          busy={auto.saving}
          onRetry={auto.retry}
          onDismiss={auto.dismiss}
          subject="stage note change"
          testId="stage-note-save-notice"
        />
      ) : null}
    </section>
  );
}
