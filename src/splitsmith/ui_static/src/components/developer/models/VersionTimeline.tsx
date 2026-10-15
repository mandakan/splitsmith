/**
 * VersionTimeline -- every committed version of the shipped calibration:
 * fixtures per head over time (stacked), with the versions that moved the
 * corpus marked. Picking one lists what it added and dropped; an added
 * fixture still on disk opens on the map.
 */
import { useMemo, useState } from "react";

import { Label } from "@/components/ui";
import { pct, versionSeries, type ModelVersion } from "@/lib/modelMap";

const W = 720;
const H = 170;
const PAD = { l: 34, r: 12, t: 10, b: 22 };

export function VersionTimeline({
  history,
  known,
  onSelectFixture,
}: {
  history: ModelVersion[];
  known: Set<string>;
  onSelectFixture: (stem: string) => void;
}) {
  const series = useMemo(() => versionSeries(history), [history]);
  const [picked, setPicked] = useState<number | null>(null);
  const current = picked ?? (series.length ? series.length - 1 : null);
  if (series.length === 0) {
    return (
      <section className="flex flex-col gap-3">
        <Label>Shipped versions</Label>
        <p className="text-sm text-muted">No git history here; the timeline needs a checkout.</p>
      </section>
    );
  }
  const maxN = Math.max(...series.map((p) => p.total), 1);
  const x = (i: number) => PAD.l + (series.length === 1 ? 0 : (i * (W - PAD.l - PAD.r)) / (series.length - 1));
  const y = (n: number) => PAD.t + (1 - n / maxN) * (H - PAD.t - PAD.b);
  const area = (top: (i: number) => number, bottom: (i: number) => number) =>
    series
      .map((_, i) => `${i === 0 ? "M" : "L"}${x(i)},${top(i)}`)
      .concat(
        series
          .map((_, i) => series.length - 1 - i)
          .map((i) => `L${x(i)},${bottom(i)}`),
      )
      .join(" ") + " Z";
  // The single-corpus versions (before the per-class heads) stack as one band.
  const headTop = (i: number) => y(series[i].headcam + (series[i].handheld || 0) + (series[i].version.n_fixtures.all ?? 0));
  const headBottom = (i: number) => y(series[i].handheld + (series[i].version.n_fixtures.all ?? 0));
  const handTop = headBottom;
  const handBottom = () => y(0);
  const ticks = [0, Math.round(maxN / 2), maxN];
  const v = current !== null ? series[current].version : null;

  return (
    <section className="flex flex-col gap-3">
      <Label>Shipped versions</Label>
      <div className="rounded-[10px] border border-rule bg-surface p-4">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Calibration fixtures per version">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--color-rule)" />
              <text x={PAD.l - 6} y={y(t)} textAnchor="end" dominantBaseline="middle" fontSize={10} fill="var(--color-subtle)">
                {t}
              </text>
            </g>
          ))}
          <path d={area(handTop, handBottom)} fill="var(--color-manual)" fillOpacity={0.35} />
          <path d={area(headTop, headBottom)} fill="var(--color-beep)" fillOpacity={0.35} />
          {series.map((p, i) => (
            <g key={p.version.sha} onClick={() => setPicked(i)} className="cursor-pointer">
              <rect x={x(i) - 8} y={PAD.t} width={16} height={H - PAD.t - PAD.b} fill="transparent" />
              {i === current ? (
                <line x1={x(i)} x2={x(i)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-ink-2)" strokeDasharray="2 3" />
              ) : null}
              <circle
                cx={x(i)}
                cy={y(p.total)}
                r={p.changed ? 4 : 2.5}
                fill={p.changed ? "var(--color-ink)" : "var(--color-muted)"}
                stroke="var(--color-surface)"
                strokeWidth={1.5}
              >
                <title>{`${p.version.date} ${p.version.subject}`}</title>
              </circle>
            </g>
          ))}
          {series
            .filter((p, i) => i === 0 || p.version.date.slice(0, 7) !== series[i - 1].version.date.slice(0, 7))
            .map((p) => (
              <text key={p.version.sha} x={x(p.index)} y={H - 6} fontSize={10} fill="var(--color-subtle)" textAnchor="middle">
                {p.version.date.slice(0, 7)}
              </text>
            ))}
        </svg>
        <div className="mt-1 flex flex-wrap gap-4 text-sm text-muted">
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden className="size-2.5 rounded-[2px] bg-beep/50" />
            Headcam head
          </span>
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden className="size-2.5 rounded-[2px] bg-manual/50" />
            Handheld head (one shared corpus before May 5)
          </span>
          <span>Big dots changed the corpus. Click a version.</span>
        </div>
        {v ? (
          <div className="mt-4 border-t border-rule pt-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-mono text-sm text-muted">{v.sha}</span>
              <span className="numeral text-sm text-ink-2">{v.date}</span>
              <span className="text-md text-ink">{v.subject}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 text-sm text-muted">
              {Object.entries(v.n_fixtures).map(([k, n]) => (
                <span key={k}>
                  {k} <span className="numeral text-ink-2">{n}</span>
                  {v.precision[k] !== undefined ? (
                    <>
                      {" "}
                      (CV precision {pct(v.precision[k])} %, recall {pct(v.recall[k])} %)
                    </>
                  ) : null}
                </span>
              ))}
            </div>
            <StemList title={`Added ${v.added.length}`} stems={v.added} known={known} onSelect={onSelectFixture} />
            <StemList title={`Dropped ${v.removed.length}`} stems={v.removed} known={known} onSelect={onSelectFixture} />
          </div>
        ) : null}
      </div>
    </section>
  );
}

function StemList({
  title,
  stems,
  known,
  onSelect,
}: {
  title: string;
  stems: string[];
  known: Set<string>;
  onSelect: (stem: string) => void;
}) {
  if (stems.length === 0) return null;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-sm text-ink-2">{title}</summary>
      <ul className="mt-1 grid max-h-[220px] gap-0.5 overflow-y-auto md:grid-cols-2">
        {stems.map((s) => (
          <li key={s} className="truncate font-mono text-sm">
            {known.has(s) ? (
              <button type="button" className="text-ink-2 hover:text-ink hover:underline" onClick={() => onSelect(s)}>
                {s}
              </button>
            ) : (
              <span className="text-subtle" title="No longer on disk (renamed or removed)">
                {s}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
