/**
 * Coach routes (#329).
 *
 * - ``/coach``       -- match-wide instrument view (polished/13)
 * - ``/coach/:stage`` -- per-stage deep dive (polished/14)
 *
 * Both mount under MatchShell; navigation is via the sidebar Coach
 * link + the stage list.
 *
 * Match-wide aggregates per-stage coach data client-side because the
 * server's /api/coach/distributions returns only histogram + top shots;
 * the rest (per-stage times, ranking, annotations feed) loops over the
 * project's audited stages.
 *
 * Per-stage preserves the existing wiring:
 *   - GET /api/stages/{n}/coach loads shots + videos + beep
 *   - POST .../reclassify reruns auto-classification
 *   - PATCH .../shots/{s}/coach writes class / flag / note edits
 * but the chrome / layout is the polished design.
 */

import {
  ArrowLeft,
  ArrowRight,
  Flag,
  Loader2,
  MessageSquare,
  Pause,
  Play,
  RefreshCw,
  Save,
  Zap,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type CoachIntervalClass,
  type CoachMatchDistributions,
  type CoachShot,
  type CoachStageResponse,
  type MatchProject,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const INTERVAL_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "Draw",
  split: "Fire",
  transition: "Transition",
  movement: "Movement",
  reload: "Reload",
  activation: "Activation",
};

const INTERVAL_TONE: Record<CoachIntervalClass, string> = {
  first_shot: "text-led border-led-deep bg-led/10",
  split: "text-done border-done/40 bg-done/10",
  transition: "text-live border-live/40 bg-live/10",
  movement: "text-beep border-beep/40 bg-beep-tint",
  reload: "text-manual border-manual/40 bg-manual/10",
  activation: "text-ink-2 border-rule-strong bg-surface-3",
};

const SPLIT_BUCKETS = [
  { max: 0.25, label: "fast", color: "var(--color-done)" },
  { max: 0.45, label: "ok", color: "var(--color-ink-2)" },
  { max: 0.85, label: "slow", color: "var(--color-live)" },
  { max: Infinity, label: "vslow", color: "var(--color-led)" },
];

function splitBucket(s: number): { label: string; color: string } {
  for (const b of SPLIT_BUCKETS) if (s <= b.max) return b;
  return SPLIT_BUCKETS[SPLIT_BUCKETS.length - 1];
}

export function Coach() {
  // Slug carried by ShooterScopedRoute (#353 phase 1) -- present whenever
  // we render. Threaded into nav so jumps between stages / tabs keep the
  // shooter in the URL.
  const { slug: slugParam, stage: stageParam } = useParams<{
    slug?: string;
    stage?: string;
  }>();
  if (stageParam) {
    const n = Number(stageParam);
    if (!Number.isFinite(n)) {
      return <div className="px-7 py-8 text-sm text-muted">Bad stage.</div>;
    }
    return <CoachStage key={`${slugParam ?? ""}-${n}`} stage={n} slug={slugParam} />;
  }
  return <CoachMatch slug={slugParam} />;
}

/* -------------------------------------------------------------------------- */
/* Match-wide view                                                            */
/* -------------------------------------------------------------------------- */

interface PerStageAggregate {
  stage_number: number;
  stage_name: string;
  audited: boolean;
  total_seconds: number;
  shot_count: number;
  avg_split: number | null;
  fastest_split: number | null;
  slowest_split: number | null;
  split_buckets: Record<string, number>;
  flagged_count: number;
}

function CoachMatch({ slug }: { slug?: string }) {
  const navigate = useNavigate();
  const stagePrefix = slug ? `/coach/${slug}` : "/coach";
  const auditPrefix = slug ? `/audit/${slug}` : "/audit";
  const [project, setProject] = useState<MatchProject | null>(null);
  const [perStage, setPerStage] = useState<PerStageAggregate[]>([]);
  const [distributions, setDistributions] =
    useState<CoachMatchDistributions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<
    {
      stage_number: number;
      stage_name: string;
      shot_number: number;
      time_from_beep: number | null;
      interval_class: CoachIntervalClass | null;
      note: string;
      flagged: boolean;
    }[]
  >([]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const proj = await api.getProject();
        if (!alive) return;
        setProject(proj);
        const auditedStages = proj.stages.filter(
          (s) => !s.skipped && s.time_seconds > 0,
        );
        const [coachResults, dist] = await Promise.all([
          Promise.all(
            auditedStages.map((s) =>
              api
                .getStageCoach(s.stage_number)
                .catch(() => null as CoachStageResponse | null),
            ),
          ),
          api.getMatchCoachDistributions().catch(() => null),
        ]);
        if (!alive) return;
        setDistributions(dist);

        const coachByStage = new Map<number, CoachStageResponse | null>();
        auditedStages.forEach((s, i) =>
          coachByStage.set(s.stage_number, coachResults[i]),
        );
        const annot: typeof annotations = [];
        const aggs: PerStageAggregate[] = proj.stages.map((s) => {
          const coach = coachByStage.get(s.stage_number);
          if (!coach) {
            return {
              stage_number: s.stage_number,
              stage_name: s.stage_name,
              audited: false,
              total_seconds: s.time_seconds || 0,
              shot_count: 0,
              avg_split: null,
              fastest_split: null,
              slowest_split: null,
              split_buckets: { fast: 0, ok: 0, slow: 0, vslow: 0 },
              flagged_count: 0,
            };
          }
          const splits = coach.shots.map((shot) => shot.split);
          const buckets = { fast: 0, ok: 0, slow: 0, vslow: 0 };
          for (const sp of splits) {
            const b = splitBucket(sp);
            (buckets as Record<string, number>)[b.label] += 1;
          }
          for (const shot of coach.shots) {
            if (shot.coaching_note && shot.coaching_note.trim()) {
              annot.push({
                stage_number: s.stage_number,
                stage_name: s.stage_name,
                shot_number: shot.shot_number,
                time_from_beep: shot.time_from_beep,
                interval_class: shot.interval_class,
                note: shot.coaching_note,
                flagged: shot.improvement_flag,
              });
            } else if (shot.improvement_flag) {
              annot.push({
                stage_number: s.stage_number,
                stage_name: s.stage_name,
                shot_number: shot.shot_number,
                time_from_beep: shot.time_from_beep,
                interval_class: shot.interval_class,
                note: "",
                flagged: true,
              });
            }
          }
          return {
            stage_number: s.stage_number,
            stage_name: s.stage_name,
            audited: true,
            total_seconds: s.time_seconds || 0,
            shot_count: coach.shots.length,
            avg_split:
              splits.length === 0
                ? null
                : splits.reduce((a, b) => a + b, 0) / splits.length,
            fastest_split: splits.length === 0 ? null : Math.min(...splits),
            slowest_split: splits.length === 0 ? null : Math.max(...splits),
            split_buckets: buckets,
            flagged_count: coach.shots.filter((sh) => sh.improvement_flag).length,
          };
        });
        setPerStage(aggs);
        setAnnotations(annot);
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const auditedAggs = perStage.filter((s) => s.audited);
  const headline = useMemo(() => computeHeadline(auditedAggs), [auditedAggs]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading coach data...
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-7 py-8">
        <div className="rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      </div>
    );
  }
  if (!project) return null;

  if (auditedAggs.length === 0) {
    return (
      <div className="px-7 py-8">
        <Kicker className="mb-2">Match analysis</Kicker>
        <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
          Coach
        </h1>
        <p className="max-w-xl text-sm text-muted">
          Audit a stage on the audit page to unlock match-wide coaching
          insights -- splits, interval breakdowns, and per-stage rankings.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 px-7 py-5">
      <div>
        <Kicker className="mb-2">Match analysis</Kicker>
        <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
          Coach
          <span className="ml-3 rounded border border-rule-strong bg-surface-2 px-2 py-1 align-middle font-mono text-[0.6875rem] font-bold uppercase tracking-[0.14em] text-ink-2">
            Match-wide
          </span>
        </h1>
        <p className="font-mono text-[0.75rem] uppercase tracking-[0.06em] text-muted">
          <b className="font-bold text-ink">{pad2(auditedAggs.length)}</b>{" "}
          stages audited &middot;{" "}
          <b className="font-bold text-ink">{headline.shotCount}</b> shots
          logged &middot;{" "}
          <span className="text-led">
            {project.competitor_name ?? "you"}
          </span>
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Match time"
          value={formatMinSec(headline.totalSeconds)}
          unit="audited"
          sub={`${auditedAggs.length} stages · ${headline.shotCount} shots`}
        />
        <StatCard
          label="Avg split"
          value={
            headline.avgSplit != null
              ? `${headline.avgSplit.toFixed(2)}s`
              : "--"
          }
          unit="across all shots"
          tone="done"
        />
        <StatCard
          label="Fastest split"
          value={
            headline.fastestSplit != null
              ? `${headline.fastestSplit.value.toFixed(3)}s`
              : "--"
          }
          tone="led"
          sub={
            headline.fastestSplit
              ? `stage ${pad2(headline.fastestSplit.stage)} · ${headline.fastestSplit.stage_name}`
              : undefined
          }
        />
        <StatCard
          label="Slowest split"
          value={
            headline.slowestSplit != null
              ? `${headline.slowestSplit.value.toFixed(3)}s`
              : "--"
          }
          tone={
            headline.slowestSplit && headline.slowestSplit.value > 0.85
              ? "warn"
              : undefined
          }
          sub={
            headline.slowestSplit
              ? `stage ${pad2(headline.slowestSplit.stage)} · ${headline.slowestSplit.stage_name}`
              : undefined
          }
        />
      </div>

      <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
        <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
          Per-stage breakdown
          <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.06em] text-muted">
            click a row to open audit · double-click for per-stage coach
          </span>
        </div>
        <div className="grid grid-cols-[40px_1fr_100px_120px_180px_60px_40px] items-center gap-3 border-b border-rule bg-surface-2 px-5 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
          <span>#</span>
          <span>Stage</span>
          <span className="text-right">Time</span>
          <span className="text-right">Avg split</span>
          <span>Distribution</span>
          <span className="text-right">Shots</span>
          <span />
        </div>
        {perStage.map((s) => (
          <PerStageRow
            key={s.stage_number}
            row={s}
            onOpen={() => s.audited && navigate(`${auditPrefix}/${s.stage_number}`)}
            onCoach={() => s.audited && navigate(`${stagePrefix}/${s.stage_number}`)}
          />
        ))}
      </section>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr]">
        <IntervalBreakdownCard distributions={distributions} />
        <CrtHistogramCard distributions={distributions} />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr]">
        <RecommendationsCard distributions={distributions} />
        <AnnotationsCard annotations={annotations} stagePrefix={stagePrefix} />
      </div>
    </div>
  );
}

function computeHeadline(aggs: PerStageAggregate[]) {
  let totalSeconds = 0;
  let shotCount = 0;
  let splitSum = 0;
  let splitCount = 0;
  let fastest: {
    value: number;
    stage: number;
    stage_name: string;
  } | null = null;
  let slowest: {
    value: number;
    stage: number;
    stage_name: string;
  } | null = null;
  for (const s of aggs) {
    totalSeconds += s.total_seconds;
    shotCount += s.shot_count;
    if (s.fastest_split != null) {
      splitSum += s.fastest_split * s.shot_count;
      splitCount += s.shot_count;
      if (fastest == null || s.fastest_split < fastest.value) {
        fastest = {
          value: s.fastest_split,
          stage: s.stage_number,
          stage_name: s.stage_name,
        };
      }
    }
    if (s.slowest_split != null) {
      if (slowest == null || s.slowest_split > slowest.value) {
        slowest = {
          value: s.slowest_split,
          stage: s.stage_number,
          stage_name: s.stage_name,
        };
      }
    }
  }
  const avgSplit = splitCount === 0 ? null : splitSum / splitCount;
  return {
    totalSeconds,
    shotCount,
    avgSplit,
    fastestSplit: fastest,
    slowestSplit: slowest,
  };
}

function StatCard({
  label,
  value,
  unit,
  sub,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  sub?: string;
  tone?: "led" | "warn" | "done";
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 px-5 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="mb-1 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.2em] text-subtle">
        {label}
      </div>
      <div
        className={cn(
          "font-mono text-3xl font-bold leading-none tabular-nums",
          tone === "led" && "text-led drop-shadow-[0_0_14px_var(--color-led-glow)]",
          tone === "warn" && "text-live drop-shadow-[0_0_14px_var(--color-live-glow)]",
          tone === "done" && "text-done drop-shadow-[0_0_14px_var(--color-done-glow)]",
          !tone && "text-ink",
        )}
      >
        {value}
      </div>
      {unit && (
        <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
          {unit}
        </div>
      )}
      {sub && (
        <div className="mt-1.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {sub}
        </div>
      )}
    </div>
  );
}

function PerStageRow({
  row,
  onOpen,
  onCoach,
}: {
  row: PerStageAggregate;
  onOpen: () => void;
  onCoach: () => void;
}) {
  const distTotal = Object.values(row.split_buckets).reduce(
    (a, b) => a + b,
    0,
  );
  return (
    <div
      className={cn(
        "grid grid-cols-[40px_1fr_100px_120px_180px_60px_40px] items-center gap-3 border-b border-rule px-5 py-3 last:border-b-0 transition-colors",
        row.audited
          ? "cursor-pointer hover:bg-surface-2"
          : "opacity-50",
        row.flagged_count > 0 && row.audited && "bg-led/[0.04]",
      )}
      onClick={onOpen}
      onDoubleClick={onCoach}
    >
      <span className="inline-flex size-8 items-center justify-center rounded-md border border-rule-strong bg-surface-3 font-mono text-xs font-bold tabular-nums text-ink-2">
        {pad2(row.stage_number)}
      </span>
      <div className="min-w-0">
        <div className="truncate font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
          {row.stage_name}
        </div>
        <div className="mt-0.5 inline-flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {row.audited ? (
            row.flagged_count > 0 && (
              <span className="inline-flex items-center gap-1 rounded border border-led/40 bg-led/10 px-1.5 py-0.5 font-bold text-led">
                <Flag className="size-2.5" /> {row.flagged_count}
              </span>
            )
          ) : (
            <span>not audited yet</span>
          )}
        </div>
      </div>
      <span className="text-right font-mono text-sm font-bold tabular-nums text-ink">
        {row.audited ? `${row.total_seconds.toFixed(2)}s` : "--"}
      </span>
      <span className="text-right font-mono text-sm tabular-nums text-ink-2">
        {row.avg_split != null ? `${row.avg_split.toFixed(3)}s` : "--"}
      </span>
      <SplitDistributionBar buckets={row.split_buckets} total={distTotal} />
      <span className="text-right font-mono text-[0.8125rem] tabular-nums text-muted">
        {row.shot_count || "--"}
      </span>
      <span className="text-right text-subtle">
        <ArrowRight className="ml-auto size-4" />
      </span>
    </div>
  );
}

function SplitDistributionBar({
  buckets,
  total,
}: {
  buckets: Record<string, number>;
  total: number;
}) {
  if (total === 0) {
    return <span className="font-mono text-[0.625rem] text-subtle">--</span>;
  }
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-3">
      {SPLIT_BUCKETS.map((b) => {
        const w = ((buckets[b.label] ?? 0) / total) * 100;
        return (
          <span
            key={b.label}
            style={{ width: `${w}%`, backgroundColor: b.color }}
            title={`${b.label}: ${buckets[b.label] ?? 0}`}
          />
        );
      })}
    </div>
  );
}

function IntervalBreakdownCard({
  distributions,
}: {
  distributions: CoachMatchDistributions | null;
}) {
  const fourClasses: CoachIntervalClass[] = [
    "first_shot",
    "split",
    "transition",
    "reload",
  ];
  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Interval breakdown
      </div>
      <div className="grid grid-cols-2 gap-3 p-4">
        {fourClasses.map((cls) => {
          const d =
            distributions?.distributions.find((x) => x.interval_class === cls) ??
            null;
          const dotColor =
            cls === "first_shot"
              ? "var(--color-led)"
              : cls === "split"
                ? "var(--color-done)"
                : cls === "transition"
                  ? "var(--color-live)"
                  : "var(--color-manual)";
          return (
            <div
              key={cls}
              className="rounded-xl border border-rule bg-bg-glow px-4 py-3"
            >
              <div className="mb-1 inline-flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink">
                <span
                  aria-hidden
                  className="inline-block size-1.5 rounded-full"
                  style={{ backgroundColor: dotColor }}
                />
                {INTERVAL_LABEL[cls]}
              </div>
              <div className="font-mono text-2xl font-bold tabular-nums text-ink">
                {d?.mean_s != null ? `${d.mean_s.toFixed(2)}s` : "--"}
              </div>
              <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {d
                  ? `${d.count} ${d.count === 1 ? "occurrence" : "occurrences"}`
                  : "no data"}
              </div>
              {d?.median_s != null && (
                <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
                  median {d.median_s.toFixed(2)}s · p90{" "}
                  {d.p90_s?.toFixed(2) ?? "--"}s
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CrtHistogramCard({
  distributions,
}: {
  distributions: CoachMatchDistributions | null;
}) {
  const splitDist =
    distributions?.distributions.find((d) => d.interval_class === "split") ??
    null;

  if (!splitDist || splitDist.count === 0) {
    return (
      <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface px-5 py-12 text-center text-sm text-muted">
        Split histogram appears once a stage with split-class shots is
        audited.
      </section>
    );
  }

  const ticks = [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0];
  const maxCount = Math.max(1, ...splitDist.buckets.map((b) => b.count));
  const W = 1200;
  const H = 280;
  const padX = 40;
  const padY = 20;
  const innerW = W - padX * 2;
  const innerH = H - padY * 2;
  const xOf = (v: number) => padX + ((v - 0.05) / (1.05 - 0.05)) * innerW;

  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-bg-glow shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Split histogram
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted tabular-nums">
          {splitDist.count} shots · median {splitDist.median_s?.toFixed(2)}s ·
          p90 {splitDist.p90_s?.toFixed(2)}s
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="led-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-led-soft)" stopOpacity={0.95} />
            <stop offset="100%" stopColor="var(--color-led-deep)" stopOpacity={0.55} />
          </linearGradient>
          <filter id="led-glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {[0.25, 0.5, 0.75].map((p) => (
          <line
            key={p}
            x1={padX}
            x2={W - padX}
            y1={padY + innerH * p}
            y2={padY + innerH * p}
            stroke="var(--color-rule)"
            strokeDasharray="3 5"
            strokeWidth={1}
          />
        ))}
        {splitDist.buckets.map((b) => {
          const midX = xOf((b.lo + b.hi) / 2);
          const w = ((b.hi - b.lo) / (1.05 - 0.05)) * innerW * 0.75;
          const h = (b.count / maxCount) * innerH;
          const y = padY + innerH - h;
          return (
            <rect
              key={`${b.lo}-${b.hi}`}
              x={midX - w / 2}
              y={y}
              width={w}
              height={h}
              fill="url(#led-fill)"
              filter="url(#led-glow)"
              rx={2}
            />
          );
        })}
        {splitDist.median_s != null && (
          <g>
            <line
              x1={xOf(splitDist.median_s)}
              x2={xOf(splitDist.median_s)}
              y1={padY}
              y2={H - padY}
              stroke="var(--color-ink)"
              strokeDasharray="6 4"
              strokeWidth={1.5}
            />
            <text
              x={xOf(splitDist.median_s) + 4}
              y={padY + 14}
              fill="var(--color-ink-2)"
              fontFamily="JetBrains Mono"
              fontSize={11}
              fontWeight={700}
            >
              MED {splitDist.median_s.toFixed(2)}s
            </text>
          </g>
        )}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={xOf(t)}
              x2={xOf(t)}
              y1={H - padY}
              y2={H - padY + 4}
              stroke="var(--color-subtle)"
              strokeWidth={1}
            />
            <text
              x={xOf(t)}
              y={H - 2}
              textAnchor="middle"
              fill="var(--color-subtle)"
              fontFamily="JetBrains Mono"
              fontSize={10}
            >
              {t.toFixed(2)}s
            </text>
          </g>
        ))}
      </svg>
    </section>
  );
}

function RecommendationsCard({
  distributions,
}: {
  distributions: CoachMatchDistributions | null;
}) {
  const reco = useMemo(() => buildRecommendations(distributions), [
    distributions,
  ]);
  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Practice priorities
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          derived from this match
        </span>
      </div>
      {reco.length === 0 ? (
        <div className="px-5 py-8 text-center text-sm text-muted">
          Looking good. Nothing leaps out at the priority threshold.
        </div>
      ) : (
        reco.map((r, i) => (
          <div
            key={i}
            className={cn(
              "flex gap-4 border-b border-rule px-5 py-4 last:border-b-0",
              i === 0 && "bg-led/[0.04]",
            )}
          >
            <div
              className={cn(
                "inline-flex size-10 shrink-0 items-center justify-center rounded-md font-display text-base font-bold tabular-nums",
                i === 0
                  ? "badge-led-fill"
                  : i === 1
                    ? "border border-live/40 bg-live/10 text-live"
                    : "border border-rule-strong bg-surface-3 text-ink-2",
              )}
            >
              {pad2(i + 1)}
            </div>
            <div className="min-w-0 flex-1">
              <div
                className={cn(
                  "mb-0.5 inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em]",
                  i === 0
                    ? "bg-led/10 text-led"
                    : i === 1
                      ? "bg-live/10 text-live"
                      : "bg-surface-3 text-ink-2",
                )}
              >
                <Zap className="size-2.5" /> P{i + 1} &middot; {r.tag}
              </div>
              <div className="mt-1 font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
                {r.heading}
              </div>
              <p className="mt-1 text-[0.8125rem] leading-relaxed text-muted">
                {r.body}
              </p>
            </div>
          </div>
        ))
      )}
    </section>
  );
}

function buildRecommendations(
  distributions: CoachMatchDistributions | null,
): {
  tag: string;
  heading: string;
  body: ReactNode;
}[] {
  if (!distributions) return [];
  const ranked = distributions.distributions
    .filter((d) => d.count > 0 && d.p90_s != null && d.median_s != null)
    .map((d) => ({
      cls: d.interval_class,
      median: d.median_s!,
      p90: d.p90_s!,
      gap: d.p90_s! - d.median_s!,
    }))
    .sort((a, b) => b.gap - a.gap);
  return ranked.slice(0, 3).map((r) => ({
    tag: INTERVAL_LABEL[r.cls],
    heading: `Tighten ${INTERVAL_LABEL[r.cls].toLowerCase()} consistency`,
    body: (
      <>
        P90 is{" "}
        <b className="font-bold text-ink">{r.p90.toFixed(2)}s</b> vs median{" "}
        <b className="font-bold text-ink">{r.median.toFixed(2)}s</b> -- the
        slow tail is{" "}
        <b className="font-bold text-led">+{(r.p90 - r.median).toFixed(2)}s</b>{" "}
        longer than the typical case.
      </>
    ),
  }));
}

function AnnotationsCard({
  annotations,
  stagePrefix,
}: {
  annotations: {
    stage_number: number;
    stage_name: string;
    shot_number: number;
    time_from_beep: number | null;
    interval_class: CoachIntervalClass | null;
    note: string;
    flagged: boolean;
  }[];
  stagePrefix: string;
}) {
  const navigate = useNavigate();
  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Annotations
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {annotations.length} notes
        </span>
      </div>
      {annotations.length === 0 ? (
        <div className="px-5 py-8 text-center text-sm text-muted">
          No annotations yet. Open per-stage coach and add notes to surface
          them here.
        </div>
      ) : (
        annotations.slice(0, 8).map((a, i) => (
          <button
            key={i}
            type="button"
            onClick={() => navigate(`${stagePrefix}/${a.stage_number}`)}
            className="grid w-full grid-cols-[40px_1fr_24px] items-start gap-3 border-b border-rule px-5 py-3 text-left last:border-b-0 hover:bg-surface-2"
          >
            <span className="inline-flex size-7 items-center justify-center rounded-md border border-rule-strong bg-surface-3 font-mono text-[0.6875rem] font-bold tabular-nums text-ink-2">
              {pad2(a.stage_number)}
            </span>
            <div className="min-w-0">
              <div className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                Shot {pad2(a.shot_number)}
                {a.interval_class && (
                  <span
                    className={cn(
                      "rounded border px-1.5 py-px font-bold",
                      INTERVAL_TONE[a.interval_class],
                    )}
                  >
                    {INTERVAL_LABEL[a.interval_class]}
                  </span>
                )}
                {a.flagged && (
                  <span className="inline-flex items-center gap-1 text-led">
                    <Flag className="size-2.5" />
                  </span>
                )}
              </div>
              <p className="mt-1 truncate text-[0.8125rem] text-ink-2">
                {a.note || (a.flagged ? "Flagged for review" : "--")}
              </p>
            </div>
            <ArrowRight className="ml-auto mt-1.5 size-3.5 text-subtle" />
          </button>
        ))
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Per-stage view                                                             */
/* -------------------------------------------------------------------------- */

function CoachStage({ stage, slug }: { stage: number; slug?: string }) {
  const navigate = useNavigate();
  const coachPrefix = slug ? `/coach/${slug}` : "/coach";
  const auditPrefix = slug ? `/audit/${slug}` : "/audit";
  const [project, setProject] = useState<MatchProject | null>(null);
  const [coach, setCoach] = useState<CoachStageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reclassifying, setReclassifying] = useState(false);
  const [activeShotNumber, setActiveShotNumber] = useState<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const shotListRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [p, c] = await Promise.all([
          api.getProject(),
          api.getStageCoach(stage),
        ]);
        if (!alive) return;
        setProject(p);
        setCoach(c);
        if (c && c.shots.length > 0) {
          setActiveShotNumber(c.shots[0].shot_number);
        }
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [stage]);

  useEffect(() => {
    if (!coach) return;
    const anyUnclassified = coach.shots.some((s) => s.interval_class === null);
    if (anyUnclassified && !reclassifying) {
      setReclassifying(true);
      api
        .reclassifyStageCoach(stage)
        .then((c) => setCoach(c))
        .catch(() => {})
        .finally(() => setReclassifying(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!coach || activeShotNumber == null) return;
    const shot = coach.shots.find((s) => s.shot_number === activeShotNumber);
    setNoteDraft(shot?.coaching_note ?? "");
  }, [activeShotNumber, coach]);

  // While the video is playing, advance the active shot to whichever
  // one's time_absolute has just passed under the playhead. Without
  // this the shot list + stepper stay frozen on whatever shot was last
  // clicked, even as audio of subsequent shots plays.
  //
  // Gate on isPlaying so a user click + seek doesn't fight with the
  // currentTime tick that follows (the seek would land at the clicked
  // shot's time and the auto-sync would re-confirm the same shot; that
  // path works, but isPlaying keeps the logic simple to reason about).
  useEffect(() => {
    if (!isPlaying || !coach) return;
    // Last shot whose time_absolute <= currentTime is the one currently
    // audible. Shots are sorted by shot_number; we sort by time here to
    // be defensive against any out-of-order input.
    const ordered = [...coach.shots].sort(
      (a, b) => a.time_absolute - b.time_absolute,
    );
    let current: CoachShot | null = null;
    for (const s of ordered) {
      if (s.time_absolute <= currentTime) current = s;
      else break;
    }
    if (current && current.shot_number !== activeShotNumber) {
      setActiveShotNumber(current.shot_number);
    }
  }, [currentTime, isPlaying, coach, activeShotNumber]);

  // Scroll the active shot row into view as it changes during playback.
  // Without this the highlight moves correctly but a long list scrolls
  // off-screen, so the user has to chase it manually. block:'nearest'
  // avoids yanking the viewport when the row is already visible.
  useEffect(() => {
    if (activeShotNumber == null) return;
    const container = shotListRef.current;
    if (!container) return;
    const row = container.querySelector<HTMLElement>(
      `[data-shot-number="${activeShotNumber}"]`,
    );
    row?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeShotNumber]);

  const reclassify = useCallback(async () => {
    setReclassifying(true);
    try {
      const c = await api.reclassifyStageCoach(stage);
      setCoach(c);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setReclassifying(false);
    }
  }, [stage]);

  const patchShot = useCallback(
    async (
      shotNumber: number,
      patch: Parameters<typeof api.patchStageShotCoach>[2],
    ) => {
      try {
        const c = await api.patchStageShotCoach(stage, shotNumber, patch);
        setCoach(c);
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      }
    },
    [stage],
  );

  const seekToShot = useCallback((shot: CoachShot) => {
    setActiveShotNumber(shot.shot_number);
    if (videoRef.current) videoRef.current.currentTime = shot.time_absolute;
  }, []);

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  }, []);

  if (error) {
    return (
      <div className="px-7 py-8">
        <div className="rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      </div>
    );
  }
  if (!coach || !project) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage coach...
      </div>
    );
  }

  const allStages = project.stages
    .map((s) => s.stage_number)
    .sort((a, b) => a - b);
  const idx = allStages.indexOf(stage);
  const prevStage = idx > 0 ? allStages[idx - 1] : null;
  const nextStage =
    idx >= 0 && idx < allStages.length - 1 ? allStages[idx + 1] : null;

  const activeShot =
    coach.shots.find((s) => s.shot_number === activeShotNumber) ?? null;
  const primary = coach.videos.find((v) => v.role === "primary");
  const streamUrl = primary ? api.videoStreamUrl(primary.path) : null;
  const maxAbs =
    coach.shots.length > 0
      ? Math.max(...coach.shots.map((s) => s.time_absolute))
      : 0;
  const minAbs =
    coach.shots.length > 0
      ? Math.min(...coach.shots.map((s) => s.time_absolute))
      : 0;
  const span = Math.max(0.0001, maxAbs - minAbs);

  return (
    <div className="flex flex-col gap-4 px-7 py-5">
      {/* Compact stage header */}
      <div className="flex flex-wrap items-center gap-4 border-b border-rule pb-4">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => prevStage != null && navigate(`${coachPrefix}/${prevStage}`)}
            disabled={prevStage == null}
            aria-label="Previous stage"
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink disabled:opacity-40"
          >
            <ArrowLeft className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => nextStage != null && navigate(`${coachPrefix}/${nextStage}`)}
            disabled={nextStage == null}
            aria-label="Next stage"
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink disabled:opacity-40"
          >
            <ArrowRight className="size-4" />
          </button>
        </div>
        <h1 className="font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink">
          <span className="text-led">STAGE {pad2(stage)}</span>
          <span className="mx-2 text-whisper">·</span>
          {coach.stage_name}
        </h1>
        <nav
          aria-label="Stage views"
          className="ml-auto inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5"
        >
          <button
            type="button"
            onClick={() => navigate(`${auditPrefix}/${stage}`)}
            className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink-2"
          >
            Audit
          </button>
          <button
            type="button"
            onClick={() => navigate(`/compare/${stage}`)}
            className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink-2"
          >
            Compare
          </button>
          <span className="tab-pill-led-fill inline-flex min-h-9 items-center rounded-md px-3.5">
            Coach
          </span>
        </nav>
      </div>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => void reclassify()}
          disabled={reclassifying}
          title="Re-run auto-classification"
        >
          {reclassifying ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          <span className="font-display uppercase tracking-[0.08em]">
            Reclassify
          </span>
        </Button>
        <span className="ml-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          shot legend
        </span>
        {SPLIT_BUCKETS.map((b) => (
          <span
            key={b.label}
            className="inline-flex items-center gap-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted"
          >
            <span
              aria-hidden
              className="inline-block size-1.5 rounded-full"
              style={{ backgroundColor: b.color }}
            />
            {b.label}
            {b.max !== Infinity ? ` ≤ ${b.max}s` : " > 0.85s"}
          </span>
        ))}
      </div>

      {/* Shot ruler */}
      <div className="overflow-hidden rounded-xl border border-rule-strong bg-surface px-6 py-5">
        <div className="relative h-5">
          <span
            aria-hidden
            className="absolute inset-y-1/2 left-0 right-0 h-px -translate-y-1/2 bg-rule"
          />
          {coach.shots.map((shot) => {
            const x = ((shot.time_absolute - minAbs) / span) * 100;
            const b = splitBucket(shot.split);
            const active = activeShotNumber === shot.shot_number;
            return (
              <button
                key={shot.shot_number}
                type="button"
                onClick={() => seekToShot(shot)}
                title={`Shot ${shot.shot_number} · ${shot.split.toFixed(3)}s · ${
                  shot.interval_class
                    ? INTERVAL_LABEL[shot.interval_class]
                    : ""
                }`}
                aria-label={`Shot ${shot.shot_number}`}
                className={cn(
                  "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all",
                  active
                    ? "size-4 ring-2 ring-led ring-offset-2 ring-offset-surface shadow-[0_0_8px_var(--color-led-glow)]"
                    : "size-3 hover:size-3.5",
                )}
                style={{
                  left: `${x}%`,
                  backgroundColor: b.color,
                }}
              />
            );
          })}
        </div>
      </div>

      {/* Work grid: video + current shot panel on left, full list on right */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="flex flex-col gap-3">
          <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface p-3">
            {streamUrl ? (
              <video
                ref={videoRef}
                src={streamUrl}
                controls={false}
                preload="metadata"
                playsInline
                onTimeUpdate={(e) =>
                  setCurrentTime((e.target as HTMLVideoElement).currentTime)
                }
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                className="aspect-video w-full bg-black"
              />
            ) : (
              <div className="flex aspect-video items-center justify-center bg-surface-3 text-sm text-muted">
                No primary video
              </div>
            )}
            <div className="mt-2 flex items-center gap-3">
              <button
                type="button"
                onClick={togglePlay}
                aria-label={isPlaying ? "Pause" : "Play"}
                className="inline-flex size-10 items-center justify-center rounded-full bg-led text-bg shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
              >
                {isPlaying ? (
                  <Pause className="size-4" />
                ) : (
                  <Play className="size-4" />
                )}
              </button>
              <span className="font-mono text-sm tabular-nums text-ink-2">
                {currentTime.toFixed(3)}s
              </span>
              {activeShot && (
                <span className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                  shot {pad2(activeShot.shot_number)} at{" "}
                  {activeShot.time_absolute.toFixed(3)}s
                </span>
              )}
            </div>
          </div>

          {activeShot && (
            <ActiveShotPanel
              shot={activeShot}
              noteDraft={noteDraft}
              onNoteChange={setNoteDraft}
              onSave={() =>
                void patchShot(activeShot.shot_number, {
                  coaching_note: noteDraft || null,
                })
              }
              onClassify={(cls) =>
                void patchShot(activeShot.shot_number, {
                  interval_class: cls,
                  interval_class_source: "manual",
                })
              }
              onToggleFlag={() =>
                void patchShot(activeShot.shot_number, {
                  improvement_flag: !activeShot.improvement_flag,
                })
              }
            />
          )}
        </div>

        <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface">
          <div className="border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
            All shots
            <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.06em] text-muted">
              {coach.shots.length} total
            </span>
          </div>
          <div className="grid grid-cols-[40px_70px_70px_110px_30px_24px] items-center gap-3 border-b border-rule bg-surface-2 px-5 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
            <span>#</span>
            <span className="text-right">t</span>
            <span className="text-right">split</span>
            <span>interval</span>
            <span />
            <span />
          </div>
          <div
            ref={shotListRef}
            className="max-h-[520px] overflow-y-auto"
          >
            {coach.shots.map((shot) => (
              <ShotRow
                key={shot.shot_number}
                shot={shot}
                active={activeShotNumber === shot.shot_number}
                onClick={() => seekToShot(shot)}
              />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function ActiveShotPanel({
  shot,
  noteDraft,
  onNoteChange,
  onSave,
  onClassify,
  onToggleFlag,
}: {
  shot: CoachShot;
  noteDraft: string;
  onNoteChange: (v: string) => void;
  onSave: () => void;
  onClassify: (cls: CoachIntervalClass) => void;
  onToggleFlag: () => void;
}) {
  const b = splitBucket(shot.split);
  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface px-5 py-4">
      <div className="mb-3 flex items-center gap-3">
        <span
          className="font-display text-4xl font-bold leading-none tabular-nums text-ink"
          style={{ color: b.color }}
        >
          {pad2(shot.shot_number)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
            Shot {pad2(shot.shot_number)} &middot; {b.label}
          </div>
          <div className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
            {shot.split.toFixed(3)}s split
          </div>
          <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted tabular-nums">
            {shot.time_from_beep.toFixed(3)}s from beep
          </div>
        </div>
        <button
          type="button"
          onClick={onToggleFlag}
          aria-pressed={shot.improvement_flag}
          title={
            shot.improvement_flag
              ? "Unflag this shot"
              : "Flag this shot for review"
          }
          className={cn(
            "inline-flex size-9 items-center justify-center rounded-md border transition-colors",
            shot.improvement_flag
              ? "border-led bg-led/10 text-led shadow-[0_0_10px_var(--color-led-glow)]"
              : "border-rule bg-surface-2 text-muted hover:text-ink",
          )}
        >
          <Flag className="size-4" />
        </button>
      </div>
      <div className="mb-3">
        <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
          Interval type
          {shot.interval_class_source === "auto" && (
            <span className="ml-2 text-muted/70">
              auto-classified · click to override
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              "first_shot",
              "split",
              "transition",
              "movement",
              "reload",
              "activation",
            ] as CoachIntervalClass[]
          ).map((cls) => (
            <button
              key={cls}
              type="button"
              onClick={() => onClassify(cls)}
              className={cn(
                "rounded-md border px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.08em] transition-colors",
                shot.interval_class === cls
                  ? INTERVAL_TONE[cls]
                  : "border-rule bg-surface-2 text-muted hover:text-ink",
              )}
            >
              {INTERVAL_LABEL[cls]}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
          Annotation
        </div>
        <textarea
          value={noteDraft}
          onChange={(e) => onNoteChange(e.target.value)}
          placeholder="Add a coaching note for this shot..."
          className="block min-h-[72px] w-full rounded-md border border-rule bg-surface-3 px-3 py-2 text-sm text-ink outline-none focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
            edit and click save
          </span>
          <Button
            type="button"
            variant="outline"
            onClick={onSave}
            disabled={noteDraft === (shot.coaching_note ?? "")}
          >
            <Save className="size-3.5" />
            <span className="font-display uppercase tracking-[0.08em]">
              Save
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
}

function ShotRow({
  shot,
  active,
  onClick,
}: {
  shot: CoachShot;
  active: boolean;
  onClick: () => void;
}) {
  const b = splitBucket(shot.split);
  return (
    <button
      type="button"
      onClick={onClick}
      data-shot-number={shot.shot_number}
      className={cn(
        "grid w-full grid-cols-[40px_70px_70px_110px_30px_24px] items-center gap-3 border-b border-rule px-5 py-2 text-left transition-colors hover:bg-surface-2 last:border-b-0",
        active && "bg-led/[0.06]",
      )}
    >
      <span className="font-mono text-[0.6875rem] font-bold tabular-nums text-ink">
        {pad2(shot.shot_number)}
      </span>
      <span className="text-right font-mono text-[0.6875rem] tabular-nums text-muted">
        {shot.time_from_beep.toFixed(2)}s
      </span>
      <span
        className="text-right font-mono text-[0.6875rem] font-semibold tabular-nums"
        style={{ color: b.color }}
      >
        {shot.split.toFixed(3)}s
      </span>
      <span>
        {shot.interval_class && (
          <span
            className={cn(
              "inline-block rounded border px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em]",
              INTERVAL_TONE[shot.interval_class],
            )}
          >
            {INTERVAL_LABEL[shot.interval_class]}
          </span>
        )}
      </span>
      <span className="text-center">
        {shot.coaching_note && <MessageSquare className="size-3 text-led" />}
      </span>
      <span className="text-center">
        {shot.improvement_flag && <Flag className="size-3 text-led" />}
      </span>
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatMinSec(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00.00";
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}
