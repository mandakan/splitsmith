/**
 * CompetitorRow -- a single scoreboard competitor rendered as a checkbox
 * row (#598).
 *
 * Shared by CreateMatch's multi-select roster picker (``checked`` tracks
 * membership in a ``Set`` of picked competitor ids, confirmed by a
 * separate "Create match" action) and the Shooters add-shooter roster
 * picker (``checked`` stays ``false``; picking a row immediately adds the
 * shooter, which drops the row out of the list on the next data refresh
 * since the competitor is now claimed).
 */
import type { ScoreboardMatchCompetitor } from "@/lib/api";
import { cn } from "@/lib/utils";

export function CompetitorRow({
  competitor,
  checked,
  disabled = false,
  onToggle,
}: {
  competitor: ScoreboardMatchCompetitor;
  checked: boolean;
  /** Disables the row's checkbox -- used while an add/pick request for
   *  this roster is in flight so a double-click can't fire two adds. */
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={cn(
        "grid w-full cursor-pointer items-center gap-3 border-b border-rule px-4 py-2.5 transition-colors last:border-b-0",
        checked ? "bg-led-tint/40" : "hover:bg-surface-2",
        disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
      )}
      style={{ gridTemplateColumns: "24px 1fr" }}
    >
      <span className="flex items-center justify-center">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          aria-label={`Add ${competitor.name}`}
          className="size-4 cursor-pointer accent-led disabled:cursor-not-allowed"
        />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-ink">
          {competitor.name}
        </span>
        <span className="mt-0.5 flex flex-wrap gap-x-2 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          {[competitor.club, competitor.division]
            .filter(Boolean)
            .map((s, i, arr) => (
              <span key={i}>
                {s}
                {i < arr.length - 1 && (
                  <span className="ml-2 text-whisper">&middot;</span>
                )}
              </span>
            ))}
        </span>
      </span>
    </label>
  );
}
