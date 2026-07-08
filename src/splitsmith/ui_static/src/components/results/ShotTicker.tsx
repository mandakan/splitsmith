/**
 * ShotTicker - chronograph HUD overlaid bottom-left on the Results
 * video. Elapsed-from-beep clock + shot counter on top; the current
 * shot's interval label, split value (bucket-colored), and bucket text
 * below - color is never the sole cue. aria-hidden: the transport row
 * already carries the accessible clock, and a live region firing per
 * shot would be noise. Read-only by contract (share-link surface).
 */
import { useEffect, useRef, useState } from "react";

import type { CoachShot } from "@/lib/api";
import { INTERVAL_LABEL, currentShotIndex, splitBucket } from "@/lib/splits";
import { cn } from "@/lib/utils";

interface ShotTickerProps {
  shots: CoachShot[];
  beepTime: number;
  time: number;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

export function ShotTicker({ shots, beepTime, time }: ShotTickerProps) {
  const idx = currentShotIndex(shots, time);
  const shot = idx >= 0 ? shots[idx] : null;
  const elapsed = Math.max(0, time - beepTime);

  // One motion moment: a short tint pulse on the split row when the
  // live shot changes. Skipped entirely under prefers-reduced-motion.
  const [pulse, setPulse] = useState(false);
  const prevShotRef = useRef<number | null>(null);
  useEffect(() => {
    const n = shot?.shot_number ?? null;
    if (prevShotRef.current === n) return;
    prevShotRef.current = n;
    if (n == null) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setPulse(true);
    const t = window.setTimeout(() => setPulse(false), 150);
    return () => window.clearTimeout(t);
  }, [shot]);

  const bucket = shot ? splitBucket(shot.split) : null;

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute bottom-2 left-2 rounded-lg bg-black/60 px-3 py-2 backdrop-blur-sm"
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-2xl font-semibold leading-none tabular-nums text-ink">
          {elapsed.toFixed(2)}
        </span>
        <span className="font-mono text-xs tabular-nums text-muted">
          {pad2(shot?.shot_number ?? 0)}/{pad2(shots.length)}
        </span>
      </div>
      {shot && bucket ? (
        <div
          className={cn(
            "-mx-1 mt-1 flex items-baseline gap-2 rounded px-1 transition-colors",
            pulse && "bg-led-tint",
          )}
        >
          <span className="font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted">
            {shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "Split"}
          </span>
          <span
            className="font-mono text-sm font-bold tabular-nums"
            style={{ color: bucket.color }}
          >
            {shot.split.toFixed(2)}
          </span>
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {bucket.label}
          </span>
        </div>
      ) : null}
    </div>
  );
}
