/**
 * ResultsStage - stage playback page (/results/:slug/:stage), also
 * mounted anonymously at /share/:token/results/:slug/:stage.
 *
 * Video + marker scrub bar + stats strip + splits list, synced through
 * one <video> element owned here. shots[].time_absolute and beep_time
 * arrive already in the served clip's coordinate system, so seeking is
 * plain currentTime assignment.
 *
 * A stage's other cameras are a PiP inset over the player (PipView,
 * #1408): click it or C / Shift+C to make another camera big. The player
 * changes its source in place (position and play state carry over by the
 * beep); the primary's audio plays whichever camera is big.
 *
 * Read-only on share mounts only: operator mounts (desktop and mobile)
 * carry the slice-5 interval-reclassify write path (mobile operator
 * surfaces program), deliberately breaking the old blanket read-only
 * contract for this page. isShareView gates the affordance client-side;
 * the server share whitelist is the backstop that actually enforces it.
 */
import { ArrowLeft, ArrowRight, ChevronDown, ListVideo, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { ReclassifySheet } from "@/components/results/ReclassifySheet";
import { ResultsPlayer, type FullscreenMode } from "@/components/results/ResultsPlayer";
import { Scorecard } from "@/components/results/Scorecard";
import { ShareDialog } from "@/components/results/ShareDialog";
import { SplitsList } from "@/components/results/SplitsList";
import { StageStats } from "@/components/results/StageStats";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { PipView } from "@/components/video/PipView";
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
import { useDeploymentMode } from "@/lib/features";
import { useMatchHref } from "@/lib/matchHref";
import { camsParam, parseCams, selectorFor, startingCamera, withCams } from "@/lib/cameraPrefs";
import { momentHref, momentToSearch, parseMoment, type Moment } from "@/lib/moment";
import { insetStream, pipKeyAction, type InsetStreamKind, type PipCamera } from "@/lib/pip";
import { attachInsetSync } from "@/lib/pipSync";
import { usePip } from "@/lib/usePip";
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
import { Avatar } from "@/components/ui/AvatarStack";
import { identityMark } from "@/lib/identityMark";

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

// Sentence case - display CSS owns any uppercasing.
const PATCH_FAILED_FALLBACK = "Could not save the change - check the connection and retry.";

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

// The beep anchor of the clip the SPA plays for this camera; falls back
// to the primary anchor when the entry is missing or beepless.
function camBeep(coach: CoachStageResponse, index: number): number {
  return coach.videos[index]?.beep_in_clip ?? coach.beep_time;
}

// The camera the page opens big, once per mount: a moment link's ?v= names
// one exactly (a payload index, primary first); otherwise the camera chosen
// on an earlier stage (?cams=, a mount or role, lib/cameraPrefs), else the
// shooter's saved default. ``null`` is the primary.
function startCameraId(
  coach: CoachStageResponse,
  moment: Moment | null,
  camsQuery: string | null,
  slug: string,
): string | null {
  const v = moment?.v;
  if (typeof v === "number" && coach.videos[v]?.beep_in_clip != null) return coach.videos[v].path;
  const start = startingCamera(coach.videos, parseCams(camsQuery)[slug], coach.compare_camera);
  return start > 0 ? (coach.videos[start]?.path ?? null) : null;
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
  // The same element as state, for what must re-bind when it changes
  // (PipView's inset sync, the primary's audio).
  const [bigEl, setBigEl] = useState<HTMLVideoElement | null>(null);
  const [fsMode, setFsMode] = useState<FullscreenMode>("off");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [playerBox, setPlayerBox] = useState<HTMLDivElement | null>(null);
  const canReclassify = !shareView;
  // Share is the hosted owner's: the same dialog Splits opens (you share
  // what you look at). Local installs deep-link from Splits instead.
  const { mode: deploymentMode } = useDeploymentMode();
  const canShare = deploymentMode === "hosted" && !shareView;
  const [showShare, setShowShare] = useState(false);
  const { shareUrl } = useActiveShare();
  const [sheetShot, setSheetShot] = useState<CoachShot | null>(null);
  const [patchBusy, setPatchBusy] = useState(false);
  const [snack, setSnack] = useState<SnackState | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const moment = useMemo(() => parseMoment(searchParams), [searchParams]);
  // Play all: one shooter's audited stages back to back. The flag lives
  // in the URL (``?play=all``) so a share link can carry it; the
  // autoplay bit does NOT - it rides on navigation state, set only by
  // this page's own advance, so a play-all link opened cold still
  // waits for a press of Play (which the browser would demand anyway).
  const playAll = searchParams.get("play") === "all";
  // Read once per mount: a camera swap changes the player's source in
  // place, so the player's own once-per-mount autoplay never fires again.
  const [autoplayArmed] = useState(
    () => playAll && Boolean((location.state as { autoplay?: boolean } | null)?.autoplay),
  );
  const togglePlayAll = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (next.get("play") === "all") next.delete("play");
        else next.set("play", "all");
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);
  const camsQuery = searchParams.get("cams");

  // The starting camera, latched the first time the coach payload is in
  // (the page remounts per stage), not on every coach reload - a later
  // PATCH response must not yank the viewer back after a swap. Set during
  // render so the player's first frame is already the right camera.
  const [start, setStart] = useState<{ id: string | null } | null>(null);
  if (coach && start === null) setStart({ id: startCameraId(coach, moment, camsQuery, slug) });

  // Stream kinds that failed per camera path, for the inset's fallback
  // (rendition, then the trim) and the primary's audio.
  const [failedStreams, setFailedStreams] = useState<Record<string, ReadonlySet<InsetStreamKind>>>({});
  const addFailed = useCallback((path: string, kind: InsetStreamKind | null) => {
    if (!kind) return;
    setFailedStreams((prev) => {
      if (prev[path]?.has(kind)) return prev;
      return { ...prev, [path]: new Set([...(prev[path] ?? []), kind]) };
    });
  }, []);

  // Every camera as PipView sees it. The id is the video path; the inset
  // streams the 720p rendition when there is one (insetStream), through
  // the same scoped stream URL the big player uses (the share alias on a
  // share mount). A camera without a beep never enters the inset.
  const pipCams = useMemo(() => {
    if (!coach) return { cameras: [] as PipCamera[], kinds: new Map<string, InsetStreamKind>() };
    const kinds = new Map<string, InsetStreamKind>();
    const cameras = coach.videos.map((e, i): PipCamera => {
      const s = insetStream(e, failedStreams[e.path]);
      if (s) kinds.set(e.path, s.kind);
      return {
        id: e.path,
        label: `Cam ${i + 1}`,
        primary: e.role === "primary",
        beepInClip: e.beep_in_clip ?? (i === 0 ? coach.beep_time : null),
        src: s ? api.videoStreamUrl(slug, e.path, s.kind, s.version, stage) : null,
        unavailable: s === null,
      };
    });
    return { cameras, kinds };
  }, [coach, failedStreams, slug, stage]);

  // A swap holds on the next stages: kept as a selector in ?cams=.
  const onBigChange = useCallback(
    (cam: PipCamera | null) => {
      if (!coach || !cam) return;
      const index = coach.videos.findIndex((v) => v.path === cam.id);
      if (index < 0 || coach.videos[index].beep_in_clip == null) return;
      const sel = selectorFor(coach.videos, index) ?? "primary";
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          const cams = camsParam({ ...parseCams(prev.get("cams")), [slug]: sel });
          if (cams) out.set("cams", cams);
          return out;
        },
        { replace: true },
      );
    },
    [coach, slug, setSearchParams],
  );
  const pip = usePip({
    cameras: pipCams.cameras,
    stageKey: `${slug}/${stage}`,
    start: start?.id ?? null,
    onBigChange,
  });

  // The big camera's payload index (primary first); 0 while there is none.
  const bigIndex = coach && pip.big ? Math.max(0, coach.videos.findIndex((v) => v.path === pip.big?.id)) : 0;
  // Mirror bigIndex / its beep for handleCopyMoment, which is memoized on
  // other deps: t is encoded against the *big* camera's beep (mirroring
  // how momentTime decodes it), not always the primary's coach.beep_time.
  const camIndexRef = useRef(0);
  const activeBeepRef = useRef(0);
  const momentTime = moment != null && coach != null ? camBeep(coach, bigIndex) + moment.t : null;

  // C / Shift+C cycles the inset (with two cameras: a swap). Never while
  // typing (the comment box) - pipKeyAction owns that rule.
  const hasInset = pip.inset != null;
  const cyclePip = pip.cycle;
  useEffect(() => {
    if (!hasInset) return;
    const onKey = (e: KeyboardEvent) => {
      const dir = pipKeyAction(e);
      if (!dir) return;
      e.preventDefault();
      cyclePip(dir);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasInset, cyclePip]);

  // The primary is the audio source whichever camera is big (epic #1405):
  // with a secondary big, the big player is muted and a hidden <audio> of
  // the primary follows it on the beep clock (the inset's sync rules).
  const primaryCam = pip.primary;
  const audioSwap =
    pip.big != null && primaryCam != null && pip.big.id !== primaryCam.id && primaryCam.src != null
      ? primaryCam
      : null;
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);
  const bigBeepInClip = pip.big?.beepInClip ?? null;
  const primaryBeepInClip = audioSwap?.beepInClip ?? null;
  useEffect(() => {
    if (!bigEl) return;
    if (!audioEl || bigBeepInClip == null || primaryBeepInClip == null) {
      bigEl.muted = false;
      return;
    }
    const detach = attachInsetSync(bigEl, audioEl, { bigBeep: bigBeepInClip, insetBeep: primaryBeepInClip });
    audioEl.muted = false;
    bigEl.muted = true;
    return () => {
      detach();
      audioEl.pause();
      bigEl.muted = false;
    };
  }, [bigEl, audioEl, bigBeepInClip, primaryBeepInClip]);

  // When the match has a live share, copy the share-scoped moment URL
  // instead of the operator one - it works for whoever the owner
  // actually shares it with. Share viewers never reach this branch:
  // useActiveShare returns null by construction on a share mount, so
  // they keep copying their own share-relative URL.
  const handleCopyMoment = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !coach) return;
    const t = Math.round((v.currentTime - activeBeepRef.current) * 100) / 100;
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

  // Window end under play-all: go to the next audited stage with the
  // flag kept and autoplay armed; on the last stage the player has
  // already paused at the tail and there is nothing left to do.
  const handleWindowEnd = useCallback(() => {
    if (nextStage == null) return;
    navigate(withCams(`${href("results", slug, String(nextStage))}?play=all`, camsQuery), {
      state: { autoplay: true },
    });
  }, [navigate, href, slug, nextStage, camsQuery]);

  const shots = useMemo(() => coach?.shots ?? [], [coach]);
  // Shot times arrive in the primary clip's coordinates; replaying them
  // on another camera shifts them onto that clip's clock via the beep.
  const camDeltaForShots = coach ? camBeep(coach, bigIndex) - coach.beep_time : 0;
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

  if (error) {
    return (
      <div className="px-4 py-8 md:px-7">
        <p role="alert" className="text-sm text-led-text">
          {error}
        </p>
        <Button type="button" className="mt-3" onClick={() => setAttempt((n) => n + 1)}>
          Retry
        </Button>
      </div>
    );
  }
  if (!loaded) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage...
      </div>
    );
  }
  if (!coach) {
    return (
      <div className="px-4 py-4 md:px-7">
        <PageHeader ordinal={pad2(stage)} title="Stage" back={{ label: "All stages", to: href("results") }} />
        <p className="text-md text-muted">Stage not audited yet.</p>
        <Button asChild className="mt-4">
          <Link to={href("results")}>Back to results</Link>
        </Button>
      </div>
    );
  }

  const activeVideo = coach.videos[bigIndex];
  const activeBeep = camBeep(coach, bigIndex);
  const camDelta = activeBeep - coach.beep_time;
  camIndexRef.current = bigIndex;
  activeBeepRef.current = activeBeep;
  const insetCam = pip.inset;
  const shooterLine = shooter ? (
    shooters.length > 1 ? (
      // Minimal shooter switcher: the name line itself is a native
      // select (OS picker on mobile), one caret of added chrome.
      // Shooters without an audited take of this stage are disabled -
      // the same contract the Splits links use.
      <span className="relative inline-flex max-w-full items-center">
        <select
          value={slug}
          onChange={(e) => navigate(withCams(href("results", e.target.value, String(stage)), camsQuery))}
          aria-label="Shooter"
          className="cursor-pointer appearance-none truncate bg-transparent pr-4 text-md text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        >
          {shooters.map((s) => (
            <option
              key={s.slug}
              value={s.slug}
              disabled={!s.stage_statuses.some((e) => e.stage_number === stage && e.status === "audited")}
            >
              {s.name}
            </option>
          ))}
        </select>
        <ChevronDown aria-hidden className="pointer-events-none absolute right-0 size-3 text-subtle" />
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5">
        {shooter.identity?.accent || shooter.identity?.logo ? (
          <Avatar size="xs" initials={shooter.name.slice(0, 2)} seed={shooter.slug} name={shooter.name} {...identityMark(shooter.slug, shooter.identity)} />
        ) : null}
        {shooter.name}
      </span>
    )
  ) : null;

  const stepButton = (label: string, to: number | null, icon: ReactNode) =>
    to != null ? (
      <Button asChild size="icon" aria-label={label}>
        <Link to={withCams(href("results", slug, String(to)), camsQuery)}>{icon}</Link>
      </Button>
    ) : (
      <Button type="button" size="icon" disabled aria-label={label}>
        {icon}
      </Button>
    );

  // The only route back to the overview on the bare share surface; on
  // the owner surface it complements the shell nav. href round-trips
  // the /share/:token prefix.
  const header = (
    <PageHeader
      ordinal={pad2(stage)}
      title={coach.stage_name || "Stage"}
      back={{ label: "All stages", to: href("results") }}
      sub={
        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
          {shooterLine}
          {trimStale ? (
            <Chip tone="warn" role="status">
              Awaiting desktop re-process
            </Chip>
          ) : null}
        </span>
      }
      actions={
        <>
          {canShare ? (
            <Button type="button" onClick={() => setShowShare(true)} aria-label="Manage share links for these results">
              Share
            </Button>
          ) : null}
          {/* Compare is a desktop-only workflow (#700); the link works
              owner- and share-side via useMatchHref, hidden on phones the
              same way DesktopGate would reject the mount anyway. */}
          {shooters.length > 1 ? (
            <Button asChild className="hidden md:inline-flex">
              <Link to={withCams(href("compare", String(stage)), camsQuery)}>Compare shooters</Link>
            </Button>
          ) : null}
          {/* Live state is the page's one primary while it is on. */}
          <Button
            type="button"
            variant={playAll ? "primary" : "default"}
            onClick={togglePlayAll}
            aria-pressed={playAll}
            aria-label={playAll ? "Play all stages: on" : "Play all stages: off"}
            title="Play this shooter's stages back to back"
          >
            <ListVideo className="size-4" aria-hidden="true" />
            Play all
          </Button>
          {stepButton("Previous stage", prevStage, <ArrowLeft className="size-4" />)}
          {stepButton("Next stage", nextStage, <ArrowRight className="size-4" />)}
        </>
      }
    />
  );

  const legend = baselines ? (
    <div className="flex flex-wrap items-center gap-4 px-1 py-2 text-sm text-muted">
      <span>
        <i aria-hidden className="mr-1.5 inline-block size-2 rounded-full bg-done" />
        Quick
      </span>
      <span>
        <i aria-hidden className="mr-1.5 inline-block size-2 rounded-full bg-ink-2" />
        Typical
      </span>
      <span>
        <i aria-hidden className="mr-1.5 inline-block size-2 rounded-full bg-live" />
        Long
      </span>
      <span className="ml-auto">Against your match baseline, per interval class</span>
    </div>
  ) : null;

  if (!activeVideo) {
    return (
      <div className="px-4 py-4 md:px-7">
        {header}
        <p className="text-md text-muted">No video for this stage.</p>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className="flex flex-col gap-4 px-4 py-4 md:px-7 lg:grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start"
    >
      <div className="lg:col-span-2">
        {header}
        {/* Leading and full-width (spec s4.5): five cells need the whole
            row; in the side column their labels wrap. */}
        <StageStats
          stageTime={stageTime}
          shotCount={shots.length}
          draw={draw}
          fastestSplit={fastestSplit}
          avgSplit={avgSplit}
        />
      </div>
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
          src={api.videoStreamUrl(slug, activeVideo.path, activeVideo.kind, null, stage)}
          beepTime={coach.beep_time + camDelta}
          shots={displayShots}
          videoRef={videoRef}
          onVideoElement={setBigEl}
          overlay={
            pipCams.cameras.length > 1 ? (
              <PipView
                pip={pip}
                bigVideo={bigEl}
                insetKind={insetCam ? (pipCams.kinds.get(insetCam.id) ?? null) : null}
                onInsetError={(cam, kind) => addFailed(cam.id, kind)}
              />
            ) : null
          }
          onTimeChange={setCurrentTime}
          onPlayingChange={setIsPlaying}
          onFullscreenChange={setFsMode}
          baselines={baselines}
          momentTime={momentTime}
          onCopyMoment={handleCopyMoment}
          commentTimes={commentAnchors.map((t) => coach.beep_time + camDelta + t)}
          onWindowEnd={playAll ? handleWindowEnd : undefined}
          autoplay={autoplayArmed}
        />
        {audioSwap ? (
          // The primary's audio while a secondary is big (see above).
          <audio
            key={audioSwap.src ?? ""}
            ref={setAudioEl}
            src={audioSwap.src ?? undefined}
            preload="auto"
            aria-hidden
            className="hidden"
            onError={() => addFailed(audioSwap.id, pipCams.kinds.get(audioSwap.id) ?? null)}
          />
        ) : null}
        {legend}
      </div>
      <div className="flex flex-col gap-4 lg:max-h-[calc(100dvh-var(--shell-header-h,86px)-2rem)] lg:overflow-y-auto">
        <SplitsList
          shots={displayShots}
          activeShotNumber={activeShotNumber}
          onSeek={seekToShot}
          isPlaying={isPlaying}
          baselines={baselines}
          onReclassify={canReclassify ? setSheetShot : undefined}
        />
        <Scorecard scorecard={scorecard} updatedAt={scorecardUpdatedAt} />
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
      {canShare && showShare ? <ShareDialog onClose={() => setShowShare(false)} /> : null}
    </div>
  );
}
