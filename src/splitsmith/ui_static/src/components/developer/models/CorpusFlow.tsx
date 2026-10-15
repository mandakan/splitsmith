/**
 * CorpusFlow -- the corpus as a flow: all fixtures, split by camera class,
 * into what the shipped build did with them. Hovering a band (or a node)
 * hands its fixtures to the page, which lights them up on the map;
 * clicking a status node filters the map to it. Layout is
 * ``lib/modelMap.layoutFlow``.
 */
import { useMemo, useState } from "react";

import { Label } from "@/components/ui";
import { corpusFlow, layoutFlow, type FixtureStatus, type ModelMapFixture } from "@/lib/modelMap";

const W = 640;
const H = 220;
const NODE_W = 12;
const LABEL_W = 150;

export function CorpusFlow({
  fixtures,
  onHighlight,
  onPickStatus,
}: {
  fixtures: ModelMapFixture[];
  onHighlight: (stems: Set<string> | null) => void;
  onPickStatus: (status: FixtureStatus) => void;
}) {
  const laid = useMemo(
    () => layoutFlow(corpusFlow(fixtures), { width: W - LABEL_W, height: H, nodeWidth: NODE_W, gap: 12 }),
    [fixtures],
  );
  const [hot, setHot] = useState<string | null>(null);
  const enter = (key: string, stems: string[]) => {
    setHot(key);
    onHighlight(new Set(stems));
  };
  const leave = () => {
    setHot(null);
    onHighlight(null);
  };
  const nodeStems = (id: string) =>
    laid.links.filter((l) => l.source === id || l.target === id).flatMap((l) => l.stems);

  return (
    <section className="flex flex-col gap-3">
      <Label>From corpus to model</Label>
      <div className="rounded-[10px] border border-rule bg-surface p-4">
        <svg
          viewBox={`-4 -8 ${W + 8} ${H + 24}`}
          className="h-auto w-full max-w-[760px]"
          role="img"
          aria-label="How the fixtures reach the shipped voter C heads"
        >
          {laid.links.map((l) => {
            const key = `${l.source}>${l.target}`;
            const target = laid.nodes.find((n) => n.id === l.target);
            return (
              <path
                key={key}
                d={l.d}
                fill={target?.color ?? "var(--color-muted)"}
                fillOpacity={hot === null ? 0.22 : hot === key || hot === l.source || hot === l.target ? 0.5 : 0.06}
                onMouseEnter={() => enter(key, l.stems)}
                onMouseLeave={leave}
                className="cursor-pointer transition-[fill-opacity]"
              >
                <title>{`${l.value} fixtures`}</title>
              </path>
            );
          })}
          {laid.nodes.map((n) => {
            const status = n.id.startsWith("st:") ? (n.id.slice(3) as FixtureStatus) : null;
            const lastColumn = n.column === 2;
            return (
              <g
                key={n.id}
                onMouseEnter={() => enter(n.id, nodeStems(n.id))}
                onMouseLeave={leave}
                onClick={status ? () => onPickStatus(status) : undefined}
                className={status ? "cursor-pointer" : undefined}
              >
                <rect x={n.x} y={n.y} width={NODE_W} height={Math.max(1, n.h)} rx={2} fill={n.color} />
                <text
                  x={lastColumn ? n.x + NODE_W + 8 : n.x + NODE_W + 6}
                  y={n.y + Math.max(n.h, 12) / 2}
                  dominantBaseline="middle"
                  fontSize={12}
                  fill="var(--color-ink-2)"
                  style={{ paintOrder: "stroke", stroke: "var(--color-surface)", strokeWidth: 3 }}
                >
                  {n.label}
                  <tspan dx={6} fill="var(--color-muted)" fontFamily="var(--font-mono)">
                    {n.value}
                  </tspan>
                </text>
              </g>
            );
          })}
        </svg>
        <p className="mt-2 text-sm text-muted">
          Every fixture follows one path. Hover a band to find its fixtures on the map; click a status to filter to it.
        </p>
      </div>
    </section>
  );
}
