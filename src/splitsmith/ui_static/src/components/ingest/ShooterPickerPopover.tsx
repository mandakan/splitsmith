/**
 * ShooterPickerPopover -- compact inline shooter selector for the wrong-
 * shooter correction flow (B1 batch banner + B2 per-video kebab).
 *
 * Renders a row of chips for all shooters except ``excludeSlug``. Single-
 * click fires ``onPick`` with the chosen slug; the caller owns the API
 * call and any loading state. ``busy`` disables the chips while a move is
 * in flight.
 *
 * No fetch inside this component -- Ingest passes the list it already
 * loaded via ``api.listMatchShooters``.
 */

import { Avatar } from "@/components/ui";
import type { ShooterListEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ShooterPickerPopoverProps {
  /** Full shooter list for this match (already fetched by the parent). */
  shooters: ShooterListEntry[];
  /** Slug to exclude (the current / source shooter). */
  excludeSlug: string;
  /** Called when the user picks a target. */
  onPick: (targetSlug: string) => void;
  /** Disables all chips while a move is in flight. */
  busy?: boolean;
}

export function ShooterPickerPopover({
  shooters,
  excludeSlug,
  onPick,
  busy = false,
}: ShooterPickerPopoverProps) {
  const targets = shooters.filter((s) => s.slug !== excludeSlug);
  if (targets.length === 0) return null;
  return (
    <div className="inline-flex flex-wrap items-center gap-1.5">
      {targets.map((s) => (
        <button
          key={s.slug}
          type="button"
          disabled={busy}
          onClick={() => onPick(s.slug)}
          title={`Move to ${s.name}`}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-rule bg-surface-2 px-2 py-1",
            "font-display text-[0.625rem] font-semibold uppercase tracking-[0.06em] text-ink-2",
            "transition-colors hover:border-led-deep hover:bg-led-tint hover:text-led",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          <Avatar size="xs" initials={initials(s.name)} seed={s.slug} name={s.name} />
          {s.name}
        </button>
      ))}
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
