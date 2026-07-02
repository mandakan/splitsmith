import type { ClipItem, ClipModel } from "@/pages/ingest/model";
import { pad2 } from "@/pages/ingest/model";
import { cn } from "@/lib/utils";

const ROLE_BADGE: Record<string, string> = {
  primary: "P",
  secondary: "S",
  ignored: "ign",
};

function ClipRow({
  clip,
  selected,
  onSelect,
}: {
  clip: ClipItem;
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  const filename = clip.video.path.split("/").pop() ?? clip.video.path;
  const recordedAt =
    clip.video.match_timestamp &&
    new Date(clip.video.match_timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  const role = clip.video.role;
  const hasBeep = clip.video.beep_time != null;
  return (
    <button
      type="button"
      onClick={() => onSelect(clip.video.path)}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "flex w-full items-center gap-2.5 border-l-2 px-3 py-2 text-left transition-colors",
        selected
          ? "border-led bg-led/10"
          : "border-transparent hover:bg-surface-2",
      )}
    >
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "truncate font-mono text-[0.75rem] font-semibold",
            selected ? "text-led" : "text-ink",
          )}
        >
          {filename}
        </div>
        <div className="mt-0.5 truncate font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
          {recordedAt ?? "no timestamp"}
          {clip.camera && <> &middot; {clip.camera.label}</>}
        </div>
      </div>
      {clip.stageNumber != null && (
        <span
          className={cn(
            "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em]",
            role === "ignored"
              ? "border-rule bg-surface-3 text-muted line-through"
              : "border-led-deep bg-led/10 text-led",
          )}
        >
          {ROLE_BADGE[role] ?? role}
        </span>
      )}
      {hasBeep && (
        <span
          aria-label="beep detected"
          title="beep detected"
          className="size-1.5 shrink-0 rounded-full bg-beep shadow-[0_0_5px_var(--color-beep-glow)]"
        />
      )}
    </button>
  );
}

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 border-b border-rule bg-surface-2/60 px-3 py-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
      <span className="truncate">{label}</span>
      <span className="ml-auto tabular-nums text-muted">{count}</span>
    </div>
  );
}

/**
 * ClipList -- the left master column. Unassigned clips float to the top as
 * the work queue; assigned clips follow, grouped by stage. Exactly one row
 * is selected at a time, highlighted with the LED accent.
 */
export function ClipList({
  model,
  selectedPath,
  onSelect,
}: {
  model: ClipModel;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface">
      <div className="flex items-center gap-2 border-b border-rule-strong px-3 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] tabular-nums text-muted">
        <span className="font-display font-bold text-ink">
          {model.assignedCount}
        </span>
        assigned
        <span className="text-whisper">/</span>
        <span className="font-display font-bold text-live">{model.remaining}</span>
        left
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {model.remaining > 0 && (
          <div>
            <SectionHeader label="To assign" count={model.remaining} />
            {model.unassigned.map((clip) => (
              <ClipRow
                key={clip.video.video_id}
                clip={clip}
                selected={clip.video.path === selectedPath}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}

        {model.stageGroups.map((group) => (
          <div key={group.stage.stage_number}>
            <SectionHeader
              label={`Stage ${pad2(group.stage.stage_number)} ${group.stage.stage_name}`}
              count={group.clips.length}
            />
            {group.clips.map((clip) => (
              <ClipRow
                key={clip.video.video_id}
                clip={clip}
                selected={clip.video.path === selectedPath}
                onSelect={onSelect}
              />
            ))}
          </div>
        ))}

        {model.order.length === 0 && (
          <div className="px-3 py-6 text-center font-mono text-[0.6875rem] text-muted">
            No footage yet.
          </div>
        )}
      </div>
    </div>
  );
}
