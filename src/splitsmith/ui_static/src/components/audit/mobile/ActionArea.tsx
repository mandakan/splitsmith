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
  /** The selected shot against its rise foot (lib/riseFoot.footReadout);
   *  null without a foot or a selected shot. */
  foot?: { text: string; onRule: boolean } | null;
  /** Whether the selected shot has a rise foot it is not already on. */
  canSeat?: boolean;
  /** Reviewing a fixture: the review flag at the selection (lib/fixtureReview
   *  ``flagAt``), ``null`` for none. Absent outside a review, which leaves
   *  the line out. */
  flag?: string | null;
  onNudge(deltaMs: -10 | 10): void;
  /** Seat the selected shot on its rise foot; absent until the 1 ms
   *  peaks are in, which hides the button. */
  onSeat?(): void;
  /** Move the shot to the burst before it; absent hides the button. */
  onEarlier?(): void;
  /** Whether there is an earlier burst to move to. */
  canEarlier?: boolean;
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

const btn = "min-h-11 whitespace-nowrap rounded-md border border-rule px-2 font-mono text-sm disabled:opacity-50";

export function ActionArea(props: ActionAreaProps) {
  const { target, readOnly, hasVideo = true, foot = null, canSeat = false, flag, onEarlier, canEarlier = false, onNudge, onSeat, onDeleteShot, onShowVideo, onPromote, onAddShot } =
    props;
  return (
    <div className="flex flex-col gap-1 px-2 pb-2">
      <div aria-live="polite" className="truncate font-mono text-sm">
        {readout(props)}
      </div>
      {/* Always there, empty when it has nothing to say: the area keeps one
          height in every state, so the waveform above never shifts. */}
      <div
        data-testid="foot-line"
        className={`min-h-5 truncate text-sm ${foot?.onRule ? "text-muted" : "text-ink-2"}`}
      >
        {target.kind === "shot" && foot ? foot.text : "\u00a0"}
      </div>
      {flag !== undefined ? (
        // Two lines, always there in a review, so the buttons never move.
        <div data-testid="review-flag" className="line-clamp-2 min-h-10 text-sm text-ink">
          {flag ?? "\u00a0"}
        </div>
      ) : null}
      <div className="flex min-h-11 gap-1.5">
        {target.kind === "shot" && (
          <>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(-10)} aria-label="-10 ms">
              -10
            </button>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(10)} aria-label="+10 ms">
              +10
            </button>
            {onSeat ? (
              <button
                type="button"
                className={btn}
                disabled={readOnly || !canSeat}
                onClick={onSeat}
              >
                Seat
              </button>
            ) : null}
            {onEarlier ? (
              <button
                type="button"
                className={btn}
                disabled={readOnly || !canEarlier}
                onClick={onEarlier}
                aria-label="Earlier rise"
              >
                Earlier
              </button>
            ) : null}
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
