/**
 * HoldoutPanel -- how the shipped voter C heads were tested. The three
 * splits the build scores (by candidate, by stage, leave one match out)
 * side by side per head at the shipped threshold, the cost of the honest
 * splits against the by-candidate one, and the folds the chosen split
 * draws over the corpus.
 */
import { useMemo } from "react";

import { Chip, Label, Segmented } from "@/components/ui";
import {
  SPLIT_EXPLAIN,
  SPLIT_LABEL,
  foldsFor,
  heldOutGap,
  pct,
  type EnsembleModel,
  type ModelMapFixture,
  type SplitName,
  type VoterCHead,
} from "@/lib/modelMap";

const SPLITS: SplitName[] = ["stratified", "grouped", "lomo"];
const HEAD_COLOR = { headcam: "var(--color-beep)", handheld: "var(--color-manual)" } as const;

export function HoldoutPanel({
  ensemble,
  fixtures,
  split,
  onSplit,
  folds,
  onPickMatch,
}: {
  ensemble: EnsembleModel;
  fixtures: ModelMapFixture[];
  split: SplitName;
  onSplit: (s: SplitName) => void;
  folds: Record<string, string>;
  onPickMatch: (match: string) => void;
}) {
  const foldList = useMemo(() => foldsFor(split, fixtures), [split, fixtures]);
  const missing = ensemble.heads.every((h) => h.heldout.length === 0);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Label>Held-out testing</Label>
        <Segmented
          label="Split"
          value={split}
          options={SPLITS.map((s) => ({ value: s, label: SPLIT_LABEL[s] }))}
          onChange={onSplit}
        />
      </div>
      <div className="grid gap-4 rounded-[10px] border border-rule bg-surface p-4 lg:grid-cols-[1fr_1fr]">
        <div className="flex flex-col gap-4">
          <p className="text-md text-ink-2">{SPLIT_EXPLAIN[split]}</p>
          {missing ? (
            <p className="text-sm text-muted">
              No held-out report on this disk. Run scripts/build_ensemble_artifacts.py to write
              build/ensemble_heldout/report.json.
            </p>
          ) : (
            <>
              <p className="text-sm text-subtle">
                At the shipped threshold, one bar per split, top to bottom: by candidate, by stage, leave match out.
              </p>
              {ensemble.heads.map((h) => (
                <HeadBars key={h.camera_class} head={h} active={split} />
              ))}
            </>
          )}
          {ensemble.heldout_built_at ? (
            <p className="text-sm text-subtle">Held-out report built {ensemble.heldout_built_at.slice(0, 10)}.</p>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <Label>{split === "lomo" ? "Folds: one per match" : split === "grouped" ? "Stage groups" : "Folds"}</Label>
          {split === "stratified" ? (
            <p className="text-sm text-muted">
              Five random folds over the {ensemble.heads.reduce((s, h) => s + h.n_candidates, 0).toLocaleString()}{" "}
              candidates. No fixture is held out whole, which is why this split flatters the model.
            </p>
          ) : null}
          {split === "grouped" ? (
            <p className="text-sm text-muted">
              {foldList.length} stage groups, dealt into five folds with each group kept whole. The build does
              not record which group went to which fold.
            </p>
          ) : null}
          {split !== "stratified" ? (
            <div className="flex max-h-[320px] flex-col overflow-y-auto">
              {foldList.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => onPickMatch(split === "lomo" ? f.key : f.key.split(":")[0])}
                  className="grid grid-cols-[1fr_auto] items-center gap-3 border-b border-rule py-1.5 text-left last:border-b-0 hover:bg-surface-2"
                >
                  <span className="flex min-w-0 items-center gap-2 text-sm text-ink-2">
                    <i
                      aria-hidden
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: folds[f.key.split(":")[0]] ?? "var(--color-muted)" }}
                    />
                    <span className="truncate">{f.key}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <FoldBar headcam={f.headcam} handheld={f.handheld} max={foldList[0]?.fixtures ?? 1} />
                    <span className="numeral w-6 text-right text-sm text-muted">{f.fixtures}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function FoldBar({ headcam, handheld, max }: { headcam: number; handheld: number; max: number }) {
  const w = 96;
  return (
    <svg width={w} height={8} aria-hidden>
      <rect x={0} y={0} width={(headcam / max) * w} height={8} rx={1} fill={HEAD_COLOR.headcam} />
      <rect x={(headcam / max) * w} y={0} width={(handheld / max) * w} height={8} rx={1} fill={HEAD_COLOR.handheld} />
    </svg>
  );
}

/** Precision and recall per split at the shipped threshold, on one 80-100 % axis. */
function HeadBars({ head, active }: { head: VoterCHead; active: SplitName }) {
  const lo = 0.8;
  const x = (v: number) => `${Math.max(0, Math.min(1, (v - lo) / (1 - lo))) * 100}%`;
  const gaps = heldOutGap(head);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <i aria-hidden className="size-2 rounded-full" style={{ background: HEAD_COLOR[head.camera_class] }} />
        <span className="text-md text-ink">{head.camera_class === "headcam" ? "Headcam head" : "Handheld head"}</span>
        <span className="text-sm text-muted">
          {head.n_fixtures} fixtures, {head.heldout_stages ?? "?"} stages, {head.heldout_matches.length} matches
        </span>
      </div>
      {(["recall", "precision"] as const).map((metric) => (
        <div key={metric} className="grid grid-cols-[72px_1fr] items-center gap-2">
          <Label>{metric}</Label>
          <div className="relative h-[46px] rounded bg-surface-2">
            {[0.85, 0.9, 0.95].map((g) => (
              <i key={g} aria-hidden className="absolute inset-y-0 w-px bg-rule" style={{ left: x(g) }} />
            ))}
            {SPLITS.map((s, i) => {
              const m = head.heldout.find((h) => h.split === s)?.at_shipped_threshold;
              if (!m) return null;
              const v = m[metric];
              return (
                <div
                  key={s}
                  className="absolute flex items-center gap-1.5"
                  style={{ top: 3 + i * 14, left: 0, right: 0, height: 12 }}
                  title={`${SPLIT_LABEL[s]}: ${pct(v)} %`}
                >
                  <div
                    className="h-2 rounded-r-sm transition-opacity"
                    style={{
                      width: x(v),
                      background: HEAD_COLOR[head.camera_class],
                      opacity: s === active ? 1 : 0.3,
                    }}
                  />
                  <span className={s === active ? "numeral text-sm text-ink" : "numeral text-sm text-subtle"}>
                    {pct(v)}
                  </span>
                  {s === active ? <span className="text-sm text-muted">{SPLIT_LABEL[s].toLowerCase()}</span> : null}
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5 pl-[80px]">
        {gaps.map((g) => (
          <Chip key={g.split} tone={g.recall < -0.01 ? "warn" : "neutral"}>
            {SPLIT_LABEL[g.split]}: recall {signed(g.recall)}, precision {signed(g.precision)}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function signed(x: number): string {
  const v = (x * 100).toFixed(1);
  return x > 0 ? `+${v}` : v;
}
