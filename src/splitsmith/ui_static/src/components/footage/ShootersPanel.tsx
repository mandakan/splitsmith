/**
 * ShootersPanel -- the match's shooters on the Footage page (UX PR 6).
 * One row each: initials, name (a link that makes them the drop zone's
 * target), video count, and a menu with Open Audit, Rebuild trims when
 * caches are missing, and Remove. Absorbs the Shooters page's per-shooter
 * controls; Add opens the AddShooterSheet.
 */
import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import type { ShooterListEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface ShootersPanelProps {
  shooters: ShooterListEntry[];
  activeSlug: string;
  editDenied: boolean;
  hrefs: { footage: (slug: string) => string; audit: (slug: string) => string };
  onAdd: () => void;
  onRemove: (shooter: ShooterListEntry) => void;
  onRebuildTrims: (shooter: ShooterListEntry) => void;
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function ShootersPanel({ shooters, activeSlug, editDenied, hrefs, onAdd, onRemove, onRebuildTrims }: ShootersPanelProps) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  return (
    <section aria-label="Shooters" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-strong px-3 py-1.5">
        <Label>Shooters</Label>
        <Button size="sm" variant="ghost" onClick={onAdd} disabled={editDenied}>
          Add
        </Button>
      </div>
      {shooters.map((s) => {
        const current = s.slug === activeSlug;
        return (
          <div
            key={s.slug}
            className={cn(
              "relative flex items-center gap-2.5 border-b border-rule px-3 py-2 text-md last:border-b-0",
              current && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
            )}
          >
            <span aria-hidden className="inline-grid size-5 shrink-0 place-items-center rounded-full bg-surface-3 font-mono text-xs text-ink-2">
              {initials(s.name)}
            </span>
            <Link to={hrefs.footage(s.slug)} className={cn("min-w-0 flex-1 truncate font-medium", current ? "text-ink" : "text-ink-2 hover:text-ink")}>
              {s.name}
            </Link>
            <span className="numeral shrink-0 text-sm text-muted">
              {s.video_count} {s.video_count === 1 ? "video" : "videos"}
            </span>
            <Button
              size="icon"
              variant="ghost"
              aria-label={`${s.name} actions`}
              aria-haspopup="menu"
              aria-expanded={menuFor === s.slug}
              onClick={() => setMenuFor((v) => (v === s.slug ? null : s.slug))}
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
            <Menu open={menuFor === s.slug} onClose={() => setMenuFor(null)} align="right">
              <Link role="menuitem" className={menuItemClass} to={hrefs.audit(s.slug)}>
                Open Audit
              </Link>
              {s.stages_missing_trim > 0 ? (
                <button
                  type="button"
                  role="menuitem"
                  className={menuItemClass}
                  disabled={editDenied}
                  onClick={() => {
                    setMenuFor(null);
                    onRebuildTrims(s);
                  }}
                >
                  Rebuild trims ({s.stages_missing_trim})
                </button>
              ) : null}
              <button
                type="button"
                role="menuitem"
                className={cn(menuItemClass, "text-led-text")}
                disabled={editDenied}
                onClick={() => {
                  setMenuFor(null);
                  onRemove(s);
                }}
              >
                Remove&hellip;
              </button>
            </Menu>
          </div>
        );
      })}
      {shooters.length <= 1 ? (
        <p className="px-3 py-2.5 text-sm text-muted">Add a squadmate to compare runs side by side.</p>
      ) : null}
    </section>
  );
}
