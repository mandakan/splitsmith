/**
 * useStageWorkspace -- the per-stage editing state Coach and Breakdown
 * share (#1371): the project and coach payload, the match distributions
 * and tier baselines, the band's peaks, the region hook (``useStageEvents``),
 * the shot PATCH with its version guard, Reclassify, the video element and
 * its clock, and the active shot. Every coach response goes through the
 * region hook's ``apply``, so the regions' revision and the shot PATCH's
 * guard both move with the document they guard (#844, spec 2026-10-08).
 *
 * The derived values a page draws from (stream URL, stage time, beep-relative
 * playhead, read-only lanes, the selected region, neighbouring stages) come
 * from ``deriveStageView`` once both payloads have loaded.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  capabilityDenied,
  type CoachMatchDistributions,
  type CoachShot,
  type CoachStageResponse,
  type CoachVideoEntry,
  type MatchProject,
  type PeaksResult,
  type StageEvent,
} from "@/lib/api";
import { shotAtOrBefore } from "@/lib/coachReview";
import { useSpacePlayPause } from "@/lib/keyboard";
import { type TierBaselines, baselinesFromMatchDistributions } from "@/lib/splits";
import { parseStageLink, resolveStageLink } from "@/lib/stageLink";
import { useIsMobile } from "@/lib/useIsMobile";
import { useScrubSource } from "@/lib/useScrubSource";
import { useStageEvents, type StageEvents } from "@/lib/useStageEvents";

/** Peaks bins for the band's audio track; same request shape as Audit's
 *  "Load peaks" effect, a wider bin count since stages run longer. */
export const PEAK_BINS = 4000;

export type ShotPatch = Parameters<typeof api.patchStageShotCoach>[3];

export interface StageWorkspace {
  project: MatchProject | null;
  coach: CoachStageResponse | null;
  distributions: CoachMatchDistributions | null;
  baselines: TierBaselines | null;
  peaks: PeaksResult | null;
  peaksLoading: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  regions: StageEvents;
  scrub: ReturnType<typeof useScrubSource>;
  videoRef: React.MutableRefObject<HTMLVideoElement | null>;
  currentTime: number;
  setCurrentTime: (t: number) => void;
  isPlaying: boolean;
  setIsPlaying: (playing: boolean) => void;
  activeShotNumber: number | null;
  setActiveShotNumber: (n: number | null) => void;
  reclassifying: boolean;
  reclassify: () => Promise<void>;
  patchShot: (shot: CoachShot, patch: ShotPatch) => Promise<void>;
  /** ``patchShot`` that rethrows instead of replacing the page: an autosaved
   *  note reports its own failure (``useNoteAutosave``). */
  savePatch: (shot: CoachShot, patch: ShotPatch) => Promise<void>;
  /** Write the stage note with the latest payload's revision (#1376);
   *  rethrows, a 409 included. */
  saveStageNote: (text: string) => Promise<void>;
  /** Fetch the coach payload again and apply it (after a conflict). */
  reload: () => Promise<CoachStageResponse | null>;
  /** Select a shot and seek the video to it; drops a selected region. */
  seekToShot: (shot: CoachShot) => void;
  /** Seek to seconds from the beep; ``shotNumber`` (else the shot the
   *  playhead has passed) becomes the current shot. */
  seekToTime: (tFromBeep: number, shotNumber?: number | null) => void;
  togglePlay: () => void;
  /** The video's metadata loaded: a deep link's pending seek lands. */
  onVideoReady: () => void;
  /** True while a shot's note has focus: playback stops advancing the current shot. */
  holdActiveShot: (held: boolean) => void;
  isMobile: boolean;
}

export interface StageWorkspaceOptions {
  /** After every region save the server accepted (Breakdown refreshes the
   *  shell's project so the nav's region count follows). */
  onRegionsSaved?: () => void;
  /** Fetch the band's audio peaks (default true). Coach draws no band. */
  peaks?: boolean;
  /** The page URL's query (``?t=&shot=&region=``, #1377), read once on load. */
  link?: string;
}

export function useStageWorkspace(slug: string, stage: number, options: StageWorkspaceOptions = {}): StageWorkspace {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [coach, setCoach] = useState<CoachStageResponse | null>(null);
  const [baselines, setBaselines] = useState<TierBaselines | null>(null);
  const [distributions, setDistributions] = useState<CoachMatchDistributions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reclassifying, setReclassifying] = useState(false);
  const [activeShotNumber, setActiveShotNumber] = useState<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [activeShotHeld, holdActiveShot] = useState(false);
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const wantPeaks = options.peaks ?? true;
  const [peaksLoading, setPeaksLoading] = useState(wantPeaks);
  const scrub = useScrubSource();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  // Guard value for the positional shot PATCH (#844). A ref rather than
  // reading ``coach``: patchShot is memoised on [slug, stage], so the
  // ``coach`` it closes over is the one from the render that created it -
  // null on mount, and stale after every patch that follows.
  const coachVersionRef = useRef<number | undefined>(undefined);
  // The latest payload, for callbacks memoised on [slug, stage] (seekToTime,
  // the stage note's revision).
  const coachRef = useRef<CoachStageResponse | null>(null);
  // A deep link's seek (#1377), in clip seconds, held until the video can take it.
  const pendingSeekRef = useRef<number | null>(null);
  const linkRef = useRef(options.link ?? "");

  // The only writer of coach state, so the guard value cannot fall out of
  // step with the document it guards. Written here rather than in an effect
  // on ``coach``: an effect lands a commit later, and a second patch fired
  // before that commit would send the version the first one just replaced.
  const applyCoach = useCallback((next: CoachStageResponse | null) => {
    coachVersionRef.current = next?.version;
    coachRef.current = next;
    setCoach(next);
  }, []);
  // Regions (spec 2026-10-08): ``apply`` wraps applyCoach and is what every
  // coach response goes through, so the events' revision moves with it. A
  // failed region save is ``regions.issue``, shown under the lane editor: it
  // never replaces the page the way a load or shot PATCH failure does.
  const regions = useStageEvents(slug, stage, applyCoach, undefined, undefined, options.onRegionsSaved);
  const { apply, select: selectEvent } = regions;
  const isMobile = useIsMobile();

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [p, c, dist] = await Promise.all([
          api.getProject(slug),
          api.getStageCoach(slug, stage),
          // Match-scope baseline for the tier chips; a failed fetch just
          // means unjudged rows, never an error.
          api.getMatchCoachDistributions(slug).catch(() => null),
        ]);
        if (!alive) return;
        setProject(p);
        apply(c);
        setBaselines(baselinesFromMatchDistributions(dist));
        setDistributions(dist);
        // A deep link (#1377) opens at its time with its shot and region;
        // a stale or malformed parameter is ignored.
        const link = c ? resolveStageLink(parseStageLink(linkRef.current), c.shots, c.events ?? []) : null;
        if (link?.shot != null) {
          setActiveShotNumber(link.shot);
        } else if (c && c.shots.length > 0 && link?.t == null) {
          setActiveShotNumber(c.shots[0].shot_number);
        }
        if (link?.region != null) selectEvent(link.region);
        if (c && link?.t != null) {
          const clip = c.beep_time + link.t;
          pendingSeekRef.current = clip;
          setCurrentTime(clip);
          if (videoRef.current) videoRef.current.currentTime = clip;
        }
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [apply, selectEvent, slug, stage]);

  // The video mounts after the payload: a linked seek lands once it can.
  // With none pending, a new source (the scrub rendition failed over to the
  // trim) takes the position the old one had, rather than starting at 0.
  const positionRef = useRef(0);
  positionRef.current = currentTime;
  const onVideoReady = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const clip = pendingSeekRef.current ?? (positionRef.current > 0 ? positionRef.current : null);
    pendingSeekRef.current = null;
    if (clip != null && Math.abs(v.currentTime - clip) > 1e-3) v.currentTime = clip;
  }, []);

  // Load peaks for the band's audio track (same shape as Audit.tsx's "Load
  // peaks" effect). A failure just means no waveform -- the track renders
  // "No audio" for null peaks once the request has settled -- never a page
  // error. peaksLoading keeps the track an empty placeholder while the
  // request is in flight, so "No audio" never flashes before a slow
  // response has had a chance to resolve.
  useEffect(() => {
    if (!wantPeaks) return;
    let alive = true;
    setPeaksLoading(true);
    api
      .getStagePeaks(slug, stage, PEAK_BINS)
      .then((p) => {
        if (alive) setPeaks(p);
      })
      .catch(() => {
        if (alive) setPeaks(null);
      })
      .finally(() => {
        if (alive) setPeaksLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [slug, stage, wantPeaks]);

  // While the video is playing, advance the active shot to whichever
  // one's time_absolute has just passed under the playhead. Gated on
  // isPlaying so a click + seek doesn't fight with the tick that follows.
  // Held while a shot's note has focus (``holdActiveShot``): playback must
  // not unmount the textarea under the cursor.
  useEffect(() => {
    if (!isPlaying || !coach || activeShotHeld) return;
    const ordered = [...coach.shots].sort((a, b) => a.time_absolute - b.time_absolute);
    let current: CoachShot | null = null;
    for (const s of ordered) {
      if (s.time_absolute <= currentTime) current = s;
      else break;
    }
    if (current && current.shot_number !== activeShotNumber) {
      setActiveShotNumber(current.shot_number);
    }
  }, [currentTime, isPlaying, coach, activeShotNumber, activeShotHeld]);

  const reclassify = useCallback(async () => {
    setReclassifying(true);
    try {
      const c = await api.reclassifyStageCoach(slug, stage);
      apply(c);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setReclassifying(false);
    }
  }, [apply, slug, stage]);

  const savePatch = useCallback(
    async (shot: CoachShot, patch: ShotPatch) => {
      const c = await api.patchStageShotCoach(slug, stage, shot, patch, coachVersionRef.current);
      apply(c);
    },
    [apply, slug, stage],
  );

  const patchShot = useCallback(
    async (shot: CoachShot, patch: ShotPatch) => {
      try {
        await savePatch(shot, patch);
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      }
    },
    [savePatch],
  );

  const saveStageNote = useCallback(
    async (text: string) => {
      const c = await api.patchStageNote(slug, stage, text, coachRef.current?._version);
      apply(c);
    },
    [apply, slug, stage],
  );

  const reload = useCallback(async () => {
    const c = await api.getStageCoach(slug, stage);
    apply(c);
    return c;
  }, [apply, slug, stage]);

  const seekToShot = useCallback(
    (shot: CoachShot) => {
      // Picking a shot brings its editor back in place of the region card.
      selectEvent(null);
      setActiveShotNumber(shot.shot_number);
      if (videoRef.current) videoRef.current.currentTime = shot.time_absolute;
    },
    [selectEvent],
  );

  const seekToTime = useCallback(
    (tFromBeep: number, shotNumber: number | null = null) => {
      const c = coachRef.current;
      if (!c) return;
      selectEvent(null);
      // A raw time makes the shot the playhead has passed the current one.
      setActiveShotNumber(shotNumber ?? shotAtOrBefore(c.shots, tFromBeep));
      const clip = c.beep_time + tFromBeep;
      setCurrentTime(clip);
      if (videoRef.current) videoRef.current.currentTime = clip;
    },
    [selectEvent],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  }, []);
  // Space toggles play/pause from anywhere on the page (focus on a shot
  // row, nav link, etc) so the user doesn't have to click the video first.
  useSpacePlayPause(togglePlay);

  return {
    project,
    coach,
    distributions,
    baselines,
    peaks,
    peaksLoading,
    error,
    setError,
    regions,
    scrub,
    videoRef,
    currentTime,
    setCurrentTime,
    isPlaying,
    setIsPlaying,
    activeShotNumber,
    setActiveShotNumber,
    reclassifying,
    reclassify,
    patchShot,
    savePatch,
    saveStageNote,
    reload,
    seekToShot,
    seekToTime,
    togglePlay,
    onVideoReady,
    holdActiveShot,
    isMobile,
  };
}

export interface StageView {
  activeShot: CoachShot | null;
  primary: CoachVideoEntry | null;
  streamUrl: string | null;
  /** The stream is the scrub rendition: an error marks it failed so the next render falls back. */
  playingScrub: boolean;
  /** Seconds from the beep the band spans: the stage time, else the last shot plus one. */
  stageTime: number;
  /** The playhead in seconds from the beep. */
  tFromBeep: number;
  /** The audit audio's beep in its own clip, else the coach anchor. */
  audioBeep: number;
  /** Regions are desktop-owned: a mirror (no edit capability) and the phone read them. */
  eventsReadOnly: boolean;
  selectedEvent: StageEvent | null;
  prevStage: number | null;
  nextStage: number | null;
}

/** Everything a stage page draws that follows from the two payloads. */
export function deriveStageView(ws: StageWorkspace, slug: string, stage: number): StageView | null {
  const { coach, project } = ws;
  if (!coach || !project) return null;
  const allStages = project.stages.map((s) => s.stage_number).sort((a, b) => a - b);
  const idx = allStages.indexOf(stage);
  const prevStage = idx > 0 ? allStages[idx - 1] : null;
  const nextStage = idx >= 0 && idx < allStages.length - 1 ? allStages[idx + 1] : null;
  const activeShot = coach.shots.find((s) => s.shot_number === ws.activeShotNumber) ?? null;
  const primary = coach.videos.find((v) => v.role === "primary") ?? null;
  const scrubChoice = primary?.kind === "trim" ? ws.scrub.choose(primary) : null;
  const playingScrub = scrubChoice?.kind === "scrub";
  const streamUrl = primary
    ? primary.kind !== "trim"
      ? api.videoStreamUrl(slug, primary.path, primary.kind, null, stage)
      : api.videoStreamUrl(slug, primary.path, scrubChoice!.kind, scrubChoice!.version, stage)
    : null;
  const eventsReadOnly = ws.isMobile || capabilityDenied(project.capabilities, "edit");
  const { events, selectedId } = ws.regions;
  const selectedEvent = eventsReadOnly ? null : (events.find((e) => e.id === selectedId) ?? null);
  const stageSeconds = project.stages.find((s) => s.stage_number === stage)?.time_seconds ?? 0;
  const lastShot = coach.shots.length > 0 ? Math.max(...coach.shots.map((s) => s.time_from_beep)) : 0;
  const stageTime = stageSeconds > 0 ? stageSeconds : lastShot + 1;
  // coach.beep_time is the coach response's own clip anchor (the same
  // trim-vs-source anchor the video stream uses); peaks.beep_time is the
  // audit audio's beep in its own clip. The two agree in the normal case
  // and can only differ when they resolve to different clips, so the
  // measured one wins when it is there.
  const audioBeep = ws.peaks?.beep_time ?? coach.beep_time;
  return {
    activeShot,
    primary,
    streamUrl,
    playingScrub,
    stageTime,
    tFromBeep: ws.currentTime - coach.beep_time,
    audioBeep,
    eventsReadOnly,
    selectedEvent,
    prevStage,
    nextStage,
  };
}
