/**
 * ResultsStage - read-only stage playback page (/results/:slug/:stage).
 *
 * Video + marker scrub bar + stats strip + splits list, synced through
 * one <video> element owned here. shots[].time_absolute and beep_time
 * arrive already in the served clip's coordinate system, so seeking is
 * plain currentTime assignment.
 *
 * Read-only by contract: this surface is the future share-link view -
 * no mutations, no localStorage, no operator-only assumptions.
 */
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ResultsPlayer, type FullscreenMode } from "@/components/results/ResultsPlayer";
import { Scorecard } from "@/components/results/Scorecard";
import { SplitsList } from "@/components/results/SplitsList";
import { StageStats } from "@/components/results/StageStats";
import { Kicker } from "@/components/ui";
import { ApiError, api, type CoachStageResponse, type StageScorecard } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { currentShotIndex } from "@/lib/splits";
import { cn } from "@/lib/utils";

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ResultsStage() {
  const { slug, stage } = useParams<{ slug?: string; stage?: string }>();
  const stageNumber = Number(stage);
  if (!slug || !stage || !Number.isFinite(stageNumber)) {
    return <div className="px-7 py-8 text-sm text-muted">Bad stage.</div>;
  }
  return (
    <ResultsStageInner key={`${slug}-${stageNumber}`} slug={slug} stage={stageNumber} />
  );
}

function ResultsStageInner({ slug, stage }: { slug: string; stage: number }) {
  const { shooters } = useOutletContext<MatchShellOutletContext>();
  const href = useMatchHref();
  const [coach, setCoach] = useState<CoachStageResponse | null>(null);
  const [scorecard, setScorecard] = useState<StageScorecard | null>(null);
  const [scorecardUpdatedAt, setScorecardUpdatedAt] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [fsMode, setFsMode] = useState<FullscreenMode>("off");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [playerBox, setPlayerBox] = useState<HTMLDivElement | null>(null);

  // The pinned player's height varies with viewport width, so no
  // constant is safe (same rationale as useShellHeaderHeight). Measured
  // into a CSS var the splits rows use as scroll-margin-top, so the
  // mobile auto-scroll never tucks the active row under the sticky
  // player. Written imperatively (not React state): resize churn must
  // not re-render the whole page per tick. Paused during fullscreen -
  // the fullscreened card leaves normal flow, collapsing the wrapper,
  // and publishing that bogus height would break the first auto-scroll
  // after exit. Callback-ref: the player mounts only after coach data
  // is in.
  useEffect(() => {
    if (!playerBox || fsMode !== "off") return;
    const write = () =>
      rootRef.current?.style.setProperty("--results-player-h", `${playerBox.offsetHeight}px`);
    write();
    const ro = new ResizeObserver(write);
    ro.observe(playerBox);
    return () => ro.disconnect();
  }, [playerBox, fsMode]);

  useEffect(() => {
    let alive = true;
    setLoaded(false);
    setError(null);
    setScorecard(null);
    setScorecardUpdatedAt(null);
    (async () => {
      const [coachResult, projectResult] = await Promise.allSettled([
        api.getStageCoach(slug, stage),
        api.getProject(slug),
      ]);
      if (!alive) return;

      if (coachResult.status === "fulfilled") {
        setCoach(coachResult.value);
        setLoaded(true);
      } else {
        const e = coachResult.reason;
        setError(e instanceof ApiError ? e.detail : String(e));
      }

      // Scorecard is a nice-to-have: a failed project fetch just means no
      // scorecard shows, it must never surface through the coach error banner.
      if (projectResult.status === "fulfilled") {
        const stageEntry = projectResult.value.stages.find((s) => s.stage_number === stage);
        setScorecard(stageEntry?.scorecard ?? null);
        setScorecardUpdatedAt(stageEntry?.scorecard_updated_at ?? null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [slug, stage, attempt]);

  const shooter = shooters.find((s) => s.slug === slug) ?? null;

  // This shooter's audited stages, ordered - prev/next skip stages that
  // lack audits (the overview only links audited cells; same contract).
  const auditedStages = useMemo(
    () =>
      (shooter?.stage_statuses ?? [])
        .filter((s) => s.status === "audited")
        .map((s) => s.stage_number)
        .sort((a, b) => a - b),
    [shooter],
  );
  const idx = auditedStages.indexOf(stage);
  const prevStage = idx > 0 ? auditedStages[idx - 1] : null;
  const nextStage = idx >= 0 && idx < auditedStages.length - 1 ? auditedStages[idx + 1] : null;

  const shots = useMemo(() => coach?.shots ?? [], [coach]);
  const activeShotNumber = useMemo(() => {
    const idx = currentShotIndex(shots, currentTime);
    return idx >= 0 ? shots[idx].shot_number : null;
  }, [shots, currentTime]);

  const stageTime = shots.length > 0 ? shots[shots.length - 1].time_from_beep : null;
  // Draw (shot 1) is not a split - exclude it from fastest/avg.
  const splits = useMemo(
    () => shots.filter((s) => s.shot_number !== 1).map((s) => s.split),
    [shots],
  );
  const fastestSplit = splits.length > 0 ? Math.min(...splits) : null;
  const avgSplit =
    splits.length > 0 ? splits.reduce((sum, s) => sum + s, 0) / splits.length : null;

  const seekToShot = useCallback((shot: { time_absolute: number }) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = shot.time_absolute;
    void v.play().catch(() => {});
  }, []);

  if (error) {
    return (
      <div className="px-4 py-8 md:px-7">
        <div role="alert" className="rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
        <button
          type="button"
          onClick={() => setAttempt((n) => n + 1)}
          className="mt-3 inline-flex min-h-11 items-center rounded-md border border-rule-strong bg-surface-2 px-4 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface-3"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!loaded) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage...
      </div>
    );
  }
  if (!coach) {
    return (
      <div className="px-4 py-16 text-center md:px-7">
        <Kicker>Stage {pad2(stage)}</Kicker>
        <p className="mt-4 text-sm text-muted">Stage not audited yet.</p>
        <Link
          to={href("results")}
          className="mt-4 inline-flex min-h-11 items-center rounded-md border border-rule-strong bg-surface-2 px-4 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface-3"
        >
          Back to results
        </Link>
      </div>
    );
  }

  const primary = coach.videos.find((v) => v.role === "primary");
  const navButton =
    "inline-flex size-11 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led";

  const header = (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="truncate font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink md:text-2xl">
          <span className="text-led">Stage {pad2(stage)}</span>
          {coach.stage_name ? <span className="text-ink"> - {coach.stage_name}</span> : null}
        </h1>
        {shooter ? (
          <p className="truncate font-mono text-xs uppercase tracking-[0.08em] text-muted">
            {shooter.name}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {prevStage != null ? (
          <Link
            to={href("results", slug, String(prevStage))}
            aria-label="Previous stage"
            className={navButton}
          >
            <ArrowLeft className="size-4" />
          </Link>
        ) : (
          <button
            type="button"
            disabled
            aria-label="Previous stage"
            className={cn(navButton, "opacity-40")}
          >
            <ArrowLeft className="size-4" />
          </button>
        )}
        {nextStage != null ? (
          <Link
            to={href("results", slug, String(nextStage))}
            aria-label="Next stage"
            className={navButton}
          >
            <ArrowRight className="size-4" />
          </Link>
        ) : (
          <button
            type="button"
            disabled
            aria-label="Next stage"
            className={cn(navButton, "opacity-40")}
          >
            <ArrowRight className="size-4" />
          </button>
        )}
      </div>
    </div>
  );

  if (!primary) {
    return (
      <div className="flex flex-col gap-4 px-4 py-4 md:px-7">
        {header}
        <div className="rounded-md border border-rule-strong bg-surface-2 px-4 py-6 text-center text-sm text-muted">
          No primary video for this stage.
        </div>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className="flex flex-col gap-4 px-4 py-4 md:px-7 lg:grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start"
    >
      <div className="flex flex-col gap-4 lg:col-span-2">{header}</div>
      {/* Sticky below lg so playback + auto-scrolling splits never lose
          the video. Disabled at viewport heights <= 500px: landscape
          phones report ~330-440px and the pinned player would eat the
          whole viewport there - fullscreen is the intended landscape
          mode; the smallest portrait phones are ~640px and keep sticky.
          Full-bleed bg fill so list content cannot ghost through the
          page gutters while pinned. --shell-header-h falls back to 0px:
          the share surface has no sticky header and never sets the var.
          During faux fullscreen the z classes SWAP (never stack): the
          raise frees the fixed card from this wrapper's stacking context
          (trapped-z, see elevation tokens), and keeping max-lg:z-20
          alongside would defeat it - that rule is emitted later in the
          stylesheet and would win the cascade at mobile widths. */}
      <div
        ref={setPlayerBox}
        className={cn(
          "max-lg:-mx-4 max-lg:bg-bg max-lg:px-4 max-lg:pb-2",
          "max-lg:[@media(min-height:501px)]:sticky max-lg:[@media(min-height:501px)]:top-[var(--shell-header-h,0px)]",
          fsMode === "faux" ? "z-takeover" : "max-lg:z-20",
        )}
      >
        <ResultsPlayer
          src={api.videoStreamUrl(slug, primary.path)}
          beepTime={coach.beep_time}
          shots={shots}
          videoRef={videoRef}
          onTimeChange={setCurrentTime}
          onPlayingChange={setIsPlaying}
          onFullscreenChange={setFsMode}
        />
      </div>
      <div className="flex flex-col gap-4 lg:max-h-[calc(100dvh-var(--shell-header-h,86px)-2rem)] lg:overflow-y-auto">
        <StageStats
          stageTime={stageTime}
          shotCount={shots.length}
          fastestSplit={fastestSplit}
          avgSplit={avgSplit}
        />
        <SplitsList
          shots={shots}
          activeShotNumber={activeShotNumber}
          onSeek={seekToShot}
          isPlaying={isPlaying}
        />
        {scorecard ? (
          <div className="flex flex-col gap-2">
            <Scorecard scorecard={scorecard} />
            {scorecardUpdatedAt ? (
              <p className="font-mono text-xs uppercase tracking-[0.08em] text-muted">
                from scoreboard, updated {formatTimestamp(scorecardUpdatedAt)}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
