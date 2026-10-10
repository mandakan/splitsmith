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
 * Per-stage is the review page (#1374, epic #1370): the video, the figures,
 * the time budget, and the metadata edited here -- per-shot notes and flags
 * (PATCH .../shots/{s}/coach). Regions and interval classes are display
 * only; "Adjust in Breakdown" opens the editor.
 */

import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

import { ReviewShotList } from "@/components/coach/ReviewShotList";
import { StageNoteCard } from "@/components/coach/StageNoteCard";
import { StageStrip } from "@/components/coach/StageStrip";
import { StageTransport, StageVideo } from "@/components/coach/StageViewer";
import { TimeBudgetBar } from "@/components/coach/TimeBudgetBar";
import { TimeBudgetCard } from "@/components/coach/TimeBudgetCard";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Stat, StatStrip } from "@/components/ui/Stat";
import {
  ApiError,
  api,
  capabilityDenied,
  type CoachIntervalClass,
  type CoachMatchDistributions,
  type CoachShot,
  type CoachStageResponse,
  type MatchProject,
} from "@/lib/api";
import { reviewFigures } from "@/lib/coachReview";
import { useMatchHref } from "@/lib/matchHref";
import { stageLinkSearch } from "@/lib/stageLink";
import { INTERVAL_LABEL, baselinesFromMatchDistributions, gapTier, statisticSplits } from "@/lib/splits";
import { BUDGET_LABEL, BUDGET_TICK, matchBudget, timeBudget } from "@/lib/timeBudget";
import { useStageWorkspace, deriveStageView } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";

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
  /** Count of gaps that fed avg/fastest/slowest (fire splits only when
   *  the stage has classifications). */
  split_count: number;
  split_buckets: Record<string, number>;
  flagged_count: number;
}

function CoachMatch({ slug }: { slug?: string }) {
  const href = useMatchHref();
  if (!slug) {
    // ShooterScopedRoute should keep this from rendering, but the
    // Coach() wrapper still passes slugParam through when undefined;
    // the slug-less coach route resolves the default shooter.
    return <Navigate to={href("coach")} replace />;
  }
  return <CoachMatchInner slug={slug} />;
}

function CoachMatchInner({ slug }: { slug: string }) {
  const href = useMatchHref();
  const stagePrefix = href("coach", slug);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [perStage, setPerStage] = useState<PerStageAggregate[]>([]);
  const [coachShots, setCoachShots] = useState<{ stageNumber: number; stageName: string; shots: CoachShot[] }[]>([]);
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
        const proj = await api.getProject(slug);
        if (!alive) return;
        setProject(proj);
        const auditedStages = proj.stages.filter(
          (s) => !s.skipped && s.time_seconds > 0,
        );
        const [coachResults, dist] = await Promise.all([
          Promise.all(
            auditedStages.map((s) =>
              api
                .getStageCoach(slug, s.stage_number)
                .catch(() => null as CoachStageResponse | null),
            ),
          ),
          api.getMatchCoachDistributions(slug).catch(() => null),
        ]);
        if (!alive) return;
        setDistributions(dist);
        const baselines = baselinesFromMatchDistributions(dist);

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
              split_count: 0,
              split_buckets: { quick: 0, typical: 0, long: 0 },
              flagged_count: 0,
            };
          }
          // Avg/fastest/slowest judge fire splits only; other interval
          // classes (draws, movement...) live on different timescales.
          // statisticSplits owns the rule and the unclassified fallback
          // (issue #772) - the same figures the Results page shows.
          const splits = statisticSplits(coach.shots);
          const buckets = { quick: 0, typical: 0, long: 0 };
          for (const shot of coach.shots) {
            const tier = gapTier(shot.split, shot.interval_class, baselines);
            if (tier) buckets[tier.label] += 1;
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
            split_count: splits.length,
            split_buckets: buckets,
            flagged_count: coach.shots.filter((sh) => sh.improvement_flag).length,
          };
        });
        setPerStage(aggs);
        setAnnotations(annot);
        setCoachShots(
          proj.stages
            .map((s) => ({ stageNumber: s.stage_number, stageName: s.stage_name, shots: coachByStage.get(s.stage_number)?.shots ?? [] }))
            .filter((s) => s.shots.length > 0),
        );
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [slug]);

  const auditedAggs = perStage.filter((s) => s.audited);
  const headline = useMemo(() => computeHeadline(auditedAggs), [auditedAggs]);
  const budget = useMemo(() => matchBudget(coachShots, distributions), [coachShots, distributions]);
  const shooterName = project?.competitor_name ?? null;

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading coach data...
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-4 py-4 md:px-7 md:py-5">
        <PageHeader title="Coach" />
        <p role="alert" className="text-sm text-led-text">
          {error}
        </p>
      </div>
    );
  }
  if (!project) return null;

  if (auditedAggs.length === 0) {
    return (
      <div className="px-4 py-4 md:px-7 md:py-5">
        <PageHeader title="Coach" sub={shooterName ?? undefined} />
        <p className="max-w-[52ch] text-md text-muted">
          Audit a stage first. Coach then reads where the time went on each audited stage, which intervals were the
          outliers, and how the match compares with itself.
        </p>
      </div>
    );
  }

  const pct = (v: number | undefined) => (v == null ? "\u2014" : String(Math.round(v * 100)));
  const draws = budget.stages.flatMap((st) => st.segments.filter((seg) => seg.cls === "first_shot").map((seg) => seg.seconds));
  const avgDraw = draws.length ? draws.reduce((a, b) => a + b, 0) / draws.length : null;
  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title="Coach"
        sub={
          <>
            {shooterName ? <>{shooterName} &middot; </> : null}
            {auditedAggs.length} of {perStage.length} stages audited &middot; {budget.classifiedCount} classified
          </>
        }
      />
      <StatStrip lead className="mb-4">
        <Stat label="Movement" value={pct(budget.shareByClass.movement)} unit={budget.shareByClass.movement != null ? "%" : undefined} tone={budget.shareByClass.movement == null ? "dim" : "ink"} />
        <Stat label="Transitions" value={pct(budget.shareByClass.transition)} unit={budget.shareByClass.transition != null ? "%" : undefined} tone={budget.shareByClass.transition == null ? "dim" : "ink"} />
        <Stat label="Avg draw" value={avgDraw != null ? avgDraw.toFixed(2) : "\u2014"} unit={avgDraw != null ? "s" : undefined} tone={avgDraw == null ? "dim" : "ink"} />
        <Stat label="Avg split" value={headline.avgSplit != null ? headline.avgSplit.toFixed(3) : "\u2014"} unit={headline.avgSplit != null ? "s" : undefined} tone={headline.avgSplit == null ? "dim" : "ink"} />
        <Stat label="Outliers" value={String(budget.outlierCount)} tone={budget.outlierCount === 0 ? "dim" : "ink"} />
      </StatStrip>

      <section aria-label="Time budget by stage" className="mb-4 overflow-hidden rounded-[10px] border border-rule bg-surface">
        <div className="border-b border-rule-strong px-3.5 py-2">
          <Label>Time budget by stage</Label>
        </div>
        {perStage.map((row) => {
          const st = budget.stages.find((b) => b.stageNumber === row.stage_number) ?? null;
          return (
            <div
              key={row.stage_number}
              className="grid grid-cols-[36px_minmax(120px,160px)_minmax(0,1fr)_72px] items-center gap-3 border-b border-rule px-3.5 py-2 text-md last:border-b-0"
            >
              <span className="font-mono text-sm text-muted">{pad2(row.stage_number)}</span>
              {/* A stage without a name reads "Stage N", as the sidebar has it:
                  an empty name was a link with nothing in it to click. */}
              {row.audited ? (
                <Link to={`${stagePrefix}/${row.stage_number}`} className="truncate font-medium text-ink hover:text-led-text">
                  {row.stage_name || `Stage ${row.stage_number}`}
                </Link>
              ) : (
                <span className="truncate text-subtle">{row.stage_name || `Stage ${row.stage_number}`}</span>
              )}
              <span>{st ? <TimeBudgetBar budget={st} compact scale={budget.maxTotal > 0 ? st.total / budget.maxTotal : 0} /> : null}</span>
              <span className="numeral text-right text-ink-2">{st ? st.total.toFixed(2) : row.total_seconds > 0 ? row.total_seconds.toFixed(2) : "\u2014"}</span>
            </div>
          );
        })}
        <div className="flex flex-wrap items-center gap-4 border-t border-rule px-3.5 py-2 text-sm text-muted">
          {(["first_shot", "movement", "transition", "split", "reload", "unclassified"] as const).map((c) => (
            <span key={c} className="inline-flex items-center gap-1.5">
              <i aria-hidden className={cn("size-2 rounded-full", LEGEND_BG[c])} />
              {BUDGET_LABEL[c]}
            </span>
          ))}
          <span className="ml-auto">amber ring = an outlier inside</span>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <IntervalBreakdownCard distributions={distributions} />
        <CrtHistogramCard distributions={distributions} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RecommendationsCard distributions={distributions} />
        <AnnotationsCard annotations={annotations} stagePrefix={stagePrefix} />
      </div>
    </div>
  );
}

/** The draw's row for the averages table, from the per-stage first-shot list. */
function firstShotRow(distributions: CoachMatchDistributions | null): { mean_s: number; median_s: number; p90_s: number; count: number } | null {
  const v = (distributions?.first_shot_seconds ?? []).filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (v.length === 0) return null;
  const at = (q: number) => v[Math.min(v.length - 1, Math.floor(q * v.length))];
  const mid = Math.floor(v.length / 2);
  return {
    mean_s: v.reduce((a, b) => a + b, 0) / v.length,
    median_s: v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2,
    p90_s: at(0.9),
    count: v.length,
  };
}

const LEGEND_BG: Record<string, string> = {
  first_shot: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  split: "bg-done",
  reload: "bg-live",
  unclassified: "bg-surface-3",
};

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
    if (s.avg_split != null) {
      splitSum += s.avg_split * s.split_count;
      splitCount += s.split_count;
    }
    if (s.fastest_split != null) {
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

function IntervalBreakdownCard({
  distributions,
}: {
  distributions: CoachMatchDistributions | null;
}) {
  const classes: CoachIntervalClass[] = ["first_shot", "split", "transition", "movement", "reload"];
  return (
    <section aria-label="Interval averages" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="border-b border-rule-strong px-3.5 py-2">
        <Label>Interval averages</Label>
      </div>
      <div className="grid grid-cols-[minmax(110px,1fr)_repeat(4,minmax(0,72px))] items-center gap-3 border-b border-rule px-3.5 py-1.5">
        <Label>Type</Label>
        <Label className="text-right">Mean</Label>
        <Label className="text-right">Median</Label>
        <Label className="text-right">p90</Label>
        <Label className="text-right">Count</Label>
      </div>
      {classes.map((cls) => {
        const d =
          cls === "first_shot"
            ? firstShotRow(distributions)
            : (distributions?.distributions.find((x) => x.interval_class === cls) ?? null);
        return (
          <div key={cls} className="numeral grid grid-cols-[minmax(110px,1fr)_repeat(4,minmax(0,72px))] items-center gap-3 border-b border-rule px-3.5 py-1.5 text-md text-ink-2 last:border-b-0">
            <span className="inline-flex items-center gap-2 font-sans text-ink">
              <i aria-hidden className={cn("size-2 rounded-full", LEGEND_BG[cls] ?? "bg-ink-2")} />
              {BUDGET_LABEL[cls]}
            </span>
            <span className="text-right">{d?.mean_s != null ? d.mean_s.toFixed(2) : "\u2014"}</span>
            <span className="text-right">{d?.median_s != null ? d.median_s.toFixed(2) : "\u2014"}</span>
            <span className="text-right">{d?.p90_s != null ? d.p90_s.toFixed(2) : "\u2014"}</span>
            <span className="text-right text-muted">{d ? d.count : "\u2014"}</span>
          </div>
        );
      })}
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
      <section aria-label="Split histogram" className="rounded-[10px] border border-rule bg-surface px-3.5 py-6 text-md text-muted">
        The split histogram appears once a stage with fire splits is audited.
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
    <section aria-label="Split histogram" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-rule-strong px-3.5 py-2">
        <Label>Split histogram</Label>
        <span className="numeral text-sm text-muted">
          {splitDist.count} shots &middot; median {splitDist.median_s?.toFixed(2)} s &middot; p90 {splitDist.p90_s?.toFixed(2)} s
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="led-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-done)" stopOpacity={0.9} />
            <stop offset="100%" stopColor="var(--color-done)" stopOpacity={0.45} />
          </linearGradient>
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
              fontWeight={500}
            >
              median {splitDist.median_s.toFixed(2)} s
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
    <section aria-label="Recommendations" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-strong px-3.5 py-2">
        <Label>Recommendations</Label>
        <Label tone="subtle">from your match distributions</Label>
      </div>
      {reco.length === 0 ? (
        <p className="px-3.5 py-6 text-md text-muted">Nothing stands out yet. Audit more stages and classify their intervals.</p>
      ) : (
        reco.map((r, i) => (
          <div key={i} className="flex gap-3 border-b border-rule px-3.5 py-3 last:border-b-0">
            <span className={cn("numeral mt-0.5 inline-grid size-7 shrink-0 place-items-center rounded-full border text-sm", i === 0 ? "border-led text-led-text" : "border-rule-strong text-ink-2")}>
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <Label tone={i === 0 ? "live" : "muted"}>{r.tag}</Label>
              <div className="mt-0.5 text-md font-medium text-ink">{r.heading}</div>
              <p className="mt-0.5 text-sm text-muted">{r.body}</p>
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
    <section aria-label="Annotations" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-strong px-3.5 py-2">
        <Label>Annotations</Label>
        <Label tone="subtle">{annotations.length} {annotations.length === 1 ? "note" : "notes"}</Label>
      </div>
      {annotations.length === 0 ? (
        <p className="px-3.5 py-6 text-md text-muted">No notes yet. Open a stage's coach and note the shot under the playhead.</p>
      ) : (
        annotations.slice(0, 8).map((a, i) => (
          <button
            key={i}
            type="button"
            onClick={() => navigate(`${stagePrefix}/${a.stage_number}`)}
            className="grid w-full grid-cols-[36px_minmax(0,1fr)] items-start gap-3 border-b border-rule px-3.5 py-2.5 text-left last:border-b-0 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led"
          >
            <span className="numeral text-sm text-muted">{pad2(a.stage_number)}</span>
            <span className="min-w-0">
              <span className="flex flex-wrap items-center gap-2 text-sm text-muted">
                <span className="numeral">shot {pad2(a.shot_number)}</span>
                {a.interval_class ? <Chip tick={BUDGET_TICK[a.interval_class]}>{BUDGET_LABEL[a.interval_class]}</Chip> : null}
                {a.flagged ? <i aria-label="Flagged" className="inline-block size-1.5 rounded-full bg-live" /> : null}
              </span>
              <span className="mt-0.5 block truncate text-md text-ink-2">{a.note || (a.flagged ? "Flagged for review" : "\u2014")}</span>
            </span>
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
  const href = useMatchHref();
  if (!slug) {
    return <Navigate to={href("coach")} replace />;
  }
  return <CoachStageInner stage={stage} slug={slug} />;
}

function CoachStageInner({ stage, slug }: { stage: number; slug: string }) {
  const href = useMatchHref();
  const coachPrefix = href("coach", slug);
  const auditPrefix = href("audit", slug);
  const breakdownPrefix = href("breakdown", slug);
  // Coach is the review page (#1374, epic #1370): no band, so no peaks.
  const { search } = useLocation();
  const ws = useStageWorkspace(slug, stage, { peaks: false, link: search });
  const { project, coach, baselines, distributions, error, regions } = ws;

  const budget = useMemo(() => timeBudget(coach?.shots ?? [], distributions), [coach, distributions]);
  // Display only: the region figures count the confirmed regions, as the
  // exports and the share figures do (``reviewFigures``). Regions are
  // Breakdown's to change. Computed above the early returns below: a hook
  // cannot be conditional on ``coach`` being loaded yet.
  const figures = useMemo(() => reviewFigures(coach?.shots ?? [], regions.events), [coach, regions.events]);

  if (error) {
    return (
      <div className="px-4 py-4 md:px-7 md:py-5">
        <PageHeader ordinal={pad2(stage)} title="Stage" back={{ label: "Match coach", to: coachPrefix }} />
        <p role="alert" className="text-sm text-led-text">
          {error}
        </p>
      </div>
    );
  }
  const view = deriveStageView(ws, slug, stage);
  if (!coach || !project || !view) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage coach...
      </div>
    );
  }

  const { prevStage, nextStage } = view;
  // Breakdown opens at this moment and shot (#1377); before the video has a
  // position, the shot's own time.
  const here = stageLinkSearch({ t: ws.currentTime > 0 ? view.tFromBeep : null, shot: ws.activeShotNumber });
  const capacityWarning = coach.event_summary?.capacity_warning ?? null;
  // Notes and flags are review actions: a mirror without the review
  // capability (never the case today) shows them read-only.
  const notesReadOnly = capabilityDenied(project.capabilities, "review");
  const selectShotNumber = (n: number) => {
    const shot = coach.shots.find((s) => s.shot_number === n);
    if (shot) ws.seekToShot(shot);
  };
  const stepButton = (label: string, to: number | null, icon: React.ReactNode) =>
    to != null ? (
      <Button asChild size="icon" aria-label={label} className="size-10">
        <Link to={`${coachPrefix}/${to}`}>{icon}</Link>
      </Button>
    ) : (
      <Button type="button" size="icon" disabled aria-label={label} className="size-10">
        {icon}
      </Button>
    );
  const dash = "—";

  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        ordinal={pad2(stage)}
        title={coach.stage_name || "Stage"}
        back={{ label: "Match coach", to: coachPrefix }}
        sub={
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
            {project.competitor_name ? <span>{project.competitor_name}</span> : null}
            <Chip tick="muted">
              {coach.shots.length} {coach.shots.length === 1 ? "shot" : "shots"}
            </Chip>
            {budget.outlierCount > 0 ? (
              <Chip tone="warn">
                {budget.outlierCount} {budget.outlierCount === 1 ? "outlier" : "outliers"}
              </Chip>
            ) : null}
            {!budget.classified && coach.shots.length > 0 ? <Chip tick="muted">unclassified</Chip> : null}
            {capacityWarning ? <Chip tone="warn">{capacityWarning}</Chip> : null}
          </span>
        }
        actions={
          <>
            {/* Regions and intervals are edited in Breakdown. Coach sits
                behind DesktopGate too, so no phone reaches this header. */}
            <Button asChild size="lg">
              <Link to={`${breakdownPrefix}/${stage}${here}`}>Adjust in Breakdown</Link>
            </Button>
            <Button asChild size="lg">
              <Link to={`${auditPrefix}/${stage}`}>Audit</Link>
            </Button>
            {stepButton("Previous stage", prevStage, <ArrowLeft className="size-4" />)}
            {stepButton("Next stage", nextStage, <ArrowRight className="size-4" />)}
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="overflow-hidden rounded-[10px] border border-rule bg-surface">
            <StageVideo ws={ws} view={view} className="aspect-video w-full" />
            <StageTransport ws={ws} view={view} touch className="border-t border-rule" />
            <div className="border-t border-rule px-3.5 pb-2 pt-2.5">
              <Label>Stage</Label>
              <StageStrip
                className="mt-2"
                shots={coach.shots}
                events={regions.events}
                stageTime={view.stageTime}
                tFromBeep={view.tFromBeep}
                activeShotNumber={ws.activeShotNumber}
                onSeek={ws.seekToTime}
              />
            </div>
          </div>

          <StatStrip aria-label="Figures">
            <Stat label="Avg split" value={figures.avgSplit != null ? figures.avgSplit.toFixed(2) : dash} unit={figures.avgSplit != null ? "s" : undefined} tone={figures.avgSplit == null ? "dim" : "ink"} />
            <Stat label="Draw" value={figures.draw != null ? figures.draw.toFixed(2) : dash} unit={figures.draw != null ? "s" : undefined} tone={figures.draw == null ? "dim" : "ink"} />
            {figures.reloads > 0 ? <Stat label="Exposed reload" value={figures.exposedReload.toFixed(2)} unit="s" /> : null}
            <Stat label="On the move" value={String(figures.movingShots)} unit={figures.movingShots === 1 ? "shot" : "shots"} />
          </StatStrip>
        </div>

        {/* The notes column spans both rows at xl, so the budget sits under
            the video; on a 1024 px tablet the budget takes the full width
            under both, where its columns fit. */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-start-2 lg:row-start-1 xl:row-span-2">
          <StageNoteCard
            note={coach.stage_note ?? null}
            save={ws.saveStageNote}
            reload={async () => {
              const fresh = await ws.reload().catch(() => null);
              return fresh ? (fresh.stage_note ?? "") : null;
            }}
            readOnly={notesReadOnly}
          />
          <ReviewShotList
            shots={coach.shots}
            activeShotNumber={ws.activeShotNumber}
            baselines={baselines}
            onSelect={ws.seekToShot}
            onSaveNote={(shot, text) => ws.savePatch(shot, { coaching_note: text })}
            onToggleFlag={(shot) => void ws.patchShot(shot, { improvement_flag: !shot.improvement_flag })}
            onReloadNote={async (shot) => {
              const fresh = await ws.reload().catch(() => null);
              if (!fresh) return null;
              const same = fresh.shots.find((s) => (shot.id ? s.id === shot.id : s.shot_number === shot.shot_number));
              return same?.coaching_note ?? "";
            }}
            onNoteFocus={ws.holdActiveShot}
            readOnly={notesReadOnly}
          />
        </div>

        {coach.shots.length > 0 ? (
          <TimeBudgetCard
            budget={budget}
            onSelectShot={selectShotNumber}
            className="min-w-0 lg:col-span-2 xl:col-span-1 xl:col-start-1 xl:row-start-2"
          />
        ) : null}
      </div>
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
