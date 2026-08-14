import { type LabEvalFixture } from "@/lib/api";

import { fmtPct } from "./labPalette";

export function VoterRecallTable({ metrics }: { metrics: LabEvalFixture["metrics"] }) {
  const order: Array<keyof typeof metrics.voter_recall> = ["vote_a", "vote_b", "vote_c"];
  return (
    <div className="rounded border border-rule/60 p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Per-voter recall on this fixture
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {order.map((k) => (
          <div key={String(k)}>
            <div className="text-[10px] uppercase text-muted">{String(k).slice(-1).toUpperCase()}</div>
            <div className="font-mono text-sm">{fmtPct(metrics.voter_recall[k as string] ?? 0)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
