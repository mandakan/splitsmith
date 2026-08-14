import { CheckCircle2 } from "lucide-react";

export function DiffList({
  fps,
  fns,
}: {
  fps: { time: number; ensemble_score: number; vote_total: number }[];
  fns: number[];
}) {
  return (
    <div className="rounded border border-rule/60 p-3 text-xs">
      <div className="mb-2 font-semibold uppercase tracking-wide text-muted">
        Diffs
      </div>
      {fps.length === 0 && fns.length === 0 && (
        <div className="flex items-center gap-1 text-success">
          <CheckCircle2 className="size-3.5" /> no diffs
        </div>
      )}
      {fps.length > 0 && (
        <div className="mb-2">
          <div className="text-[10px] uppercase text-orange-500">false positives ({fps.length})</div>
          <ul className="mt-1 space-y-0.5 font-mono">
            {fps.slice(0, 8).map((c, i) => (
              <li key={`fp-${i}`}>{c.time.toFixed(3)}s -- vote {c.vote_total} (score {c.ensemble_score.toFixed(2)})</li>
            ))}
          </ul>
        </div>
      )}
      {fns.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-red-500">false negatives ({fns.length})</div>
          <ul className="mt-1 space-y-0.5 font-mono">
            {fns.slice(0, 8).map((t, i) => (
              <li key={`fn-${i}`}>{t.toFixed(3)}s</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
