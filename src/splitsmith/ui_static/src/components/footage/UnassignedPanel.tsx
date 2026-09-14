/**
 * UnassignedPanel -- the videos not yet placed on a stage (UX PR 6). One
 * row per file with a stage picker; the name opens the clip sheet. The
 * count reads amber while anything is waiting.
 */
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import type { StageEntry } from "@/lib/api";
import { shortName, type UnassignedItem } from "@/lib/footage";

export interface UnassignedPanelProps {
  items: UnassignedItem[];
  stages: StageEntry[];
  multi: boolean;
  editDenied: boolean;
  onOpen: (item: UnassignedItem) => void;
  onAssign: (item: UnassignedItem, stageNumber: number) => void;
  onRemove: (item: UnassignedItem) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function when(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function UnassignedPanel({ items, stages, multi, editDenied, onOpen, onAssign, onRemove }: UnassignedPanelProps) {
  return (
    <section aria-label="Unassigned videos" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-strong px-3 py-2">
        <Label>Unassigned</Label>
        <Label tone={items.length > 0 ? "live" : "muted"}>{items.length}</Label>
      </div>
      {items.length === 0 ? (
        <p className="px-3 py-2.5 text-sm text-muted">Every video is placed.</p>
      ) : (
        items.map((item) => {
          const base = item.video.path.split("/").pop() ?? item.video.path;
          const t = when(item.recordedAt);
          return (
            <div key={`${item.slug}:${item.video.path}`} className="border-b border-rule px-3 py-2 text-md last:border-b-0">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onOpen(item)}
                  title={base}
                  className="min-w-0 flex-1 truncate text-left font-mono text-sm text-ink hover:text-led-text"
                >
                  {shortName(item.video.path)}
                </button>
                <span className="numeral shrink-0 text-sm text-muted">
                  {multi ? `${item.shooterName}${t ? ` · ${t}` : ""}` : (t ?? "")}
                </span>
              </div>
              {editDenied ? null : (
                <div className="mt-1.5 flex items-center gap-2">
                  <select
                    aria-label={`Assign ${base} to stage`}
                    value=""
                    onChange={(e) => {
                      if (e.target.value) onAssign(item, Number(e.target.value));
                    }}
                    className="min-w-0 flex-1 rounded-md border border-rule-strong bg-surface-2 px-2 py-1 text-sm text-ink-2"
                  >
                    <option value="">Assign to stage&hellip;</option>
                    {stages
                      .filter((s) => !s.placeholder)
                      .map((s) => (
                        <option key={s.stage_number} value={s.stage_number}>
                          {pad2(s.stage_number)} &middot; {s.stage_name}
                        </option>
                      ))}
                  </select>
                  <Button size="sm" variant="ghost" onClick={() => onRemove(item)}>
                    Remove
                  </Button>
                </div>
              )}
            </div>
          );
        })
      )}
    </section>
  );
}
