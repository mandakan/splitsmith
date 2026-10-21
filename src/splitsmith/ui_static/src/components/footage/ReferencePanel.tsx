/**
 * Compare a clip with a sorted shooter (spec 2026-10-21): a panel to the
 * left of Sort footage's clip panel showing every stage of the match as a
 * frame from a shooter who already has footage on it. A tile plays that
 * stage beside the clip being assigned; "Use stage" hands the stage to the
 * clip panel's picker, which is the only thing this panel ever decides.
 * Desktop only: two players side by side need the width.
 *
 * It renders inside the Sheet's dialog, laid out to its left, never through
 * a portal of its own: the sheet remounts per clip, and a second portal
 * mounted in the same commit landed under the sheet's backdrop.
 */
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { api, type SortReference, type SortView } from "@/lib/api";
import {
  missingCount,
  referenceShooters,
  referenceTiles,
  shooterName,
  type ReferenceTile,
} from "@/lib/footageSort";
import { cn } from "@/lib/utils";

const SELECT =
  "min-w-0 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink disabled:opacity-50";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function frameUrl(view: SortView, ref: SortReference): string | null {
  if (!ref.frame) return null;
  if (ref.source === "project")
    return api.footageSortReferenceFrameUrl(view.scan_id, ref.shooter, ref.stage);
  const clip = view.clips.find((c) => c.clip_id === ref.clip_id);
  return clip ? api.footageSortThumbUrl(view.scan_id, clip.index) : null;
}

function videoUrl(view: SortView, ref: SortReference): string | null {
  if (ref.source === "project" && ref.video_path)
    return api.videoStreamUrl(ref.shooter, ref.video_path, ref.stream_kind, null, ref.stage);
  const clip = view.clips.find((c) => c.clip_id === ref.clip_id);
  return clip ? api.footageSortVideoUrl(view.scan_id, clip.index) : null;
}

export function ReferencePanel({
  view,
  referenceShooter,
  onReferenceShooter,
  stage,
  onStage,
  assigning,
  clipId,
  onUseStage,
  onClose,
}: {
  view: SortView;
  referenceShooter: string;
  onReferenceShooter: (key: string) => void;
  /** The stage playing in the panel; the page keeps it across clips. */
  stage: number | null;
  onStage: (stage: number | null) => void;
  /** The shooter chosen in the clip panel: their stages are the "has it". */
  assigning: string;
  clipId: string;
  onUseStage: (stage: number) => void;
  onClose: () => void;
}) {
  const tiles = referenceTiles(view, referenceShooter, assigning, clipId);
  const current = tiles.find((t) => t.stage === stage && t.reference) ?? null;
  const src = current?.reference ? videoUrl(view, current.reference) : null;
  const missing = missingCount(tiles);
  return (
    <aside
      aria-label="Compare with a shooter"
      data-own-space
      className="absolute inset-y-0 right-full hidden w-[480px] flex-col border-r border-rule-strong bg-surface shadow-lg lg:flex"
    >
        <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
          <Label>Compare with</Label>
          <select
            aria-label="Reference shooter"
            className={cn(SELECT, "flex-1")}
            value={referenceShooter}
            onChange={(e) => {
              onReferenceShooter(e.target.value);
              onStage(null);
            }}
          >
            {referenceShooters(view).map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close comparison">
            &#10005;
          </Button>
        </div>
        <div className="flex flex-col gap-4 overflow-y-auto px-4 py-4">
          <div className="overflow-hidden rounded-[10px] bg-black">
            {src ? (
              <video
                key={`${referenceShooter}-${stage}`}
                controls
                autoPlay
                muted
                playsInline
                preload="metadata"
                src={src}
                className="aspect-video w-full object-contain"
              />
            ) : (
              <div className="flex aspect-video w-full items-center justify-center text-sm text-muted">
                Click a stage to play it
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted">
              {current
                ? `${shooterName(view, referenceShooter)} on stage ${pad2(current.stage)}`
                : "No stage chosen"}
            </span>
            <span className="flex-1" />
            <Button
              size="sm"
              disabled={current === null}
              onClick={() => current && onUseStage(current.stage)}
            >
              {current ? `Use stage ${pad2(current.stage)}` : "Use stage"}
            </Button>
          </div>
          <div className="flex items-center gap-3">
            <Label>Stages</Label>
            <span className="text-sm text-muted">
              <span className="numeral">{missing}</span> still missing for{" "}
              {shooterName(view, assigning)}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {tiles.map((t) => (
              <Tile
                key={t.stage}
                tile={t}
                frame={t.reference ? frameUrl(view, t.reference) : null}
                current={t.stage === stage}
                onPick={() => onStage(t.stage)}
              />
            ))}
          </div>
        </div>
    </aside>
  );
}

function Tile({
  tile,
  frame,
  current,
  onPick,
}: {
  tile: ReferenceTile;
  frame: string | null;
  current: boolean;
  onPick: () => void;
}) {
  const none = tile.reference === null;
  return (
    <button
      type="button"
      aria-label={`Stage ${pad2(tile.stage)} ${tile.name}`.trim()}
      aria-pressed={current}
      disabled={none}
      onClick={onPick}
      className={cn(
        "relative flex aspect-video flex-col justify-between overflow-hidden rounded-md border border-rule bg-surface-2 bg-cover bg-center p-1.5 text-left",
        current && "ring-1 ring-ink",
        tile.has && "opacity-50",
        none && "cursor-default",
      )}
      style={frame ? { backgroundImage: `url("${frame}")` } : undefined}
    >
      <span className="numeral rounded bg-bg/70 px-1 text-sm text-ink">{pad2(tile.stage)}</span>
      <span className="truncate rounded bg-bg/70 px-1 text-sm text-ink-2">
        {none ? "no clip" : tile.has ? "has it" : tile.name}
      </span>
    </button>
  );
}
