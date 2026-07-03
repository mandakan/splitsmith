import { Link, useParams } from "react-router-dom";

import type { RawVideoManifestEntry } from "@/lib/api";
import { takeHref } from "@/lib/matchHref";
import { findTakeForPath, takeFilename } from "@/lib/takes";
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
  takeOverviewHref,
}: {
  clip: ClipItem;
  selected: boolean;
  onSelect: (path: string) => void;
  /** Link to the take-overview page when this clip is a multi-stage
   *  take; null renders no link. */
  takeOverviewHref: string | null;
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
    <div
      className={cn(
        "flex w-full items-center gap-2.5 border-l-2 px-3 py-2 transition-colors",
        selected
          ? "border-led bg-led/10"
          : "border-transparent hover:bg-surface-2",
      )}
    >
      <button
        type="button"
        onClick={() => onSelect(clip.video.path)}
        aria-current={selected ? "true" : undefined}
        className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
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
      {takeOverviewHref != null && (
        <Link
          to={takeOverviewHref}
          title={`Take overview for ${filename}`}
          aria-label={`Take overview for ${filename}`}
          className="shrink-0 rounded border border-beep/40 bg-beep-tint px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-beep transition-colors hover:bg-beep/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-beep/60"
        >
          Take
        </Link>
      )}
    </div>
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
  slug,
  rawVideos,
}: {
  model: ClipModel;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  slug: string;
  /** Raw-video manifest from the project; drives the "Take" link on
   *  rows whose source recording covers 2+ stages. */
  rawVideos: RawVideoManifestEntry[];
}) {
  const { matchId } = useParams<{ matchId?: string }>();
  const takeOverviewHrefFor = (path: string): string | null => {
    const take = findTakeForPath(rawVideos, path);
    if (take == null) return null;
    const filename = takeFilename(take);
    return filename != null ? takeHref(matchId, slug, filename) : null;
  };
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
                takeOverviewHref={takeOverviewHrefFor(clip.video.path)}
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
                takeOverviewHref={takeOverviewHrefFor(clip.video.path)}
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
