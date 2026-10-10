/**
 * The lab Review page's step-through (#1363): one close-up per shot whose
 * rise foot disagrees with its stored time, decided by one key, then a scan
 * of the whole waveform and a sign-off. Rules in ``lib/stepThrough``.
 *
 * Its keys are taken in the capture phase so Space and the arrows never also
 * reach the page's own play and scrub handlers.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { Button } from "@/components/ui/button";
import { isTypingTextTarget } from "@/lib/audit-input";
import type { SnapPeaks } from "@/lib/peak-snap";
import {
  CLOSEUP_HALF_S,
  STEP_MIN_MOVE_MS,
  closeupBins,
  stepActionForKey,
  stepQueue,
  type StepItem,
} from "@/lib/stepThrough";

const HEIGHT = 140;

export function StepThrough({
  markers,
  peaks,
  onSetTime,
  onReject,
  onRecord,
  onFocus,
  onDone,
  onExit,
  busy,
}: {
  markers: AuditMarker[];
  peaks: SnapPeaks;
  onSetTime: (id: string, time: number) => void;
  onReject: (marker: AuditMarker) => void;
  onRecord: (kind: string, payload: Record<string, unknown>) => void;
  onFocus: (id: string, time: number) => void;
  /** Sign the fixture off and move on. */
  onDone: () => void;
  onExit: () => void;
  busy: boolean;
}) {
  // The queue is fixed when the step-through opens: taking a suggestion must
  // not drop the shot out from under the cursor, and Backspace returns to it.
  const [queue] = useState<StepItem[]>(() =>
    stepQueue(
      markers.filter((m) => m.kind === "detected" || m.kind === "manual"),
      peaks,
    ),
  );
  const [index, setIndex] = useState(0);
  const item = index < queue.length ? queue[index] : null;
  const marker = item ? (markers.find((m) => m.id === item.id) ?? null) : null;
  const keptCount = markers.filter((m) => m.kind === "detected" || m.kind === "manual").length;

  useEffect(() => {
    if (item && marker) onFocus(item.id, marker.time);
    // Follow the cursor, not every nudge: a nudge moves the marker itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  const advance = useCallback(() => setIndex((i) => Math.min(i + 1, queue.length)), [queue.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTextTarget(e.target as HTMLElement | null)) return;
      const action = stepActionForKey(e);
      if (!action) return;
      e.preventDefault();
      e.stopPropagation();
      if (!item || !marker) {
        if (action.kind === "accept" && !busy) onDone();
        else if (action.kind === "back" && queue.length > 0) setIndex(queue.length - 1);
        return;
      }
      switch (action.kind) {
        case "accept":
          onRecord("step_rise_foot_taken", { id: item.id, from_time: marker.time, to_time: item.suggested });
          onSetTime(item.id, item.suggested);
          advance();
          break;
        case "keep":
          onRecord("step_time_kept", { id: item.id, time: marker.time, rise_foot: item.suggested });
          advance();
          break;
        case "reject":
          if (marker.kind !== "rejected") onReject(marker);
          advance();
          break;
        case "back":
          setIndex((i) => Math.max(0, i - 1));
          break;
        case "nudge": {
          const next = Math.max(0, Math.round((marker.time + action.ms / 1000) * 1000) / 1000);
          onSetTime(item.id, next);
          onFocus(item.id, next);
          break;
        }
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [item, marker, queue.length, busy, advance, onDone, onRecord, onSetTime, onReject, onFocus]);

  if (!item || !marker) {
    return (
      <div className="space-y-2 rounded-md border border-border p-4 text-sm">
        <p className="text-ink">
          {queue.length === 0
            ? `No shot is more than ${STEP_MIN_MOVE_MS} ms from its rise foot.`
            : `All ${queue.length} disagreements decided.`}{" "}
          Scan the {keptCount} markers on the waveform below; click any that looks wrong.
        </p>
        <p className="text-muted">
          <kbd>Enter</kbd> saves, marks the fixture reviewed and opens the next one.
          {queue.length > 0 ? (
            <>
              {" "}
              <kbd>Backspace</kbd> goes back to the last shot.
            </>
          ) : null}
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={onDone} disabled={busy}>
            Mark reviewed and next
          </Button>
          <Button size="sm" variant="ghost" onClick={onExit}>
            Leave step-through
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border border-border p-4 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-ink">
          {index + 1} / {queue.length} to check
        </span>
        <span className="font-mono text-muted">stored {item.stored.toFixed(3)} s</span>
        <span className="font-mono text-status-complete">
          rise foot {item.suggested.toFixed(3)} s ({item.moveMs > 0 ? "+" : ""}
          {item.moveMs} ms)
        </span>
        <span className="font-mono text-ink">
          {marker.kind === "rejected" ? "not a shot" : `marker ${marker.time.toFixed(3)} s`}
        </span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onExit}>
          Leave step-through
        </Button>
      </div>
      <Closeup
        peaks={peaks}
        item={item}
        markerTime={marker.kind === "rejected" ? null : marker.time}
        onPlace={(t) => {
          onSetTime(item.id, t);
          onFocus(item.id, t);
        }}
      />
      <p className="text-xs text-muted">
        <kbd>Enter</kbd> take the rise foot · <kbd>Space</kbd> keep the marker where it is ·{" "}
        <kbd>X</kbd> not a shot · <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> nudge 1 ms (Shift 5 ms) · click to
        place · <kbd>Backspace</kbd> back
      </p>
    </div>
  );
}

function Closeup({
  peaks,
  item,
  markerTime,
  onPlace,
}: {
  peaks: SnapPeaks;
  item: StepItem;
  markerTime: number | null;
  onPlace: (t: number) => void;
}) {
  const center = (item.stored + item.suggested) / 2;
  const half = Math.max(CLOSEUP_HALF_S, Math.abs(item.moveMs) / 2000 + 0.015);
  const bins = useMemo(() => closeupBins(peaks, center, half), [peaks, center, half]);
  const t0 = center - half;
  const span = 2 * half;
  const binW = peaks.duration / Math.max(1, peaks.peaks.length);
  const vmax = Math.max(1e-6, ...bins.map((b) => b.v));
  const x = (t: number) => ((t - t0) / span) * 1000;

  return (
    <svg
      viewBox={`0 0 1000 ${HEIGHT}`}
      preserveAspectRatio="none"
      className="block h-36 w-full cursor-crosshair select-none"
      role="img"
      aria-label="Close-up of the shot's rise"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        const t = t0 + ((e.clientX - r.left) / r.width) * span;
        onPlace(Math.max(0, Math.round(t * 1000) / 1000));
      }}
    >
      {bins.map((b) => {
        const h = (b.v / vmax) * (HEIGHT - 10);
        return (
          <rect
            key={b.t}
            x={x(b.t)}
            y={HEIGHT - h}
            width={Math.max(1, (binW / span) * 1000 - 1)}
            height={h}
            className="fill-current text-ink-2"
            opacity={0.55}
          />
        );
      })}
      <line
        x1={x(item.stored)}
        x2={x(item.stored)}
        y1={0}
        y2={HEIGHT}
        className="stroke-current text-muted"
        strokeWidth={2}
        strokeDasharray="6 5"
        vectorEffect="non-scaling-stroke"
      />
      <line
        x1={x(item.suggested)}
        x2={x(item.suggested)}
        y1={0}
        y2={HEIGHT}
        className="stroke-current text-status-complete"
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />
      {markerTime != null ? (
        <line
          x1={x(markerTime)}
          x2={x(markerTime)}
          y1={0}
          y2={HEIGHT}
          className="stroke-current text-ink"
          strokeWidth={3}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}
