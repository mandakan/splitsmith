/** Cockpit right rail: the RankingTable's data at a third of the height.
 *  One card per shooter - rank, name, stage time, delta to leader, and
 *  the draw / fastest / avg-split microstats (#774 semantics via
 *  statisticSplits, same as the retired RankingTable). */

import { Avatar } from "@/components/ui";
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
        shotCount: s.shots.length,
      };
    })
    .sort((a, b) => a.time - b.time)
    .map((row, i) => ({ ...row, rank: i + 1 }));
  const leaderTime = rows.length > 0 ? rows[0].time : Infinity;

  return (
    <aside
      data-testid="leaderboard-rail"
      aria-label="Leaderboard"
      className="flex w-[360px] flex-none flex-col overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]"
    >
      <div className="flex items-baseline justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-4 py-2.5">
        <span className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
          Leaderboard
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          stage time
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((row) => (
          <div
            key={row.shooter.slug}
            className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1.5 border-b border-rule px-4 py-3 last:border-b-0"
          >
            <span
              className={cn(
                "font-display text-xl font-bold tabular-nums",
                row.rank === 1
                  ? "text-led drop-shadow-[0_0_10px_var(--color-led-glow)]"
                  : "text-whisper",
              )}
            >
              {row.rank}
            </span>
            <span className="inline-flex min-w-0 items-center gap-2">
              <Avatar
                size="xs"
                initials={initials(row.shooter.name)}
                tone={undefined}
                seed={row.shooter.slug}
              />
              <span
                data-testid="rail-name"
                className="truncate font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-ink"
              >
                {row.shooter.name}
              </span>
            </span>
            <span
              className={cn(
                "text-right font-mono text-lg font-bold leading-none tabular-nums",
                row.rank === 1
                  ? "text-led drop-shadow-[0_0_8px_var(--color-led-glow)]"
                  : "text-ink",
              )}
            >
              {Number.isFinite(row.time) ? `${row.time.toFixed(2)}s` : "-"}
            </span>
            <span aria-hidden="true" />
            <div className="col-span-2 col-start-2 flex items-center gap-3 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted tabular-nums">
              <span data-testid="rail-draw">
                draw{" "}
                <b className="font-bold text-ink-2">
                  {row.draw != null ? row.draw.toFixed(2) : "-"}
                </b>
              </span>
              <span data-testid="rail-fast">
                fast{" "}
                <b className="font-bold text-ink-2">
                  {row.fastestSplit != null ? row.fastestSplit.toFixed(3) : "-"}
                </b>
              </span>
              <span data-testid="rail-avg">
                avg{" "}
                <b className="font-bold text-ink-2">
                  {row.avgSplit != null ? row.avgSplit.toFixed(3) : "-"}
                </b>
              </span>
              <span className="ml-auto text-subtle">
                {row.rank === 1 || !Number.isFinite(row.time)
                  ? ""
                  : `+${(row.time - leaderTime).toFixed(2)}s`}
              </span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
