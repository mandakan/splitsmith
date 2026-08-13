/**
 * ResultsStage - stage playback page (/results/:slug/:stage), also
 * mounted anonymously at /share/:token/results/:slug/:stage.
 *
 * Video + marker scrub bar + stats strip + splits list, synced through
 * one <video> element owned here. shots[].time_absolute and beep_time
 * arrive already in the served clip's coordinate system, so seeking is
 * plain currentTime assignment.
 *
 * Read-only on share mounts only: operator mounts (desktop and mobile)
 * carry the slice-5 interval-reclassify write path (mobile operator
 * surfaces program), deliberately breaking the old blanket read-only
 * contract for this page. isShareView gates the affordance client-side;
 * the server share whitelist is the backstop that actually enforces it.
 */
import { ArrowLeft, ArrowRight, ChevronDown, ChevronLeft, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { CommentPanel } from "@/components/comments/CommentPanel";
import { Snackbar, type SnackState } from "@/components/Snackbar";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { CamPicker } from "@/components/results/CamPicker";
import { ReclassifySheet } from "@/components/results/ReclassifySheet";
import { ResultsPlayer, type FullscreenMode } from "@/components/results/ResultsPlayer";
import { Scorecard } from "@/components/results/Scorecard";
import { SplitsList } from "@/components/results/SplitsList";
import { StageStats } from "@/components/results/StageStats";
import { Kicker } from "@/components/ui";
import {
  ApiError,
  api,
  apiErrorText,
  capabilityDenied,
  type CoachShot,
  type CoachShotPatch,
  type CoachStageResponse,
  type StageScorecard,
} from "@/lib/api";
import { buildUndoPatch } from "@/lib/coachPatch";
import { useMatchHref } from "@/lib/matchHref";
import { momentHref, momentToSearch, parseMoment } from "@/lib/moment";
import { isShareView } from "@/lib/shareView";
import {
  INTERVAL_LABEL,
  type TierBaselines,
  baselinesFromMatchDistributions,
  currentShotIndex,
  statisticSplits,
} from "@/lib/splits";
import { useActiveShare } from "@/lib/useActiveShare";
import { cn } from "@/lib/utils";

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

// Sentence case - display CSS owns any uppercasing.
const PATCH_FAILED_FALLBACK = "Could not save the change - check the connection and retry.";

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
  const { shooters, capabilities } = useOutletContext<MatchShellOutletContext>();
  const location = useLocation();
  // `comment_write` is one capability with two meanings, and the two
  // affordances it drives are not the same affordance (final review,
  // I2/I3). On a share mount it means "may post": the POST handler
  // requires a share_token_id, which only the share middleware sets.
  // On the owner's own mount the very same capability means "may
  // moderate": posting there 404s, but DELETE on anyone's comment
  // succeeds. Reading one flag for both put a dead compose box on the
  // owner's page and left owner delete with no button at all.
  //
  // isShareView is legitimate here precisely because it selects which
  // *affordance* to render, not who is allowed to do what - the server
  // remains the only enforcement (the write allowlist, the scope gate,
  // and the capability check). This is not the SPA re-implementing
  // authorization; a wrong answer here shows or hides a button, it
  // never grants anything.
  const commentWrite = !capabilityDenied(capabilities, "comment_write");
  const shareView = isShareView(location.pathname);
  const canComment = commentWrite && shareView;
  const canModerate = commentWrite && !shareView;
  const [commentAnchors, setCommentAnchors] = useState<number[]>([]);
  const href = useMatchHref();
  const navigate = useNavigate();
  const [coach, setCoach] = useState<CoachStageResponse | null>(null);
  // The audit version the next positional shot PATCH must guard on (#844).
  // A ref, not the ``coach`` state: applyShotPatch hands its own Undo
  // closure to the snack, and that closure outlives the render it was made
  // in - reading ``coach`` there would send the version from *before* the
  // apply, which the apply itself has just superseded.
  const coachVersionRef = useRef<number | undefined>(undefined);
  const [baselines, setBaselines] = useState<TierBaselines | null>(null);
  const [scorecard, setScorecard] = useState<StageScorecard | null>(null);
  const [scorecardUpdatedAt, setScorecardUpdatedAt] = useState<string | null>(null);
  const [trimStale, setTrimStale] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [fsMode, setFsMode] = useState<FullscreenMode>("off");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [playerBox, setPlayerBox] = useState<HTMLDivElement | null>(null);
  const canReclassify = !shareView;
  const { shareUrl } = useActiveShare();
  const [sheetShot, setSheetShot] = useState<CoachShot | null>(null);
  const [patchBusy, setPatchBusy] = useState(false);
  const [snack, setSnack] = useState<SnackState | null>(null);
  const [searchParams] = useSearchParams();
  const moment = useMemo(() => parseMoment(searchParams), [searchParams]);
  const [activeCamIndex, setActiveCamIndex] = useState(0);
  // Position to restore after a camera switch remounts the player.
  const pendingSeekRef = useRef<{ t: number; play: boolean } | null>(null);
  // One-shot: apply a moment link's ?v= once per mount (the page remounts
  // per slug-stage via the key in ResultsStage).
  const appliedMomentCamRef = useRef(false);
  // Mirrors camIndex for handleCopyMoment, which is memoized on other
  // deps - a ref avoids re-memoizing the callback on every camera switch.
  const camIndexRef = useRef(0);
  const momentTime =
    moment != null && coach != null
      ? (coach.videos[coach.videos[activeCamIndex] ? activeCamIndex : 0]?.beep_in_clip ??
          coach.beep_time) + moment.t
      : null;

  // When the match has a live share, copy the share-scoped moment URL
  // instead of the operator one - it works for whoever the owner
  // actually shares it with. Share viewers never reach this branch:
  // useActiveShare returns null by construction on a share mount, so
  // they keep copying their own share-relative URL.
  const handleCopyMoment = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !coach) return;
    const t = Math.round((v.currentTime - coach.beep_time) * 100) / 100;
    const m = { t, ...(camIndexRef.current > 0 ? { v: camIndexRef.current } : {}) };
    const link = shareUrl
      ? `${shareUrl}/results/${slug}/${stage}?${momentToSearch(m).toString()}`
      : `${window.location.origin}${momentHref(location.pathname, m)}`;
    try {
      await navigator.clipboard.writeText(link);
      setSnack({
        message: shareUrl
          ? `Share link copied at ${t.toFixed(2)}s`
          : `Link copied at ${t.toFixed(2)}s`,
        tone: "status",
      });
    } catch {
      setSnack({ message: "Could not copy link", tone: "error" });
    }
  }, [coach, location.pathname, shareUrl, slug, stage]);

  // The only writer of coach state, so the guard value cannot fall out of
  // step with the document it guards. Written here rather than in an effect
  // on ``coach``: an effect lands a commit later, and a second patch fired
  // before that commit would send the version the first one just replaced.
  const applyCoach = useCallback((next: CoachStageResponse | null) => {
    coachVersionRef.current = next?.version;
    setCoach(next);
  }, []);

  // Non-optimistic write, per the desktop Coach precedent: PATCH returns
  // the full CoachStageResponse, which replaces coach state wholesale -
  // no refetch, no local mirror to desync. Undo re-patches the inverse
  // (buildUndoPatch) of exactly the fields the apply touched.
  const applyShotPatch = useCallback(
    async (shot: CoachShot, patch: CoachShotPatch, undoable: boolean) => {
      setPatchBusy(true);
      try {
        // Addressed by id where the shot has one, so a concurrent insert
        // cannot slide this annotation onto the neighbouring shot; the
        // version is only the fallback's guard (#844).
        const updated = await api.patchStageShotCoach(
          slug,
          stage,
          shot,
          patch,
          coachVersionRef.current,
        );
        applyCoach(updated);
        // Stale-close guard: only dismiss the sheet if it still shows the
        // shot this patch was for - a slower in-flight patch resolving
        // after the operator has already reopened the sheet on a
        // different shot must not yank it closed under them.
        setSheetShot((cur) => (cur && cur.shot_number === shot.shot_number ? null : cur));
        if (undoable) {
          const undoPatch = buildUndoPatch(shot, patch);
          setSnack({
            message: patch.interval_class
              ? `Shot ${shot.shot_number} - ${INTERVAL_LABEL[patch.interval_class]}`
              : `Shot ${shot.shot_number} note saved`,
            tone: "status",
            actionLabel: "Undo",
            // Double-tap guard: clear the snack synchronously so a second
            // tap on Undo (before the re-patch round-trip resolves) has
            // no button left to hit.
            onAction: () => {
              setSnack(null);
              void applyShotPatch(shot, undoPatch, false);
            },
          });
        } else {
          setSnack({ message: "Change undone", tone: "status" });
        }
      } catch (e) {
        setSnack({ message: apiErrorText(e, PATCH_FAILED_FALLBACK), tone: "error" });
      } finally {
        setPatchBusy(false);
      }
    },
    [applyCoach, slug, stage],
  );

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
    setTrimStale(false);
    (async () => {
      const [coachResult, projectResult, distResult] = await Promise.allSettled([
        api.getStageCoach(slug, stage),
        api.getProject(slug),
        api.getMatchCoachDistributions(slug),
      ]);
      if (!alive) return;

      if (coachResult.status === "fulfilled") {
        applyCoach(coachResult.value);
        setLoaded(true);
      } else {
        const e = coachResult.reason;
        setError(e instanceof ApiError ? e.detail : String(e));
      }

      // Baselines are a nice-to-have like the scorecard: a failed fetch
      // just means unjudged (chip-less) rows, never an error banner.
      setBaselines(
        distResult.status === "fulfilled"
          ? baselinesFromMatchDistributions(distResult.value)
          : null,
      );

      // Scorecard is a nice-to-have: a failed project fetch just means no
      // scorecard shows, it must never surface through the coach error banner.
      if (projectResult.status === "fulfilled") {
        const stageEntry = projectResult.value.stages.find((s) => s.stage_number === stage);
        setScorecard(stageEntry?.scorecard ?? null);
        setScorecardUpdatedAt(stageEntry?.scorecard_updated_at ?? null);
        setTrimStale(
          (stageEntry?.videos ?? []).some(
            (v) => v.role !== "ignored" && v.beep_time != null && !v.processed.trim,
          ),
        );
      }
    })();
    return () => {
      alive = false;
    };
  }, [applyCoach, slug, stage, attempt]);

  // Moment links can name a non-primary camera via ?v=. Applied once per
  // mount, not on every coach reload - a later PATCH response must not
  // yank the operator back to the linked camera after they have switched.
  useEffect(() => {
    if (!coach || appliedMomentCamRef.current) return;
    appliedMomentCamRef.current = true;
    const v = moment?.v;
    if (
      typeof v === "number" &&
      v < coach.videos.length &&
      coach.videos[v]?.beep_in_clip != null
    ) {
      setActiveCamIndex(v);
    }
  }, [coach, moment]);

  // Restores the preserved playback position after a camera switch
  // remounts ResultsPlayer. Runs after the child's own effects (parent
  // effects fire after child effects on mount), so it lands after
  // ResultsPlayer's seekToWindowStart / moment seek in both the
  // loadedmetadata-listener path and the already-buffered readyState>=1
  // path.
  useEffect(() => {
    const pending = pendingSeekRef.current;
    const el = videoRef.current;
    if (!pending || !el) return;
    pendingSeekRef.current = null;
    const apply = () => {
      el.currentTime = Math.max(0, pending.t);
      if (pending.play) void el.play().catch(() => {});
    };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
  }, [activeCamIndex]);

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
  // Shot times arrive in the primary clip's coordinates; replaying them
  // on another camera shifts them onto that clip's clock via the beep.
  const camDeltaForShots = coach
    ? (coach.videos[coach.videos[activeCamIndex] ? activeCamIndex : 0]?.beep_in_clip ??
        coach.beep_time) - coach.beep_time
    : 0;
  const displayShots = useMemo(
    () =>
      camDeltaForShots === 0
        ? shots
        : shots.map((s) => ({ ...s, time_absolute: s.time_absolute + camDeltaForShots })),
    [shots, camDeltaForShots],
  );
  const activeShotNumber = useMemo(() => {
    const idx = currentShotIndex(displayShots, currentTime);
    return idx >= 0 ? displayShots[idx].shot_number : null;
  }, [displayShots, currentTime]);

  const stageTime = shots.length > 0 ? shots[shots.length - 1].time_from_beep : null;
  // Split statistics count split-classed intervals only - transitions,
  // movement and reloads are dead time, not shooting (issue #772).
  // statisticSplits owns the rule and the unclassified fallback.
  const splits = useMemo(() => statisticSplits(shots), [shots]);
  const fastestSplit = splits.length > 0 ? Math.min(...splits) : null;
  const avgSplit =
    splits.length > 0 ? splits.reduce((sum, s) => sum + s, 0) / splits.length : null;
  // The first shot's split is the draw, whatever its classification.
  const draw = shots.length > 0 ? shots[0].split : null;

  // Clip-absolute seconds - shared by shot rows and comment rows, which
  // both already have their own coordinate math done by the time they
  // call this (shots via time_absolute, comments via beepTime + anchor_t).
  const seekToTime = useCallback((t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = t;
    void v.play().catch(() => {});
  }, []);

  const seekToShot = useCallback(
    (shot: { time_absolute: number }) => seekToTime(shot.time_absolute),
    [seekToTime],
  );

  const handleSelectCam = useCallback(
    (index: number) => {
      setActiveCamIndex((prev) => {
        if (index === prev || !coach) return prev;
        const prevBeep = coach.videos[prev]?.beep_in_clip ?? coach.beep_time;
        const nextBeep = coach.videos[index]?.beep_in_clip;
        if (nextBeep == null) return prev;
        const el = videoRef.current;
        if (el) {
          // Same run moment on the new camera's clock.
          pendingSeekRef.current = { t: el.currentTime - prevBeep + nextBeep, play: !el.paused };
        }
        return index;
      });
    },
    [coach],
  );

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

  // Camera identity is the payload index (primary first). A stale index
  // (coach reloaded with fewer cameras) silently falls back to entry 0;
  // entry 0 also covers the no-primary edge instead of dead-ending.
  const camIndex = coach.videos[activeCamIndex] ? activeCamIndex : 0;
  const activeVideo = coach.videos[camIndex];
  const activeBeep = activeVideo?.beep_in_clip ?? coach.beep_time;
  const camDelta = activeBeep - coach.beep_time;
  camIndexRef.current = camIndex;
  const navButton =
    "inline-flex size-11 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led";

  const header = (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        {/* The only route back to the overview on the bare share
            surface; on the owner surface it complements the shell nav.
            href round-trips the /share/:token prefix. */}
        <Link
          to={href("results")}
          className="mb-1 inline-flex items-center gap-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        >
          <ChevronLeft className="size-3.5" aria-hidden />
          All stages
        </Link>
        <div className="flex flex-wrap items-center gap-x-2">
          <h1 className="truncate font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink md:text-2xl">
            <span className="text-led">Stage {pad2(stage)}</span>
            {coach?.stage_name ? <span className="text-ink"> - {coach.stage_name}</span> : null}
          </h1>
          {trimStale ? (
            <span
              role="status"
              className="ml-2 inline-flex min-h-6 items-center rounded border border-rule px-2 text-xs text-muted"
            >
              Awaiting desktop re-process
            </span>
          ) : null}
        </div>
        {shooter ? (
          shooters.length > 1 ? (
            // Minimal shooter switcher: the name line itself is a
            // native select (OS picker on mobile), one caret of added
            // chrome. Shooters without an audited take of this stage
            // are disabled - the same contract the overview links use.
            <span className="relative inline-flex max-w-full items-center">
              <select
                value={slug}
                onChange={(e) => navigate(href("results", e.target.value, String(stage)))}
                aria-label="Shooter"
                className="cursor-pointer appearance-none truncate bg-transparent pr-4 font-mono text-xs uppercase tracking-[0.08em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                {shooters.map((s) => (
                  <option
                    key={s.slug}
                    value={s.slug}
                    disabled={
                      !s.stage_statuses.some(
                        (e) => e.stage_number === stage && e.status === "audited",
                      )
                    }
                  >
                    {s.name}
                  </option>
                ))}
              </select>
              <ChevronDown
                aria-hidden
                className="pointer-events-none absolute right-0 size-3 text-subtle"
              />
            </span>
          ) : (
            <p className="truncate font-mono text-xs uppercase tracking-[0.08em] text-muted">
              {shooter.name}
            </p>
          )
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {/* Compare is a desktop-only workflow (#700); the link works
            owner- and share-side via useMatchHref, hidden on phones the
            same way DesktopGate would reject the mount anyway. */}
        <Link
          to={href("compare", String(stage))}
          className="hidden min-h-11 items-center rounded-md border border-rule-strong bg-surface-2 px-4 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led md:inline-flex"
        >
          Compare shooters
        </Link>
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

  if (!activeVideo) {
    return (
      <div className="flex flex-col gap-4 px-4 py-4 md:px-7">
        {header}
        <div className="rounded-md border border-rule-strong bg-surface-2 px-4 py-6 text-center text-sm text-muted">
          No video for this stage.
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
          key={camIndex}
          src={api.videoStreamUrl(slug, activeVideo.path)}
          beepTime={coach.beep_time + camDelta}
          shots={displayShots}
          videoRef={videoRef}
          onTimeChange={setCurrentTime}
          onPlayingChange={setIsPlaying}
          onFullscreenChange={setFsMode}
          baselines={baselines}
          momentTime={momentTime}
          onCopyMoment={handleCopyMoment}
          commentTimes={commentAnchors.map((t) => coach.beep_time + camDelta + t)}
        />
        <CamPicker
          entries={coach.videos}
          activeIndex={camIndex}
          onSelect={handleSelectCam}
          srcFor={(e) => api.videoStreamUrl(slug, e.path)}
        />
      </div>
      <div className="flex flex-col gap-4 lg:max-h-[calc(100dvh-var(--shell-header-h,86px)-2rem)] lg:overflow-y-auto">
        <StageStats
          stageTime={stageTime}
          shotCount={shots.length}
          draw={draw}
          fastestSplit={fastestSplit}
          avgSplit={avgSplit}
        />
        <SplitsList
          shots={displayShots}
          activeShotNumber={activeShotNumber}
          onSeek={seekToShot}
          isPlaying={isPlaying}
          baselines={baselines}
          onReclassify={canReclassify ? setSheetShot : undefined}
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
        <CommentPanel
          slug={slug}
          stage={stage}
          shots={shots}
          // The active camera's beep: anchor_t stays beep-relative (and so
          // camera-independent) only if capture and seek both use the beep
          // of the clip currentTime is measured in.
          beepTime={coach.beep_time + camDelta}
          currentTime={currentTime}
          canComment={canComment}
          canModerate={canModerate}
          onSeek={seekToTime}
          onAnchorsChange={setCommentAnchors}
        />
      </div>
      <ReclassifySheet
        key={sheetShot?.shot_number ?? "closed"}
        shot={sheetShot}
        busy={patchBusy}
        onApply={(shot, patch) => void applyShotPatch(shot, patch, true)}
        onCancel={() => setSheetShot(null)}
      />
      <Snackbar snack={snack} onDismiss={() => setSnack(null)} />
    </div>
  );
}
