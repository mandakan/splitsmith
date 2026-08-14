/**
 * One slot that always names what it will act on: a kept shot, a
 * rejected candidate (promote preserves the detector's provenance) or
 * nothing (add). Read-only disables rather than hides, so the operator
 * on a share-less mirror still sees what the surface can do.
 */
import type { AuditTarget } from "@/lib/audit-target";

export interface ActionAreaProps {
  target: AuditTarget;
  shotOrdinal: { index: number; total: number } | null;
  splitS: number | null;
  nudgeMs: number;
  readOnly: boolean;
  /** Whether the stage has a primary video to show. Defaults to true so
   *  existing callers keep the button; the mobile audit page passes
   *  false when the project payload names no video for this stage. */
  hasVideo?: boolean;
  onNudge(deltaMs: -10 | 10): void;
  onDeleteShot(): void;
  onShowVideo(): void;
  onPromote(): void;
  onAddShot(): void;
}

function readout(props: ActionAreaProps): string {
  const { target, shotOrdinal, splitS, nudgeMs } = props;
  let text: string;
  if (target.kind === "shot") {
    const ord = shotOrdinal != null ? `shot ${shotOrdinal.index}/${shotOrdinal.total}` : "shot";
    const split = splitS != null ? ` . ${splitS.toFixed(3)} s` : "";
    const nudge = nudgeMs !== 0 ? ` . ${nudgeMs > 0 ? "+" : ""}${nudgeMs} ms` : "";
    text = `${ord}${split}${nudge}`;
  } else if (target.kind === "candidate") {
    const conf = target.marker.confidence;
    text = conf != null ? `rejected candidate . conf ${conf.toFixed(2)}` : "rejected candidate";
  } else {
    text = "no shot at playhead";
  }
  return props.readOnly ? `${text} . read-only` : text;
}

const btn = "min-h-11 rounded-md border border-rule px-3 font-mono text-sm disabled:opacity-50";

export function ActionArea(props: ActionAreaProps) {
  const { target, readOnly, hasVideo = true, onNudge, onDeleteShot, onShowVideo, onPromote, onAddShot } = props;
  return (
    <div className="flex flex-col gap-1 px-2 pb-2">
      <div aria-live="polite" className="truncate font-mono text-sm">
        {readout(props)}
      </div>
      <div className="flex gap-2">
        {target.kind === "shot" && (
          <>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(-10)}>
              -10 ms
            </button>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(10)}>
              +10 ms
            </button>
            <button type="button" className={btn} disabled={readOnly} onClick={onDeleteShot}>
              Delete
            </button>
            {hasVideo && (
              <button type="button" className={`${btn} ml-auto`} onClick={onShowVideo}>
                Video
              </button>
            )}
          </>
        )}
        {target.kind === "candidate" && (
          <button type="button" className="btn-led-fill min-h-11 flex-1 rounded-md" disabled={readOnly} onClick={onPromote}>
            Promote candidate
          </button>
        )}
        {target.kind === "none" && (
          <button type="button" className={`${btn} flex-1`} disabled={readOnly} onClick={onAddShot}>
            Add shot at playhead
          </button>
        )}
      </div>
    </div>
  );
}
