/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
/** Cockpit right rail: the RankingTable's data at a third of the height.
 *  One card per shooter - rank, name, stage time, delta to leader, and
 *  the draw / fastest / avg-split microstats (#774 semantics via
 *  statisticSplits, same as the retired RankingTable). */

import { Avatar } from "@/components/ui";
import { Label } from "@/components/ui/Label";
import { type CompareShooterRecord } from "@/lib/api";
import { splitsFromTimeline, statisticSplits } from "@/lib/splits";
import { cn } from "@/lib/utils";

import { avg, initials } from "./format";

export function LeaderboardRail({
  shooters,
}: {
  shooters: CompareShooterRecord[];
}) {
  const rows = shooters
    .map((s) => {
      const pairs = splitsFromTimeline(s.shots);
      const splits = statisticSplits(pairs);
      return {
        shooter: s,
        time: s.stage_time_seconds ?? Infinity,
        draw: pairs.length > 0 ? pairs[0].split : null,
        fastestSplit: splits.length === 0 ? null : Math.min(...splits),
        avgSplit: splits.length === 0 ? null : avg(splits),
      };
    })
    .sort((a, b) => a.time - b.time)
    .map((row, i) => ({ ...row, rank: i + 1 }));
  const leaderTime = rows.length > 0 ? rows[0].time : Infinity;

  return (
    <aside
      data-testid="leaderboard-rail"
      aria-label="Leaderboard"
      className="flex w-[340px] flex-none flex-col overflow-hidden rounded-[10px] border border-rule bg-surface"
    >
      <div className="flex items-baseline justify-between border-b border-rule-strong px-3 py-2">
        <Label>Leaderboard</Label>
        <Label tone="subtle">stage time &middot; gap</Label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((row) => (
          <div key={row.shooter.slug} className="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1 border-b border-rule px-3 py-2.5 last:border-b-0">
            <span className={cn("numeral text-md", row.rank === 1 ? "text-led" : "text-muted")}>{row.rank}</span>
            <span className="inline-flex min-w-0 items-center gap-2">
              <Avatar size="xs" initials={initials(row.shooter.name)} tone={undefined} seed={row.shooter.slug} />
              <span data-testid="rail-name" className="truncate text-md font-medium text-ink">
                {row.shooter.name}
              </span>
            </span>
            <span className="numeral text-right text-md text-ink">
              {Number.isFinite(row.time) ? row.time.toFixed(2) : "\u2014"}
              {row.rank !== 1 && Number.isFinite(row.time) ? (
                <span className="ml-1.5 text-sm text-live">{`+${(row.time - leaderTime).toFixed(2)}s`}</span>
              ) : null}
            </span>
            <span aria-hidden="true" />
            <div className="numeral col-span-2 col-start-2 flex items-center gap-3 text-sm text-muted">
              <span data-testid="rail-draw">
                draw <b className="font-medium text-ink-2">{row.draw != null ? row.draw.toFixed(2) : "\u2014"}</b>
              </span>
              <span data-testid="rail-fast">
                fast <b className="font-medium text-ink-2">{row.fastestSplit != null ? row.fastestSplit.toFixed(3) : "\u2014"}</b>
              </span>
              <span data-testid="rail-avg">
                avg <b className="font-medium text-ink-2">{row.avgSplit != null ? row.avgSplit.toFixed(3) : "\u2014"}</b>
              </span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
