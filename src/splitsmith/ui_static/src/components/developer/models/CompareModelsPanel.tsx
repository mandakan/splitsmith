/**
 * CompareModelsPanel -- two models side by side: each head's figures under
 * the three splits at its own shipped threshold, leave-one-match-out match
 * by match, and the parameters that tell them apart. Rules live in
 * ``lib/modelMap`` (``compareHeads``, ``paramRows``).
 */
import { useMemo } from "react";

import { Label, Table, Td, Th, Tr } from "@/components/ui";
import {
  CLASSIFIER_LABEL,
  MEMBERSHIP_COLOR,
  SPLIT_LABEL,
  compareHeads,
  corpusMembership,
  paramRows,
  pct,
  type ModelMap,
  type SplitMetrics,
} from "@/lib/modelMap";
import { cn } from "@/lib/utils";

const A = MEMBERSHIP_COLOR.a;
const B = MEMBERSHIP_COLOR.b;

export function CompareModelsPanel({ a, b }: { a: ModelMap; b: ModelMap }) {
  const heads = useMemo(() => compareHeads(a, b), [a, b]);
  const params = useMemo(() => paramRows(a, b), [a, b]);
  const membership = useMemo(() => corpusMembership(a, b), [a, b]);
  const counts = useMemo(() => {
    const c = { both: 0, a: 0, b: 0, neither: 0 };
    for (const m of Object.values(membership)) c[m] += 1;
    return c;
  }, [membership]);
  const nameA = a.model?.name ?? "A";
  const nameB = b.model?.name ?? "B";

  return (
    <section className="flex flex-col gap-3">
      <Label>Compare models</Label>
      <div className="flex flex-col gap-5 rounded-[10px] border border-rule bg-surface p-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted">
          <Key color={A}>{`${nameA} ${a.model?.version ?? ""}`}</Key>
          <Key color={B}>{`${nameB} ${b.model?.version ?? ""}`}</Key>
          <span>
            Calibrated on both <span className="numeral text-ink-2">{counts.both}</span>, only {nameA}{" "}
            <span className="numeral text-ink-2">{counts.a}</span>, only {nameB}{" "}
            <span className="numeral text-ink-2">{counts.b}</span>.
          </span>
        </div>
        <p className="text-sm text-subtle">
          Each model is scored on its own corpus, at its own threshold. Leave match out is the fairest line between
          them, and a match both corpora hold is the fairest single figure.
        </p>

        {heads.map((h) => (
          <div key={h.cameraClass} className="flex flex-col gap-3">
            <span className="text-md text-ink">{CLASSIFIER_LABEL[h.cameraClass]}</span>
            <div className="grid gap-4 xl:grid-cols-2">
              <Table>
                <thead>
                  <tr>
                    <Th>Split</Th>
                    <Th align="right">Recall {nameA}</Th>
                    <Th align="right">Recall {nameB}</Th>
                    <Th align="right">Precision {nameA}</Th>
                    <Th align="right">Precision {nameB}</Th>
                  </tr>
                </thead>
                <tbody>
                  {h.rows.map((r) => (
                    <Tr key={r.split}>
                      <Td>{SPLIT_LABEL[r.split]}</Td>
                      <Td kind="num">{pct(r.a?.recall)}</Td>
                      <Td kind="num">
                        {pct(r.b?.recall)}
                        <Delta a={r.a?.recall} b={r.b?.recall} />
                      </Td>
                      <Td kind="num">{pct(r.a?.precision)}</Td>
                      <Td kind="num">
                        {pct(r.b?.precision)}
                        <Delta a={r.a?.precision} b={r.b?.precision} />
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <MatchBars rows={h.matches} perMatch={h.perMatch} />
            </div>
          </div>
        ))}

        <details>
          <summary className="cursor-pointer text-sm text-ink-2">
            Parameters ({params.filter((p) => p.differs).length} differ)
          </summary>
          <div className="mt-2">
            <Table>
              <thead>
                <tr>
                  <Th>Parameter</Th>
                  <Th align="right">{nameA}</Th>
                  <Th align="right">{nameB}</Th>
                </tr>
              </thead>
              <tbody>
                {params.map((p) => (
                  <Tr key={p.label}>
                    <Td className={p.differs ? "text-ink" : "text-muted"}>{p.label}</Td>
                    <Td kind="num" dim={!p.differs}>
                      {p.a}
                    </Td>
                    <Td kind="num" dim={!p.differs}>
                      {p.b}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        </details>
      </div>
    </section>
  );
}

function Key({ color, children }: { color: string; children: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-ink-2">
      <i aria-hidden className="size-2.5 rounded-[2px]" style={{ background: color }} />
      {children}
    </span>
  );
}

function Delta({ a, b }: { a: number | undefined; b: number | undefined }) {
  if (a === undefined || b === undefined) return null;
  const d = (b - a) * 100;
  if (Math.abs(d) < 0.05) return null;
  return (
    <span className={cn("ml-1.5 text-sm", d > 0 ? "text-done" : "text-live")}>
      {d > 0 ? "+" : ""}
      {d.toFixed(1)}
    </span>
  );
}

/** Leave-one-match-out recall per match: A over B, on one 50-100 % axis. */
function MatchBars({
  rows,
  perMatch,
}: {
  rows: { match: string; a: SplitMetrics | null; b: SplitMetrics | null }[];
  perMatch: { a: boolean; b: boolean };
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted">No per-match figures: the build predates them.</p>;
  }
  const lo = 0.5;
  const w = (v: number) => `${Math.max(0, Math.min(1, (v - lo) / (1 - lo))) * 100}%`;
  return (
    <div className="flex flex-col gap-1.5">
      <Label>Leave match out recall</Label>
      {rows.map((r) => (
        <div key={r.match} className="grid grid-cols-[minmax(0,180px)_1fr] items-center gap-3">
          <span className="truncate text-sm text-ink-2" title={r.match}>
            {r.match}
          </span>
          <div className="flex flex-col gap-0.5">
            {([
              [r.a, A, perMatch.a],
              [r.b, B, perMatch.b],
            ] as const).map(([m, color, recorded], i) => (
              <div key={i} className="flex h-2.5 items-center gap-1.5">
                {m ? (
                  <>
                    <div className="h-2 rounded-r-sm" style={{ width: w(m.recall), background: color }} />
                    <span className="numeral text-sm text-muted">{pct(m.recall)}</span>
                  </>
                ) : (
                  <span className="text-sm text-subtle">
                    {recorded ? "not in corpus" : "build recorded no per-match figures"}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
      <p className="text-sm text-subtle">
        Axis from 50 %. Only matches a model trained on can be left out of it; builds before model bundles kept
        no per-match figures.
      </p>
    </div>
  );
}
