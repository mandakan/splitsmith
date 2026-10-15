/**
 * RankerPanel -- the beep ranker: how often each candidate model put the
 * real beep first while its match was held out, per tag, and what the
 * shipped model leans on (LR coefficients, GBDT importances).
 */
import { Chip, Label } from "@/components/ui";
import type { BeepRankerModel } from "@/lib/modelMap";

const NAME: Record<string, string> = { heuristic: "Heuristic", lr: "Logistic regression", gbdt: "GBDT" };

export function RankerPanel({ ranker }: { ranker: BeepRankerModel }) {
  const maxCoef = Math.max(...ranker.features.map((f) => Math.abs(f.lr_coef ?? 0)), 1e-9);
  const maxImp = Math.max(...ranker.features.map((f) => f.gbdt_importance ?? 0), 1e-9);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Label>Beep ranker</Label>
        {ranker.shipped_version ? <Chip>{ranker.shipped_version}</Chip> : null}
      </div>
      <div className="grid gap-4 rounded-[10px] border border-rule bg-surface p-4 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted">
            {ranker.n_fixtures} fixtures, {ranker.n_reachable} with the beep among the candidates. Scored leaving
            one match out across {ranker.folds.length} folds: {ranker.folds.join(", ")}.
          </p>
          {ranker.scores.map((s) => (
            <div key={s.name} className="grid grid-cols-[150px_1fr_auto] items-center gap-3">
              <span className="text-sm text-ink-2">
                {NAME[s.name] ?? s.name}
                {s.name === ranker.shipped_kind ? <span className="ml-1.5 text-done">shipped</span> : null}
              </span>
              <div className="h-2 rounded bg-surface-2">
                <div
                  className="h-2 rounded"
                  style={{
                    width: `${s.top1_pct}%`,
                    background: s.name === ranker.shipped_kind ? "var(--color-done)" : "var(--color-muted)",
                  }}
                />
              </div>
              <span className="numeral text-sm text-ink">
                {s.top1} / {s.n}
              </span>
            </div>
          ))}
          <div className="flex flex-wrap gap-1.5">
            {Object.values(ranker.by_tag).map((t) => (
              <Chip key={t.name} tone={t.top1_pct < 90 ? "warn" : "neutral"}>
                {t.name} {t.top1} / {t.n}
              </Chip>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="grid grid-cols-[1fr_120px_120px] gap-3">
            <Label>Feature</Label>
            <Label>LR coef</Label>
            <Label>GBDT weight</Label>
          </div>
          {ranker.features.map((f) => (
            <div key={f.name} className="grid grid-cols-[1fr_120px_120px] items-center gap-3">
              <span className="truncate font-mono text-sm text-ink-2">{f.name}</span>
              <Diverging value={f.lr_coef} max={maxCoef} />
              <div className="h-2 rounded bg-surface-2" title={f.gbdt_importance?.toFixed(3)}>
                <div
                  className="h-2 rounded bg-muted"
                  style={{ width: `${((f.gbdt_importance ?? 0) / maxImp) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** A bar from the centre: right for a positive coefficient, left for negative. */
function Diverging({ value, max }: { value: number | null; max: number }) {
  const v = value ?? 0;
  const half = (Math.abs(v) / max) * 50;
  return (
    <div className="relative h-2 rounded bg-surface-2" title={value?.toFixed(3)}>
      <i aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-rule-strong" />
      <div
        className="absolute inset-y-0 rounded"
        style={{
          left: v >= 0 ? "50%" : `${50 - half}%`,
          width: `${half}%`,
          background: v >= 0 ? "var(--color-done)" : "var(--color-live)",
        }}
      />
    </div>
  );
}
