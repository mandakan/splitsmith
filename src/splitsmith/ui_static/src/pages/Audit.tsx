/**
 * Audit screen v2 (#15).
 *
 * Through Step 5 -- save flow + audit_events log persisted.
 *
 * Contract:
 *   - Audit truth = primary's audio. The waveform is always the primary's.
 *   - The active <video> drives playback time.
 *   - The Audit page exposes "primary timeline" times to children.
 *   - Markers live on the primary's timeline. Switching tabs offsets only
 *     the video element; markers don't move.
 *
 * Marker model:
 *   - Each candidate from `_candidates_pending_audit.candidates` becomes a
 *     marker. If the same candidate_number is in `shots[]`, kind="detected"
 *     (kept); otherwise kind="rejected".
 *   - Each shot with no candidate_number (or source="manual") becomes a
 *     standalone manual marker.
 *
 * Step 4 adds the bottom stepper (◀ shot N/M ▶) and the right-side list
 * drawer (toggled with `L`). Step 5 wires save: Cmd+S writes the audit
 * JSON to ``<project>/audit/stage<N>.json`` (atomic, with a .bak), and a
 * silent auto-save fires on stage switch so the user never loses work.
 * Edits accrete into ``audit_events[]`` -- an append-only history that
 * makes the saved JSON self-explaining months later.
 *
 * Right-click context menu is still deferred to Step 7 polish.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { Loader2 } from "lucide-react";

import { AnomalyPins } from "@/components/audit/AnomalyPins";
import { AuditFooter } from "@/components/audit/AuditFooter";
import { BeepStep } from "@/components/audit/BeepStep";
import { CurrentShotLine } from "@/components/audit/CurrentShotLine";
import { PrereqGate } from "@/components/audit/PrereqGate";
import { ShotList } from "@/components/audit/ShotList";
import { HelpButton, LegendKey, TransportLine, TransportMenuItems } from "@/components/audit/TransportLine";
import type { CamSyncState } from "@/components/audit/CamSyncPill";
import { CamGridModal } from "@/components/audit/CamGridModal";
import { CamPill, MultiCamColumn, type CamLayout } from "@/components/audit/MultiCamColumn";
import {
  DEFAULT_FILTERS,
  type MarkerFilters,
  visibleKindsFromFilters,
} from "@/components/AuditControls";
import { HelpOverlay } from "@/components/HelpOverlay";
import { useConfirm } from "@/components/useConfirm";
import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { VideoPanel } from "@/components/VideoPanel";
import { DesktopCommandLine } from "@/components/desktop/DesktopCommandLine";
import { Timeline, type TimelineTrack } from "@/components/timeline/Timeline";
import { WaveformTrack } from "@/components/timeline/WaveformTrack";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Kbd } from "@/components/ui/Kbd";
import { PageHeader } from "@/components/ui/PageHeader";
import { Portal } from "@/components/ui/Portal";
import {
  ApiError,
  api,
  capabilityDenied,
  errorLine,
  type ErrorLine,
  type AuditEvent,
  type Job,
  type MatchProject,
  type PeaksResult,
  type StageAudit,
  type StageVideo,
} from "@/lib/api";
import { detectAnomalies, keptShotsFromMarkers } from "@/lib/anomalies";
import { bandPipelineAction, bandReadouts } from "@/lib/auditBand";
import { onlyBookkeepingMoved } from "@/lib/auditConflict";
import { isActiveCommand, latestForStage, REDETECT_CONFIRM } from "@/lib/desktopCommands";
import { useDesktopCommands } from "@/lib/useDesktopCommands";
import { isTypingTextTarget, useBlurOnPointerClick } from "@/lib/audit-input";
import { buildAuditJson, deriveMarkers } from "@/lib/audit-doc";
import { beepStepVideos, headerState, nextFlaggedIndex, shotRows } from "@/lib/auditStep";
import { isJobActive } from "@/lib/jobs";
import { auditProxyReady, auditVideoSrc } from "@/lib/auditVideoSrc";
import { addFailedKind, auditPipCameras, type FailedKinds } from "@/lib/auditPip";
import { planServedClip } from "@/lib/camPlayback";
import { pipKeyAction, type InsetStreamKind, type PipCamera } from "@/lib/pip";
import { usePrimaryAudio } from "@/lib/usePrimaryAudio";
import { usePip } from "@/lib/usePip";
import { useScrubSource } from "@/lib/useScrubSource";
import { computeAuditNextStep } from "@/lib/audit-next-step";
import { useMatchHref } from "@/lib/matchHref";
import { placeTime, type SnapPeaks } from "@/lib/peak-snap";
import { reviewMaxZoom } from "@/lib/reviewZoom";
import { deriveStageStatus } from "@/lib/stageStatus";
import type { Zoom } from "@/lib/timelineView";
import { cn } from "@/lib/utils";

const PEAK_BINS = 1500;
/** The stage peaks route's cap: 1 ms bins on a clip up to ~131 s. */
const STAGE_PEAKS_MAX_BINS = 131_072;
const MAX_UNDO = 50;
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const MOD_LABEL = IS_MAC ? "Cmd" : "Ctrl";
const MOD_GLYPH = IS_MAC ? "\u2318" : "\u2303";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Row style for the transport line's overflow menu items. */
const MENU_ITEM =
  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-md text-ink-2 hover:bg-surface-2 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led";
const K_AUTO_PROGRESS_KEY = "splitsmith.audit.k_auto_progress";

/** Region-loop window around the focused marker (#29): 0.5 s of pre-roll
 *  to hear the shot coming, 0.7 s of tail to catch echo/AGC behavior. */
const LOOP_PRE_S = 0.5;
const LOOP_POST_S = 0.7;

export function Audit() {
  // ShooterScopedRoute canonicalises every Audit entry to /audit/:slug/:stage
  // (or /audit/:slug when no stage yet), so slug is always populated by the
  // time we render. The slug also keys the component remount on switch.
  const { slug: slugParam, stage: stageParam } = useParams<{
    slug?: string;
    stage?: string;
  }>();
  const navigate = useNavigate();
  const href = useMatchHref();
  const [searchParams] = useSearchParams();

  // Drop button / chip focus after a mouse click so the next Space press
  // toggles playback instead of re-clicking the last-touched control.
  useBlurOnPointerClick();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  // Shooters come from the MatchShell outlet context now -- one fetch
  // per match, shared with the breadcrumb chip strip. No own fetch.
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();
  const shooters = outletCtx?.shooters ?? [];
  // A desktop-synced match on hosted: detection is the desktop's job, so
  // the overflow asks it to run (#1100) instead of a local re-detect that
  // the mirror would refuse.
  const desktopMirror =
    outletCtx?.origin === "desktop" && !capabilityDenied(outletCtx?.capabilities, "review");
  const desktop = useDesktopCommands(desktopMirror);
  // ShooterScopedRoute remounts this whole component on slug change so we
  // no longer need explicit switching state -- the URL change is the
  // single source of truth.

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);
  const [snapPeaks, setSnapPeaks] = useState<SnapPeaks | null>(null);

  const [audit, setAudit] = useState<StageAudit | null>(null);

  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const undoStackRef = useRef<AuditMarker[][]>([]);
  const [focusedMarkerId, setFocusedMarkerId] = useState<string | null>(null);

  // Stepper navigates kept shots (detected + manual) in time order. The
  // index is decoupled from the playhead -- scrubbing doesn't reset it.
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [showHelp, setShowHelp] = useState(false);
  // Step 1 (the beep) in place of the editor: forced by Re-pick, or by a
  // camera pill / the ``?beep=<video_id>`` redirect from the old queue.
  const [repick, setRepick] = useState(false);
  const [beepFocusVideoId, setBeepFocusVideoId] = useState<string | null>(null);

  // Save flow (Step 5).
  // sessionEventsRef accumulates audit_events for this session; appended
  // to the saved JSON's audit_events[] on save and cleared. isDirtyRef
  // controls whether stage-switch / Cmd+S actually fires a write.
  const sessionEventsRef = useRef<AuditEvent[]>([]);
  const isDirtyRef = useRef(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  // Focus target for the editor shell. Focused on mount so the page owns
  // keyboard focus immediately -- see the focus effect below.
  const editorRootRef = useRef<HTMLDivElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [loopMode, setLoopMode] = useState(false);
  // The big <video> as an element too (PipView and the primary-audio
  // follower bind to it; a remounted player must re-bind them).
  const [bigVideoEl, setBigVideoEl] = useState<HTMLVideoElement | null>(null);
  const setBigVideo = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    setBigVideoEl(el);
  }, []);
  // Stream kinds that failed in the PiP inset, per video_id (#1407).
  const [insetFailed, setInsetFailed] = useState<FailedKinds>({});
  // The stage audit WAV failed as the swapped-in primary audio (see below).
  const [stageWavFailed, setStageWavFailed] = useState(false);
  // Auto-advance to the next visible marker after K toggles a candidate.
  // Default on (FCP-style "mark and move"); persisted across sessions
  // because the user audits in long flow blocks and shouldn't have to
  // re-enable it every reload.
  const [kAutoProgress, setKAutoProgress] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const v = window.localStorage.getItem(K_AUTO_PROGRESS_KEY);
    return v == null ? true : v === "1";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(K_AUTO_PROGRESS_KEY, kAutoProgress ? "1" : "0");
  }, [kAutoProgress]);

  // Cam layout: "focus" docks the column with primary + secondaries,
  // "grid" opens the equal-grid review modal. The column itself never
  // disappears -- the operator always has the docked surface for
  // scrubbing.
  const [camLayout, setCamLayout] = useState<CamLayout>("focus");

  // Beep editing is step 1 of this page (BeepStep); the header's Re-pick
  // and every per-cam pill open it in place -- see ``openBeepReview``.
  // Anchor for loop-to-start semantics: the audit-timeline position
  // playback last started from (or where the user last scrubbed). On
  // pause / end-of-clip while loopMode is on, the playhead snaps back
  // here. Matches the old review SPA's "Loop: pause snaps the playhead
  // back to where playback started" behavior.
  const loopAnchorRef = useRef<number | null>(null);

  // Loop region around the focused marker (#29). Loop on + a focused
  // marker = repeat a tight window around it; loop on + no focus keeps
  // the old loop-to-anchor behavior.
  const loopRegion = useMemo(() => {
    if (!loopMode || !focusedMarkerId) return null;
    const m = markers.find((x) => x.id === focusedMarkerId);
    const dur = peaks?.duration;
    if (!m || dur == null) return null;
    return {
      start: Math.max(0, m.time - LOOP_PRE_S),
      end: Math.min(dur, m.time + LOOP_POST_S),
    };
  }, [loopMode, focusedMarkerId, markers, peaks]);


  const [filters, setFilters] = useState<MarkerFilters>(DEFAULT_FILTERS);
  // Momentary "peek": true while the user holds the peek button or the p key.
  // Adds "rejected" to visibleKinds without touching the sticky filter chip.
  const [peeking, setPeeking] = useState(false);
  // The timeline band's zoom: ``null`` = Fit, else a multiple of Fit. The
  // band clamps it and owns the + / - / 0 keys and the wheel. Reset on
  // stage change.
  const [zoom, setZoom] = useState<Zoom>(null);
  const rafRef = useRef<number | null>(null);

  // ShooterScopedRoute redirects to /shooters when slug is missing, so by
  // the time this renders ``slugParam`` is always a non-empty string.
  const slug = slugParam!;

  const stageNumber = useMemo(() => {
    if (stageParam == null) return null;
    const n = Number.parseInt(stageParam, 10);
    return Number.isFinite(n) ? n : null;
  }, [stageParam]);

  // Load project once.
  useEffect(() => {
    let alive = true;
    api
      .getProject(slug)
      .then((p) => {
        if (alive) setProject(p);
      })
      .catch((err) => {
        if (alive) setProjectError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  // Resolved automation: feeds the CamSyncPill's "needs sync" gate so it
  // reads from the same threshold the HITL queue uses. Server-resolved
  // (CLI > project > global > default); we only consume the result.
  // Falls back to the in-code AutomationSettings default (0.97) until
  // the request lands.
  const [beepLowConfThreshold, setBeepLowConfThreshold] = useState(0.97);
  useEffect(() => {
    let alive = true;
    api
      .getAutomation(slug)
      .then((r) => {
        if (alive) {
          setBeepLowConfThreshold(r.settings.beep_low_confidence_threshold);
        }
      })
      .catch(() => {
        /* keep default -- automation endpoint failures aren't fatal */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Switching shooter is a route change now (#353 phase 1). The chip
  // strip uses <Link to=/audit/:newSlug/:stage>; ShooterScopedRoute
  // canonicalises the URL and remounts this component with key={slug},
  // which resets every piece of local state (peaks, audit JSON, markers,
  // video refs, undo stack) without us having to thread reset logic
  // through every effect.

  const stagesWithPrimary = useMemo(() => {
    if (!project) return [];
    return project.stages.filter((s) => s.videos.some((v) => v.role === "primary"));
  }, [project]);

  // Stable identity for memoisation: a fresh array each render churns
  // any consumer that keys on the items. The status field carries the
  // backend's per-stage lifecycle so the chip rail's trailing dot reads
  // the truth instead of falling back to "todo".
  const stageSelectorOptions = useMemo(
    () =>
      stagesWithPrimary.map((s) => ({
        stageNumber: s.stage_number,
        stageName: s.stage_name,
        status: deriveStageStatus(s),
      })),
    [stagesWithPrimary],
  );

  // Neighbour stage numbers for prev/next nav. `null` at the boundaries
  // so the header buttons disable instead of wrapping -- accidental wrap
  // is worse than a dead key when the user is moving fast. Cross-shooter
  // chaining is handled by computeAuditNextStep; these are strictly
  // within-shooter jumps for `[` / `]` and the header chevrons.
  const { prevStageNumber, nextStageNumber } = useMemo(() => {
    if (stageNumber == null || stageSelectorOptions.length === 0) {
      return { prevStageNumber: null, nextStageNumber: null };
    }
    const idx = stageSelectorOptions.findIndex((s) => s.stageNumber === stageNumber);
    if (idx === -1) return { prevStageNumber: null, nextStageNumber: null };
    return {
      prevStageNumber: idx > 0 ? stageSelectorOptions[idx - 1].stageNumber : null,
      nextStageNumber:
        idx < stageSelectorOptions.length - 1
          ? stageSelectorOptions[idx + 1].stageNumber
          : null,
    };
  }, [stageSelectorOptions, stageNumber]);

  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    // Must go through ``href`` so the target keeps the ``/match/:matchId``
    // prefix. A bare ``/audit/...`` escapes the match scope, matches no
    // route, and the catch-all redirect dead-ends at /pick (health.bound
    // is always false post-state-refactor, so it can't re-prefix).
    navigate(href("audit", slug, String(stagesWithPrimary[0].stage_number)), {
      replace: true,
    });
  }, [stageNumber, stagesWithPrimary, navigate, href, slug]);

  const stage = useMemo(() => {
    if (!project || stageNumber == null) return null;
    return project.stages.find((s) => s.stage_number === stageNumber) ?? null;
  }, [project, stageNumber]);

  const videos = useMemo<StageVideo[]>(() => {
    if (!stage) return [];
    const primary = stage.videos.find((v) => v.role === "primary");
    const secondaries = stage.videos
      .filter((v) => v.role === "secondary")
      .slice()
      .sort((a, b) => a.added_at.localeCompare(b.added_at));
    return primary ? [primary, ...secondaries] : [...secondaries];
  }, [stage]);

  const primary = videos[0] ?? null;
  const primaryBeep = primary?.beep_time ?? null;

  // Per-cam buzzer sync state, surfaced as the CamSyncPill on each tile
  // inside the video column.
  //
  //   no_beep          -- never detected anything
  //   manual           -- operator overrode the buzzer time
  //   low_confidence   -- auto-detected, confidence below the
  //                       beep_low_confidence_threshold automation
  //                       setting (same gate the HITL queue uses),
  //                       AND the operator hasn't acked it yet
  //                       (beep_reviewed === false). Reviewed
  //                       low-confidence beeps are treated as
  //                       synced -- the operator has eyeballed and
  //                       confirmed.
  //   synced           -- everything else.
  const camSyncStates = useMemo<CamSyncState[]>(() => {
    return videos.map((v) => {
      if (v.beep_time == null) return "no_beep";
      if (v.beep_source === "manual") return "manual";
      if (
        v.beep_confidence != null &&
        v.beep_confidence < beepLowConfThreshold &&
        !v.beep_reviewed
      ) {
        return "low_confidence";
      }
      return "synced";
    });
  }, [videos, beepLowConfThreshold]);
  // Beep position **on the audit timeline** -- this is the X where the
  // waveform draws the dashed beep line and where audit-time = beep-time.
  // When the server is serving trimmed audio, peaks.beep_time is the
  // clip-local beep (typically near the trim buffer of 5 s). When the
  // server falls back to full-source audio, peaks.beep_time mirrors
  // primary.beep_time. Either way, this value is the correct anchor.
  const auditBeep = peaks?.beep_time ?? primaryBeep;

  // A camera pill's "sync" opens step 1 on that camera, in place.
  const openBeepReview = useCallback((cam: StageVideo) => {
    setBeepFocusVideoId(cam.video_id);
    setRepick(true);
  }, []);

  // The other cameras are a PiP inset over the big player (#1407). Each
  // camera's inset clip and beep come from the clip the big player would
  // stream for it (lib/auditPip.ts); its sync pill rides as the note.
  // Swap / C change only which camera is big: the audit timeline, the
  // waveform, the markers and every time stay the primary's, and so does
  // the sound (see the primary-audio follower below).
  const pipCameras = useMemo(() => {
    const base = auditPipCameras({
      slug,
      stageNumber,
      videos,
      peaks: peaks ? { beep_time: peaks.beep_time, trimmed: peaks.trimmed } : null,
      preBufferSeconds: project?.trim_pre_buffer_seconds ?? 5,
      failed: insetFailed,
    });
    return base.map((c, i) => ({
      ...c,
      note: (
        <CamPill
          video={videos[i]}
          index={i}
          state={camSyncStates[i] ?? "no_beep"}
          primaryBeepTime={primaryBeep}
          onStartSync={openBeepReview}
        />
      ),
    }));
  }, [slug, stageNumber, videos, peaks, project?.trim_pre_buffer_seconds, insetFailed, camSyncStates, primaryBeep, openBeepReview]);
  const pip = usePip({ cameras: pipCameras, stageKey: `${slug}/${stageNumber ?? ""}` });
  const activeVideoIndex = Math.max(0, videos.findIndex((v) => v.video_id === pip.state.big));
  const activeVideo = videos[activeVideoIndex] ?? primary;
  const activeBeep = activeVideo?.beep_time ?? null;
  const pipCycle = pip.cycle;
  const insetKind = pip.inset ? (pipCameras.find((c) => c.id === pip.inset?.id)?.kind ?? null) : null;

  // Which file to stream for the active cam + how audit time maps onto
  // it. Secondaries have their own beep-anchored trim once built, so the
  // mapping runs through clip-local beeps - source-space deltas against
  // a trimmed clip seek past its EOF (black frame). See camPlayback.ts.
  // The trim pin plays the 720p rendition when the server named a fresh
  // one (see lib/scrubSource.ts); a playback error falls back per video.
  const scrub = useScrubSource();
  const servedPlan = useMemo(
    () =>
      activeVideo
        ? planServedClip({
            index: activeVideoIndex,
            beepTime: activeBeep,
            processedTrim: activeVideo.processed?.trim ?? false,
            primaryPeaksTrimmed: peaks?.trimmed ?? false,
            auditBeep,
            preBufferSeconds: project?.trim_pre_buffer_seconds ?? 5,
          })
        : null,
    [activeVideo, activeVideoIndex, activeBeep, auditBeep, peaks?.trimmed, project],
  );
  const beepOffset = servedPlan?.offset ?? 0;

  // Reset state on stage change. Anything dirty has already been
  // auto-saved in the StageSelector handler before navigating.
  useEffect(() => {
    setCurrentTime(0);
    setIsPlaying(false);
    // The big camera goes back to the primary by itself (usePip's
    // stageKey); the inset's failed streams are this stage's only.
    setInsetFailed({});
    setStageWavFailed(false);
    setFocusedMarkerId(null);
    setCurrentShotIndex(0);
    setRepick(false);
    setBeepFocusVideoId(null);
    setZoom(null);
    setFilters(DEFAULT_FILTERS);
    undoStackRef.current = [];
    sessionEventsRef.current = [];
    isDirtyRef.current = false;
    setSaveStatus({ kind: "idle" });
    setCamLayout("focus");
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
    // slugParam is a dependency too: shooter B's stage 3 is a different
    // recording than shooter A's stage 3, so switching shooters on the
    // same stage number must reset playback + cam layout as well.
  }, [stageNumber, slugParam]);

  // Load peaks.
  useEffect(() => {
    if (stageNumber == null || !primary) {
      setPeaks(null);
      return;
    }
    let alive = true;
    setPeaksLoading(true);
    setPeaksError(null);
    api
      .getStagePeaks(slug, stageNumber, PEAK_BINS)
      .then((p) => {
        if (alive) setPeaks(p);
      })
      .catch((err) => {
        if (alive) {
          setPeaksError(err instanceof ApiError ? err.detail : String(err));
          setPeaks(null);
        }
      })
      .finally(() => {
        if (alive) setPeaksLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [slug, stageNumber, primary]);

  // Peaks at 1 ms per bin: the leading-edge snap of a drop while zoomed out
  // (lib/peak-snap.placeTime), and the waveform the band draws once they
  // arrive, so a deep zoom shows the shot's rise instead of 35 ms blocks
  // (each column draws the loudest bin under it, at any zoom). Until this
  // resolves, drops land exactly and the band draws the fit peaks.
  useEffect(() => {
    if (!peaks || stageNumber == null) {
      setSnapPeaks(null);
      return;
    }
    const bins = Math.min(STAGE_PEAKS_MAX_BINS, Math.max(PEAK_BINS, Math.ceil(peaks.duration / 0.001)));
    if (bins <= PEAK_BINS) {
      setSnapPeaks({ peaks: peaks.peaks, duration: peaks.duration });
      return;
    }
    // Clear before fetching: drops during the refetch window fall back to
    // grid snapping instead of snapping against the previous clip's array.
    setSnapPeaks(null);
    let alive = true;
    api
      .getStagePeaks(slug, stageNumber, bins)
      .then((p) => {
        if (alive) setSnapPeaks({ peaks: p.peaks, duration: p.duration });
      })
      .catch(() => {
        if (alive) setSnapPeaks(null);
      });
    return () => {
      alive = false;
    };
  }, [peaks, slug, stageNumber]);

  // Every load of the stage's audit takes a ticket; a response applies only
  // while its ticket is the newest. Without it a slow load (the page's
  // first one, on a spinning disk) could land after a newer one -- the
  // reload after a reset -- and put the older copy back, whose revision
  // the next save then sends: a refused save over nothing. A save bumps
  // the counter too, so no load started before it can undo it.
  const auditLoadSeqRef = useRef(0);
  // A save the server refused because the stored stage really changed
  // (shots, beep or candidates; see lib/auditConflict). The operator's
  // edits stay on the page until they pick a side in the banner.
  const [conflict, setConflict] = useState<{ stored: StageAudit | null; advance: boolean } | null>(null);

  // Load audit JSON. 404 means "no audit yet" -- start with empty markers.
  useEffect(() => {
    setConflict(null);
    if (stageNumber == null) {
      setAudit(null);
      return;
    }
    const ticket = ++auditLoadSeqRef.current;
    api
      .getStageAudit(slug, stageNumber)
      .then((a) => {
        if (ticket !== auditLoadSeqRef.current) return;
        setAudit(a);
        setMarkers(deriveMarkers(a));
      })
      .catch(() => {
        if (ticket !== auditLoadSeqRef.current) return;
        setAudit(null);
        setMarkers([]);
      });
    return () => {
      auditLoadSeqRef.current += 1;
    };
  }, [slug, stageNumber]);

  // Tab change: re-seek the new <video> to the audit-timeline position.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const targetVideoTime = currentTime + beepOffset;
    const seekWhenReady = () => {
      if (Number.isFinite(targetVideoTime) && targetVideoTime >= 0) {
        v.currentTime = targetVideoTime;
      }
      if (isPlaying) void v.play();
    };
    if (v.readyState >= 1) {
      seekWhenReady();
    } else {
      v.addEventListener("loadedmetadata", seekWhenReady, { once: true });
      return () => v.removeEventListener("loadedmetadata", seekWhenReady);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVideoIndex]);

  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const auditT = v.currentTime - beepOffset;
        const dur = peaks?.duration ?? null;
        // Loop wrap: region end when a marker is focused, clip end otherwise.
        // Falls back to 0 on the rare case the anchor is unset (loop toggled
        // mid-playback before any anchor recorded).
        const regionEnd = loopRegion?.end ?? (dur != null ? dur - 0.05 : null);
        if (loopMode && regionEnd != null && auditT >= regionEnd) {
          const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
          // The inset and the primary-audio follower follow the seek.
          v.currentTime = target + beepOffset;
          setCurrentTime(target);
        } else {
          setCurrentTime(auditT);
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, beepOffset, loopMode, peaks, loopRegion]);

  // The other cameras are not the page's to sync: PipView keeps its inset
  // on the big player's clock (lib/pipSync), and the primary-audio
  // follower below uses the same rule. Every seek here moves only the big
  // player; both follow its ``seeking``.
  const handleScrub = useCallback(
    (primaryTime: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = primaryTime + beepOffset;
      setCurrentTime(primaryTime);
      // Manual scrub re-anchors the loop. Without this, hitting R, then
      // dragging to a candidate, then play-pausing would yank the
      // playhead back to the OLD anchor instead of the new one.
      loopAnchorRef.current = primaryTime;
    },
    [beepOffset],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      // Starting playback -- record the anchor in audit-timeline coords.
      loopAnchorRef.current = v.currentTime - beepOffset;
      void v.play();
      setIsPlaying(true);
    } else {
      v.pause();
      setIsPlaying(false);
      // Loop semantics: pause snaps back to region start (or play anchor if
      // no region is active).
      if (loopMode && (loopRegion != null || loopAnchorRef.current != null)) {
        const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
        v.currentTime = target + beepOffset;
        setCurrentTime(target);
      }
    }
  }, [beepOffset, loopMode, loopRegion]);

  // Stepping focus to another marker while region-looping seeks to the
  // new region's pre-roll so the "step -> loop -> K -> step" review flow
  // needs no extra scrubbing. Keyed on focusedMarkerId only: marker drags
  // recompute loopRegion each frame and must not re-trigger the seek.
  useEffect(() => {
    if (!loopMode || !isPlaying || !loopRegion) return;
    handleScrub(loopRegion.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedMarkerId]);

  // A secondary is big (a swap): its picture, the primary's sound. The
  // shared follower (lib/usePrimaryAudio) mutes the big player and plays
  // the primary's audio on its clock. Audit's primary audio is the stage
  // audit WAV, the file the waveform is drawn from, so its anchor is
  // ``peaks.beep_time``; if the WAV fails the primary's own stream takes
  // over (its served clip shares that anchor, lib/auditPip). With the
  // primary big nothing follows and nothing is muted.
  const primaryCam = pipCameras[0] ?? null;
  const primaryAudioSrc =
    stageNumber == null ? null : stageWavFailed ? (primaryCam?.src ?? null) : api.stageAudioUrl(slug, stageNumber);
  const primaryAudioBeep = stageWavFailed ? (primaryCam?.beepInClip ?? null) : (peaks?.beep_time ?? null);
  // A swap remounts the big <video> (its src changes); until the new
  // element arrives the old one still names the previous camera, so the
  // follower waits for it instead of attaching to an element on its way out.
  const bigVideoForAudio = bigVideoEl?.dataset.activePath === activeVideo?.path ? bigVideoEl : null;
  usePrimaryAudio({
    bigVideo: bigVideoForAudio,
    bigIsPrimary: activeVideoIndex === 0,
    src: primaryAudioSrc,
    primaryBeep: primaryAudioBeep,
    bigBeep: pip.big?.beepInClip ?? null,
    onError: useCallback(() => {
      if (!stageWavFailed) setStageWavFailed(true);
      else if (primaryCam) setInsetFailed((prev) => addFailedKind(prev, primaryCam.id, primaryCam.kind));
    }, [stageWavFailed, primaryCam]),
  });

  const onInsetError = useCallback(
    (camera: PipCamera, kind: InsetStreamKind | null) => {
      setInsetFailed((prev) => addFailedKind(prev, camera.id, kind));
      // A broken rendition is broken for the big player too.
      const video = videos.find((v) => v.video_id === camera.id);
      if (kind === "scrub" && video) scrub.markFailed(video);
    },
    [videos, scrub],
  );

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind,
      payload,
    });
    isDirtyRef.current = true;
  }, []);

  const handleGridModeToggle = useCallback(() => {
    // No-op: VideoPanel always renders the single big cell; the column
    // owns the other cameras (the PiP inset). Kept so VideoPanel's
    // required prop type is satisfied.
  }, []);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const mutate = useCallback((next: AuditMarker[]) => {
    setMarkers((prev) => {
      undoStackRef.current.push(prev);
      if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
      return next;
    });
    isDirtyRef.current = true;
  }, []);

  const undo = useCallback(() => {
    setMarkers((curr) => {
      const prev = undoStackRef.current.pop();
      return prev ?? curr;
    });
  }, []);

  const handleMarkerClick = useCallback(
    (m: AuditMarker) => {
      if (m.kind === "manual") return; // toggle is meaningless for manual markers
      const next = m.kind === "detected" ? "rejected" : "detected";
      recordEvent(next === "detected" ? "marker_kept" : "marker_rejected", {
        id: m.id,
        time: m.time,
        candidate_number: m.candidateNumber,
      });
      mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: next } : x)));
    },
    [markers, mutate, recordEvent],
  );

  const handleMarkerDelete = useCallback(
    (m: AuditMarker) => {
      if (m.kind === "manual") {
        recordEvent("marker_deleted", { id: m.id, time: m.time, kind: m.kind });
        mutate(markers.filter((x) => x.id !== m.id));
      } else if (m.kind === "detected") {
        recordEvent("marker_rejected", {
          id: m.id,
          time: m.time,
          candidate_number: m.candidateNumber,
        });
        mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: "rejected" } : x)));
      }
    },
    [markers, mutate, recordEvent],
  );

  // Drag / nudge gestures are bracketed by the MarkerLayer:
  //   onTimeChangeBegin -> snapshot the pre-edit markers list
  //   onTimeChange      -> live-mutate state for visual feedback (no undo push)
  //   onTimeChangeCommit-> push exactly one undo entry + one audit event
  //
  // editingSnapshotRef holds the markers snapshot taken at Begin so the
  // Commit can recover the pre-edit time without depending on the React
  // state at the moment of the closure.
  const editingSnapshotRef = useRef<{
    id: string;
    fromMarkers: AuditMarker[];
    fromTime: number;
  } | null>(null);

  const handleMarkerTimeChange = useCallback((id: string, time: number) => {
    // Live update for visual feedback only. Undo push happens at Commit.
    setMarkers((prev) => prev.map((x) => (x.id === id ? { ...x, time } : x)));
    isDirtyRef.current = true;
  }, []);

  const handleMarkerTimeChangeBegin = useCallback((id: string) => {
    setMarkers((prev) => {
      const target = prev.find((x) => x.id === id);
      editingSnapshotRef.current = {
        id,
        fromMarkers: prev,
        fromTime: target?.time ?? 0,
      };
      return prev;
    });
  }, []);

  const handleMarkerTimeChangeCommit = useCallback((id: string, time: number) => {
    const snap = editingSnapshotRef.current;
    editingSnapshotRef.current = null;
    if (!snap || snap.id !== id) return;
    if (snap.fromTime === time) return; // no-op gesture; don't dirty undo
    undoStackRef.current.push(snap.fromMarkers);
    if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind: "marker_time_changed",
      payload: { id, from_time: snap.fromTime, to_time: time },
    });
    isDirtyRef.current = true;
  }, []);

  // Alt+Arrow nudge burst (page-level handler -- fires whether or not a
  // marker has DOM focus). Uses the same begin/commit bracketing so a
  // run of nudges produces one undo entry. Mirror of MarkerLayer's
  // keyboard burst tracking; lives here because the page-level handler
  // can't see MarkerLayer's internal state.
  const altNudgeRef = useRef<{ id: string; lastTime: number; timer: number } | null>(
    null,
  );
  const flushAltNudge = useCallback(() => {
    const n = altNudgeRef.current;
    if (!n) return;
    window.clearTimeout(n.timer);
    altNudgeRef.current = null;
    handleMarkerTimeChangeCommit(n.id, n.lastTime);
  }, [handleMarkerTimeChangeCommit]);

  const handleAddManual = useCallback(
    (time: number, shiftKey = false, pxPerSecond = 0) => {
      const t = placeTime(time, { pxPerSecond, shiftKey, peaks: snapPeaks });
      const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      recordEvent("marker_added_manual", { id, time: t });
      mutate([
        ...markers,
        {
          id,
          shotId: id,
          kind: "manual",
          time: t,
          candidateNumber: null,
          confidence: null,
          peakAmplitude: null,
          note: "",
        },
      ]);
      setFocusedMarkerId(id);
    },
    [markers, mutate, recordEvent, snapPeaks],
  );

  const handleNoteChange = useCallback(
    (id: string, note: string) => {
      // Notes don't go on the undo stack -- a stray keystroke shouldn't
      // bury the last marker drag. We do log the change to audit_events
      // (debounce-on-save would be nicer; one event per keystroke is
      // fine for v1 since notes are short).
      sessionEventsRef.current.push({
        ts: new Date().toISOString(),
        kind: "note_changed",
        payload: { id, note },
      });
      isDirtyRef.current = true;
      setMarkers((prev) => prev.map((m) => (m.id === id ? { ...m, note } : m)));
    },
    [],
  );

  // Kept shots = the sequence the stepper walks. Detected (kept) + manual
  // markers, sorted by time. Rejected markers don't appear here -- the
  // user reaches them via the list drawer.
  const keptShots = useMemo(
    () =>
      markers
        .filter((m) => m.kind === "detected" || m.kind === "manual")
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers],
  );

  // Live anomaly list -- mirrors what the saved report.txt will surface.
  // Recomputed on every marker / beep / stage-time change so chips + pins
  // stay in sync as the user keeps / rejects candidates. The list also
  // absorbs the "beep looks wrong" heuristic below as a synthetic warn
  // chip -- the dedicated banner is reserved for sync-mode (per design),
  // so passive warnings ride the anomaly row alongside everything else.
  const anomalies = useMemo(() => {
    if (!stage) return [];
    const shots = keptShotsFromMarkers(markers, auditBeep);
    return detectAnomalies(shots, stage.time_seconds);
  }, [markers, auditBeep, stage]);

  // "Beep looks wrong" heuristic. Fires when the post-detection state
  // has signals that strongly suggest the beep was placed on the wrong
  // sound rather than e.g. the user missing shots. Surfacing this as a
  // banner is the proactive counterpart to the always-visible 'Re-pick
  // beep' button: catches the mistake even before the user manually
  // compares shot count vs expected.
  //
  // Heuristic, conservative to avoid false alarms:
  //   - "draw too long": first detected shot lands > 2.5s after beep
  //     (typical IPSC draw is < 2s; > 2.5s means the beep is probably
  //     before the actual buzzer).
  //   - "stage time overshoot": the last detected shot lands more than
  //     1s AFTER the official stage time. Stage time is the call from
  //     beep to last shot; if our beep is too early, last shot's time-
  //     from-beep exceeds stage_time.
  //
  // Only fires when there are enough shots to draw a conclusion (>= 3).
  const beepDiagnostic = useMemo<{ reason: string } | null>(() => {
    if (!stage || stage.time_seconds <= 0 || auditBeep == null) return null;
    // A reviewed beep is confirmed -- ``beep_reviewed`` is the single
    // source of truth (same field the backend's beep-queue status checks
    // first, before confidence: server.py ``get_beep_queue``). This
    // heuristic exists only to *prompt* review; once the operator has
    // listened and confirmed, it must fall silent everywhere it feeds
    // (the anomaly banner, the toolbar chip, and the pre-audit gate).
    if (primary?.beep_reviewed) return null;
    if (keptShots.length < 3) return null;
    const sorted = keptShots.slice().sort((a, b) => a.time - b.time);
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    const firstFromBeep = first.time - auditBeep;
    const lastFromBeep = last.time - auditBeep;
    const overshoot = lastFromBeep - stage.time_seconds;
    if (firstFromBeep > 2.5) {
      return {
        reason: `First shot lands ${firstFromBeep.toFixed(2)}s after the beep -- typical draws are well under 2 s, so the beep is likely placed before the actual buzzer.`,
      };
    }
    if (overshoot > 1.0) {
      return {
        reason: `Last shot lands ${overshoot.toFixed(2)}s after the official stage time (${stage.time_seconds.toFixed(2)}s) -- the beep may have been picked up too early.`,
      };
    }
    return null;
  }, [keptShots, auditBeep, stage, primary?.beep_reviewed]);

  // Whenever the kept-shot list shrinks (reject / delete), keep the index
  // in range. Don't change otherwise -- the user's position is sticky.
  useEffect(() => {
    if (keptShots.length === 0) {
      if (currentShotIndex !== 0) setCurrentShotIndex(0);
      return;
    }
    if (currentShotIndex >= keptShots.length) {
      setCurrentShotIndex(keptShots.length - 1);
    }
  }, [keptShots, currentShotIndex]);

  const stepShot = useCallback(
    (delta: number) => {
      if (keptShots.length === 0) return;
      // Anchor on the focused marker (#39): if focus is on a kept shot,
      // step from there. Otherwise use currentShotIndex as a fallback.
      // This way, after K rejects the focused marker, M / Shift+M land
      // on the correct neighbour in the post-mutation list instead of
      // skipping one because the integer index now addresses i+2.
      let anchor = currentShotIndex;
      if (focusedMarkerId) {
        const idx = keptShots.findIndex((k) => k.id === focusedMarkerId);
        if (idx >= 0) anchor = idx;
        // Focus is on a non-kept marker (e.g., just-rejected). Walk to
        // the kept shot at-or-just-before the playhead so +1 lands on
        // the next kept after currentTime.
        else {
          let preceding = -1;
          for (let i = 0; i < keptShots.length; i++) {
            if (keptShots[i].time <= currentTime) preceding = i;
            else break;
          }
          // delta>0: start one step *before* the next kept marker so
          // anchor+1 lands on it. delta<0: start at the next kept marker
          // so anchor-1 lands on the preceding one.
          anchor = delta > 0 ? preceding : Math.max(0, preceding + 1);
        }
      }
      const next = Math.min(Math.max(anchor + delta, 0), keptShots.length - 1);
      setCurrentShotIndex(next);
      setFocusedMarkerId(keptShots[next].id);
      handleScrub(keptShots[next].time);
    },
    [keptShots, currentShotIndex, focusedMarkerId, currentTime, handleScrub],
  );

  const visibleKinds = useMemo(() => {
    const kinds = visibleKindsFromFilters(filters);
    if (peeking) kinds.add("rejected");
    return kinds;
  }, [filters, peeking]);

  // Keep the focused marker visible even when its kind's filter is off,
  // so `n`-stepping onto a rejected marker never focuses an invisible
  // one (the user can then K it back to kept). Per-marker, not per-kind:
  // promoting the whole kind made the first click-to-reject unhide every
  // discarded detection and left the legend pill looking dead (#666).
  const forcedVisibleId = useMemo(() => {
    if (!focusedMarkerId) return null;
    const f = markers.find((x) => x.id === focusedMarkerId);
    return f && !visibleKinds.has(f.kind) ? f.id : null;
  }, [focusedMarkerId, markers, visibleKinds]);

  // Markers in time order, filtered to currently-visible kinds. N and
  // K-auto-progress walk this list -- a marker that's filtered out of
  // view shouldn't be a navigation target either, otherwise the
  // playhead jumps to a marker the user can't see.
  const visibleMarkersSorted = useMemo(
    () =>
      markers
        .filter((m) => visibleKinds.has(m.kind) || m.id === forcedVisibleId)
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers, visibleKinds, forcedVisibleId],
  );

  const stepAnyMarker = useCallback(
    (delta: number) => {
      if (visibleMarkersSorted.length === 0) return;
      // Anchor: focused marker -> use its index; otherwise pick the
      // marker at-or-just-before the playhead so forward stepping lands
      // on the next one and back-stepping lands on the previous.
      let curIdx = -1;
      if (focusedMarkerId) {
        curIdx = visibleMarkersSorted.findIndex((m) => m.id === focusedMarkerId);
      }
      if (curIdx < 0) {
        for (let i = 0; i < visibleMarkersSorted.length; i++) {
          if (visibleMarkersSorted[i].time <= currentTime) curIdx = i;
          else break;
        }
        if (curIdx < 0) curIdx = delta > 0 ? -1 : 0;
      }
      const nextIdx = Math.min(
        Math.max(curIdx + delta, 0),
        visibleMarkersSorted.length - 1,
      );
      const target = visibleMarkersSorted[nextIdx];
      setFocusedMarkerId(target.id);
      handleScrub(target.time);
      const keptIdx = keptShots.findIndex((k) => k.id === target.id);
      if (keptIdx >= 0) setCurrentShotIndex(keptIdx);
    },
    [visibleMarkersSorted, focusedMarkerId, currentTime, handleScrub, keptShots],
  );

  const jumpToMarker = useCallback(
    (m: AuditMarker) => {
      setFocusedMarkerId(m.id);
      handleScrub(m.time);
      // If the marker is a kept shot, line the stepper up with it too.
      const idx = keptShots.findIndex((k) => k.id === m.id);
      if (idx >= 0) setCurrentShotIndex(idx);
    },
    [handleScrub, keptShots],
  );

  // ---- Save flow (Step 5) ------------------------------------------------

  const performSave = useCallback(
    async (opts: { silent?: boolean; advance?: boolean; base?: StageAudit | null } = {}): Promise<boolean> => {
      if (stageNumber == null || !stage) return false;
      if (!isDirtyRef.current && opts.silent) return true; // nothing to save
      const beepInClip = peaks?.beep_time ?? primary?.beep_time ?? null;
      const appendEvents = sessionEventsRef.current;
      const build = (base: StageAudit | null): StageAudit => {
        const payload = buildAuditJson({
          base,
          stage: {
            stage_number: stage.stage_number,
            stage_name: stage.stage_name,
            time_seconds: stage.time_seconds,
          },
          primaryBeepInClip: beepInClip,
          markers,
          appendEvents: [
            ...appendEvents,
            {
              ts: new Date().toISOString(),
              kind: "save",
              payload: { shots_count: 0 /* filled after build */ },
            },
          ],
        });
        // Attach the actual shots count to the synthetic save event.
        const lastEv = payload.audit_events?.[payload.audit_events.length - 1];
        if (lastEv && lastEv.kind === "save") {
          lastEv.payload = { shots_count: payload.shots.length };
        }
        return payload;
      };
      const base = opts.base !== undefined ? opts.base : audit;
      setSaveStatus({ kind: "saving" });
      try {
        let saved: StageAudit;
        try {
          saved = await api.saveStageAudit(slug, stageNumber, build(base));
        } catch (err) {
          // 409: the stored stage no longer matches the copy this page
          // started from. When only bookkeeping moved (the audit log, the
          // revision: a stale load landing late, a re-run with the same
          // shots) the save is replayed on the stored copy and nothing is
          // asked. Otherwise the operator chooses in the conflict banner;
          // their edits stay on the page until they do.
          if (!(err instanceof ApiError && err.status === 409)) throw err;
          const fresh = await api.getStageAudit(slug, stageNumber);
          if (!onlyBookkeepingMoved(base, fresh)) {
            setConflict({ stored: fresh, advance: opts.advance ?? false });
            setSaveStatus({ kind: "idle" });
            return false;
          }
          saved = await api.saveStageAudit(slug, stageNumber, build(fresh));
        }
        auditLoadSeqRef.current += 1;
        setAudit(saved);
        setConflict(null);
        sessionEventsRef.current = [];
        isDirtyRef.current = false;
        setSaveStatus({ kind: "saved", at: Date.now() });
        // Nudge the MatchShell to re-fetch the project so the sidebar's
        // status dots transition this stage to ``audited`` immediately.
        // Without this the sidebar would keep showing ``in_progress``
        // until the next navigation -- the bug the user flagged as
        // "the state change isn't obvious".
        outletCtx?.refresh?.();
        // Auto-advance on explicit Save (Cmd+S or the Save button): the
        // common audit loop is "run detect -> Save -> next stage", so we
        // jump immediately after the write returns. Silent saves (the
        // dirty-flush during stage switch) never advance. The conveyor
        // goes where its label says: the next stage, the next shooter's
        // first stage, and after the last one the Splits page.
        if (opts.advance) {
          const step = computeAuditNextStep({
            shooters,
            activeSlug: slugParam,
            stages: stageSelectorOptions,
            activeStage: stageNumber,
          });
          if (step.kind === "stage") {
            navigate(href("audit", step.nextSlug, String(step.nextStage)));
          } else if (step.kind === "shooter") {
            navigate(href("audit", step.nextSlug));
          } else {
            navigate(href("results"));
          }
        }
        return true;
      } catch (err) {
        const message = err instanceof ApiError ? err.detail : String(err);
        setSaveStatus({ kind: "error", message });
        return false;
      }
    },
    [
      stageNumber,
      stage,
      peaks,
      primary,
      audit,
      markers,
      navigate,
      slugParam,
      shooters,
      stageSelectorOptions,
    ],
  );

  // Auto-clear "saved" toast after a short hold so it stops nagging.
  useEffect(() => {
    if (saveStatus.kind !== "saved") return;
    const timer = window.setTimeout(() => setSaveStatus({ kind: "idle" }), 2500);
    return () => window.clearTimeout(timer);
  }, [saveStatus]);

  // Auto-save on stage switch: if the user picks a different stage in
  // the selector, wait for the save to complete before navigating so a
  // crash mid-flight doesn't lose the last stage's edits.
  const navigateToStage = useCallback(
    async (n: number) => {
      if (stageNumber === n) return;
      if (isDirtyRef.current && !(await performSave({ silent: true }))) return;
      // Match-prefixed (see the stage-redirect effect above): a bare
      // ``/audit/...`` would escape the match scope and bounce to /pick.
      navigate(href("audit", slug, String(n)));
    },
    [navigate, performSave, stageNumber, href, slug],
  );

  // ---- Global keyboard shortcuts -----------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      // ``inField`` distinguishes typing text from anything else focusable.
      // Hidden checkboxes (filter chips), buttons, etc. are NOT "in a field"
      // -- the audit screen reserves Space / arrow keys / etc. for playback
      // control regardless of which control was last clicked.
      const inField = isTypingTextTarget(target);

      if (e.code === "Space" && !inField) {
        e.preventDefault();
        togglePlay();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
        return;
      }
      if (!inField && e.key === "?") {
        e.preventDefault();
        setShowHelp((v) => !v);
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void performSave({ advance: true });
        return;
      }
      // `[` / `]` walk between stages without leaving the keyboard.
      // navigateToStage auto-saves a dirty stage before navigating, so
      // these can be tapped freely while auditing.
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "[" && prevStageNumber != null) {
          e.preventDefault();
          void navigateToStage(prevStageNumber);
          return;
        }
        if (e.key === "]" && nextStageNumber != null) {
          e.preventDefault();
          void navigateToStage(nextStageNumber);
          return;
        }
      }
      // Zoom (+ / 0 / -, Cmd+1/2/3 as aliases) is the timeline band's own
      // window listener; the page does not answer those keys.
      // Alt+Arrow nudges the focused marker (or the current shot) by 1 ms,
      // the grid shot times are stored on; Alt+Shift+Arrow by 10 ms.
      // We also scrub the playhead to the new position so the user
      // immediately hears what the marker is aligned to.
      if (
        !inField &&
        e.altKey &&
        !e.metaKey &&
        !e.ctrlKey &&
        (e.key === "ArrowLeft" || e.key === "ArrowRight")
      ) {
        e.preventDefault();
        let target: AuditMarker | null = null;
        if (focusedMarkerId) {
          target = markers.find((x) => x.id === focusedMarkerId) ?? null;
        }
        if (!target && keptShots.length > 0) {
          const idx = Math.min(currentShotIndex, keptShots.length - 1);
          target = keptShots[idx];
        }
        if (!target) return;
        const dir = e.key === "ArrowRight" ? 1 : -1;
        const step = e.shiftKey ? 0.01 : 0.001;
        const dur = peaks?.duration ?? target.time + step;
        const next = Math.min(dur, Math.max(0, target.time + dir * step));
        // Burst-coalesce so Cmd+Z reverses the entire burst, not each tap.
        if (altNudgeRef.current?.id !== target.id) {
          flushAltNudge();
          handleMarkerTimeChangeBegin(target.id);
          altNudgeRef.current = {
            id: target.id,
            lastTime: next,
            timer: window.setTimeout(flushAltNudge, 350),
          };
        } else {
          window.clearTimeout(altNudgeRef.current.timer);
          altNudgeRef.current.lastTime = next;
          altNudgeRef.current.timer = window.setTimeout(flushAltNudge, 350);
        }
        handleMarkerTimeChange(target.id, next);
        handleScrub(next);
        return;
      }
      // C / Shift+C: the next (previous) camera into the PiP inset; with
      // two cameras a swap (lib/pip.ts pipKeyAction: never while typing,
      // never auto-repeat, never with a modifier).
      const camDir = inField ? null : pipKeyAction(e);
      if (camDir) {
        e.preventDefault();
        pipCycle(camDir);
        return;
      }
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "m" || e.key === "M") {
          e.preventDefault();
          stepShot(e.shiftKey ? -1 : 1);
          return;
        }
        if (e.key === "n" || e.key === "N") {
          // N steps through *every* marker (detected / rejected / manual)
          // so the user can find a rejected one and K-toggle it back to
          // kept without leaving the keyboard.
          e.preventDefault();
          stepAnyMarker(e.shiftKey ? -1 : 1);
          return;
        }
        if (e.key === "l" || e.key === "L") {
          // Loop ("L" as in the old review SPA); the visible button next
          // to play/pause matches the same icon.
          e.preventDefault();
          setLoopMode((v) => !v);
          return;
        }
        if (e.key === "r" || e.key === "R") {
          // Reject the current shot (the footer's R). Manual markers are
          // deleted, detected ones flip to rejected.
          e.preventDefault();
          const idx = Math.min(currentShotIndex, keptShots.length - 1);
          const target = keptShots[idx] ?? null;
          if (target) handleMarkerDelete(target);
          return;
        }
        if (e.key === "f" || e.key === "F") {
          // Next flagged shot, wrapping.
          e.preventDefault();
          const next = nextFlaggedIndex(shotRows(markers, anomalies).all, currentShotIndex);
          if (next != null && keptShots[next]) {
            setCurrentShotIndex(next);
            setFocusedMarkerId(keptShots[next].id);
            handleScrub(keptShots[next].time);
          }
          return;
        }
        if ((e.key === "Delete" || e.key === "Backspace") && focusedMarkerId) {
          const target = markers.find((x) => x.id === focusedMarkerId) ?? null;
          if (target?.kind === "manual") {
            e.preventDefault();
            handleMarkerDelete(target);
          }
          return;
        }
        if (e.key === "k" || e.key === "K") {
          // Keep / reject toggle for the current shot. Lets the user step
          // through shots with M and decide each one without reaching for
          // the mouse. Prefers the focused marker (could be a rejected
          // one the user is reconsidering) and falls back to the kept
          // shot at the stepper's current position.
          e.preventDefault();
          let target: AuditMarker | null = null;
          if (focusedMarkerId) {
            target = markers.find((x) => x.id === focusedMarkerId) ?? null;
          }
          if (!target && keptShots.length > 0) {
            const idx = Math.min(currentShotIndex, keptShots.length - 1);
            target = keptShots[idx];
          }
          if (target) {
            // Toggle is meaningless for manual markers; route to delete so
            // K on a manual marker actually does something useful.
            if (target.kind === "manual") handleMarkerDelete(target);
            else handleMarkerClick(target);
            // Auto-progress: walk the visible marker list (filters
            // respected) so the user can rip through K-K-K without
            // moving the mouse. stepAnyMarker anchors on the focused
            // marker, which is the one we just toggled -- so +1 lands
            // on the next visible marker even though kept/rejected
            // membership shifted underneath us.
            if (kAutoProgress) stepAnyMarker(1);
          }
          return;
        }
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          // Fine-grained playhead step. Shift = ~1 frame at 30 fps;
          // unmodified = 250 ms (matches the old review SPA).
          e.preventDefault();
          const v = videoRef.current;
          if (!v) return;
          const dir = e.key === "ArrowRight" ? 1 : -1;
          const step = e.shiftKey ? 0.025 : 0.25;
          const t = v.currentTime - beepOffset;
          const dur = peaks?.duration ?? t + step;
          handleScrub(Math.min(dur, Math.max(0, t + dir * step)));
          return;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    togglePlay,
    undo,
    stepShot,
    stepAnyMarker,
    performSave,
    beepOffset,
    peaks,
    handleScrub,
    handleMarkerClick,
    handleMarkerDelete,
    handleMarkerTimeChange,
    handleMarkerTimeChangeBegin,
    flushAltNudge,
    focusedMarkerId,
    markers,
    keptShots,
    currentShotIndex,
    kAutoProgress,
    navigateToStage,
    prevStageNumber,
    nextStageNumber,
    anomalies,
    pipCycle,
  ]);

  // Stage switch / unmount: flush any pending nudge bracket so the
  // commit doesn't fire after the markers list has been swapped out.
  useEffect(() => {
    return () => flushAltNudge();
  }, [stageNumber, flushAltNudge]);

  // Peek key handler -- separate from the main onKey effect so it can be
  // a lightweight listener that only cares about p/P down/up.
  // Guards: ignore when typing in a text field, when a modifier key is held,
  // and on auto-repeat (keydown fires repeatedly when held; we only need the
  // first press to set peeking). window blur resets peek when the user
  // alt-tabs or otherwise loses focus mid-hold.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "p" && e.key !== "P") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.repeat) return;
      const target = e.target as HTMLElement | null;
      if (isTypingTextTarget(target)) return;
      setPeeking(true);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== "p" && e.key !== "P") return;
      setPeeking(false);
    };
    const onBlur = () => setPeeking(false);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Pull keyboard focus into the editor once it mounts (and on each stage
  // change) so the global ``window`` keydown shortcuts are delivered to
  // the page right away. Without this the page saw no key at all until
  // the user first clicked the video (the originally reported bug, back
  // when zoom was Cmd+1 and the browser switched tabs instead). Guarded
  // so it never yanks focus out of a text field the operator is
  // mid-typing in.
  const editorReady = stage != null && primary != null;
  useEffect(() => {
    if (!editorReady) return;
    const root = editorRootRef.current;
    if (!root) return;
    const active = document.activeElement as HTMLElement | null;
    if (active && active !== document.body && isTypingTextTarget(active)) return;
    root.focus({ preventScroll: true });
  }, [stageNumber, editorReady]);

  // Which file the player streams: pinned per <video> element, through
  // the scrub source for a trimmed angle, naming the stage. The rules
  // live in lib/auditVideoSrc.ts.
  const videoSrc = auditVideoSrc({
    slug,
    video: activeVideo,
    plan: servedPlan,
    peaksLoaded: peaks != null,
    peaksFailed: peaksError != null,
    stageNumber,
    choose: scrub.choose,
  });

  // The trimmed audit clip drives the waveform when present; falls back
  // to the full source peaks otherwise. Beep-editing surfaces lived
  // here in a previous iteration; those moved to /beep-review.
  const displayPeaks = peaks;

  // ---- Step 1 / step 2 and the processing chain ---------------------------

  const rows = useMemo(() => shotRows(markers, anomalies), [markers, anomalies]);
  // Step 1 when a video on the stage still needs its beep confirmed, or
  // on Re-pick. Auto-trusted beeps are already reviewed server-side.
  const beepStep = stage != null && (repick || beepStepVideos(stage).length > 0);
  // The ``?beep=<video_id>`` redirect from the old queue page opens step 1
  // on that camera; consumed once.
  useEffect(() => {
    const v = searchParams.get("beep");
    if (v) {
      setBeepFocusVideoId(v);
      setRepick(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read once on mount
  }, []);
  // ``?cam=<video_id>``: the beep queue lands on this camera of the stage
  // (a pending beep, not a re-pick). Read on every change: the queue moves
  // between stages without remounting the page.
  const camParam = searchParams.get("cam");
  useEffect(() => {
    if (camParam) setBeepFocusVideoId(camParam);
  }, [camParam]);

  const reloadPeaks = useCallback(() => {
    if (stageNumber == null) return;
    api
      .getStagePeaks(slug, stageNumber, PEAK_BINS)
      .then((np) => setPeaks(np))
      .catch(() => {});
  }, [slug, stageNumber]);
  const reloadAudit = useCallback(async () => {
    if (stageNumber == null) return;
    const ticket = ++auditLoadSeqRef.current;
    const a = await api.getStageAudit(slug, stageNumber);
    if (ticket !== auditLoadSeqRef.current) return;
    setAudit(a);
    setMarkers(deriveMarkers(a));
    setConflict(null);
  }, [slug, stageNumber]);

  // The conflict banner's two ways out. Keeping the edits saves them over
  // the stored copy (its audit log kept, this session's appended);
  // loading the stored copy drops them, which is what a 409 used to do
  // without asking.
  const keepMyEdits = useCallback(() => {
    if (!conflict) return;
    void performSave({ advance: conflict.advance, base: conflict.stored });
  }, [conflict, performSave]);
  const loadStoredVersion = useCallback(() => {
    if (!conflict) return;
    auditLoadSeqRef.current += 1;
    setAudit(conflict.stored);
    setMarkers(deriveMarkers(conflict.stored));
    sessionEventsRef.current = [];
    isDirtyRef.current = false;
    setConflict(null);
  }, [conflict]);
  const reloadProject = useCallback(async () => {
    try {
      setProject(await api.getProject(slug));
    } catch {
      /* the next poll or navigation refetches */
    }
  }, [slug]);

  // Confirm chains trim -> shot_detect for this shooter and stage; the
  // shell's poller is the signal (one poller per shell). While a chain
  // job is active the page shows its progress; when the active set goes
  // empty, refetch so the gate flips to the editor without a reload.
  const jobs = outletCtx?.jobs ?? [];
  const chainJob = jobs.find(
    (j) =>
      isJobActive(j) &&
      (j.kind === "trim" || j.kind === "shot_detect") &&
      j.shooter_slug === slug &&
      j.stage_number === stageNumber,
  );
  const chainRunning = chainJob ? { kind: chainJob.kind, progress: chainJob.progress ?? null } : null;
  const chainWasRunningRef = useRef(false);
  useEffect(() => {
    if (chainJob) {
      chainWasRunningRef.current = true;
      return;
    }
    if (!chainWasRunningRef.current) return;
    chainWasRunningRef.current = false;
    void reloadProject();
    reloadPeaks();
    void reloadAudit();
  }, [chainJob, reloadProject, reloadPeaks, reloadAudit]);

  const handleBeepConfirmed = useCallback(
    async (next: { slug: string; stageNumber: number; videoId?: string } | "done" | null) => {
      setRepick(false);
      setBeepFocusVideoId(null);
      if (next === "done") {
        // The beep queue is empty: back to the Overview, whose next step
        // now says what comes after the beeps.
        navigate(href(""));
        return;
      }
      await reloadProject();
      reloadPeaks();
      void reloadAudit();
      if (next && (next.slug !== slug || next.stageNumber !== stageNumber)) {
        const cam = next.videoId ? `?cam=${encodeURIComponent(next.videoId)}` : "";
        navigate(`${href("audit", next.slug, String(next.stageNumber))}${cam}`);
      } else if (next?.videoId) {
        setBeepFocusVideoId(next.videoId);
      }
    },
    [reloadProject, reloadPeaks, reloadAudit, navigate, href, slug, stageNumber],
  );

  const stageCommand =
    desktopMirror && stageNumber != null ? latestForStage(desktop.commands, slug, stageNumber) : null;
  // The desktop's result arrives through sync: reload when the request
  // turns succeeded, so its shots show without a manual refresh.
  const lastCommandStatus = useRef<string | null>(null);
  useEffect(() => {
    const status = stageCommand?.status ?? null;
    const prev = lastCommandStatus.current;
    if ((prev === "pending" || prev === "claimed") && status === "succeeded") void reloadAudit();
    lastCommandStatus.current = status;
  }, [stageCommand?.status, reloadAudit]);

  // ---- Render ------------------------------------------------------------

  if (projectError) {
    return (
      <div className="px-4 py-4 md:px-7">
        <PageHeader title="Audit" />
        <p role="alert" className="text-sm text-led-text">
          Failed to load project: {projectError}
        </p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading project...
      </div>
    );
  }

  if (stagesWithPrimary.length === 0) {
    return (
      <div className="px-4 py-4 md:px-7">
        <PageHeader title="Audit" />
        <p className="max-w-[52ch] text-md text-muted">
          Nothing to audit yet. Assign a primary video to at least one stage on the Footage page; Audit works on a
          stage's primary audio.
        </p>
      </div>
    );
  }

  const detectedCount = markers.filter((m) => m.kind === "detected").length;
  const rejectedCount = markers.filter((m) => m.kind === "rejected").length;
  const manualCount = markers.filter((m) => m.kind === "manual").length;

  // Blocking pre-audit state. When a stage hasn't met the prerequisites
  // for audit -- the trim isn't built yet, or detection hasn't run --
  // the audit canvas is replaced by PrereqGate. The toolbar's
  // TrimNowBadge / DetectShotsBadge are suppressed in that case so the
  // affordance lives in exactly one place (the gate).
  //
  // Only fires once peaks have loaded so we don't flash the gate while
  // we're still figuring out whether the trim exists.
  const prereqKind: "trim" | "detect" | null = peaks
    ? !peaks.trimmed
      ? "trim"
      : markers.length === 0
        ? "detect"
        : null
    : null;
  const prereqActive = prereqKind != null && stage != null && primary != null;
  // The band menu's pipeline entry (lib/auditBand).
  const pipelineAction = bandPipelineAction(peaks);
  // One frame (25 ms) back or forward from the playhead, from the band header.
  const stepFrame = (dir: -1 | 1) => {
    const v = videoRef.current;
    if (!v || !peaks) return;
    const t = v.currentTime - beepOffset;
    handleScrub(Math.min(peaks.duration, Math.max(0, t + dir * 0.025)));
  };
  const prereqShouldShow = prereqActive;

  // ---- Render --------------------------------------------------------------

  const flagCount = anomalies.filter((a) => a.time != null).length;
  const activeShooter = shooters.find((s) => s.slug === slugParam) ?? null;
  const chips = primary ? headerState({ primary, keptCount: keptShots.length, flagCount }) : null;
  const header = stage
    ? {
        ordinal: pad2(stage.stage_number),
        title: stage.stage_name || "Stage",
        sub: (
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
            {activeShooter ? <span>{activeShooter.name}</span> : null}
            {chips ? (
              <>
                <Chip tick="muted">{chips.camera}</Chip>
                <Chip tone={chips.beep.tone} tick={chips.beep.tick}>
                  {chips.beep.label}
                </Chip>
                <Chip tick="muted">{chips.shots}</Chip>
                {chips.flags ? <Chip tone="warn">{chips.flags}</Chip> : null}
              </>
            ) : null}
            {audit?.needs_attention?.flagged ? (
              <Chip tone="warn" title={audit.needs_attention.note ?? undefined}>
                Flagged for desktop
              </Chip>
            ) : null}
          </span>
        ),
      }
    : null;

  const nextStep = computeAuditNextStep({
    shooters,
    activeSlug: slugParam,
    stages: stageSelectorOptions,
    activeStage: stageNumber,
  });
  const saving = saveStatus.kind === "saving";
  const currentShot = keptShots[Math.min(currentShotIndex, keptShots.length - 1)] ?? null;
  const currentFlag = currentShot ? (rows.all.find((r) => r.marker.id === currentShot.id)?.flag ?? null) : null;
  const shotAtPlayhead = keptShots.some((s) => Math.abs(s.time - currentTime) < 0.05);

  // The band's tracks, all in clip seconds (the band's domain is the clip,
  // its ruler zero the beep). Pins go on their own Flags row at their
  // content x: the band scrolls the row, so the pins need no view of their
  // own. The audio track holds the waveform and MarkerLayer in one wrapper,
  // which is the parent MarkerLayer measures for drags.
  const auditTracks = (dp: PeaksResult): TimelineTrack[] => {
    const tracks: TimelineTrack[] = [];
    if (anomalies.some((a) => a.time != null)) {
      tracks.push({
        id: "flags",
        rows: [{ label: "Flags", height: 18 }],
        render: (geom) => (
          // z-10: the pins' glow paints over the audio row below, not under it.
          <div data-testid="audit-flags-track" className="pointer-events-none absolute inset-x-0 top-1/2 z-10 h-0">
            <AnomalyPins
              anomalies={anomalies}
              duration={dp.duration}
              view={{ contentWidth: geom.contentWidth, viewportWidth: geom.contentWidth, scrollLeft: 0 }}
              onJump={(a) => {
                if (a.time != null) handleScrub(a.time);
              }}
            />
          </div>
        ),
      });
    }
    tracks.push({
      id: "audio",
      rows: [{ label: "Audio", height: 140 }],
      seekable: true,
      onDoubleClick: handleAddManual,
      render: (geom) => (
        <div data-testid="audit-audio-track" className="relative h-full">
          <WaveformTrack
            peaks={snapPeaks && snapPeaks.duration === dp.duration ? snapPeaks.peaks : dp.peaks}
            clipDuration={dp.duration}
            from={0}
            to={dp.duration}
            geom={geom}
            height={140}
            beepTime={filters.beep ? auditBeep : null}
            timerStopTime={
              filters.beep && auditBeep != null && stage && stage.time_seconds > 0
                ? auditBeep + stage.time_seconds
                : null
            }
            loopRegion={loopRegion}
          />
          <MarkerLayer
            markers={markers}
            duration={dp.duration}
            focusedId={focusedMarkerId}
            onFocusChange={setFocusedMarkerId}
            onClick={handleMarkerClick}
            onDelete={handleMarkerDelete}
            onTimeChange={handleMarkerTimeChange}
            onTimeChangeBegin={handleMarkerTimeChangeBegin}
            onTimeChangeCommit={handleMarkerTimeChangeCommit}
            visibleKinds={visibleKinds}
            forcedVisibleId={forcedVisibleId}
            snapPeaks={snapPeaks ?? undefined}
          />
        </div>
      ),
    });
    return tracks;
  };

  return (
    <div
      ref={editorRootRef}
      tabIndex={-1}
      className="relative flex min-h-full flex-col px-4 pt-4 text-ink outline-none md:px-7 md:pt-5"
    >
      {stage && primary && header ? (
        beepStep ? (
          <BeepStep
            slug={slug}
            stageNumber={stage.stage_number}
            focusVideoId={beepFocusVideoId}
            repick={repick}
            onCancel={() => {
              setRepick(false);
              setBeepFocusVideoId(null);
            }}
            onConfirmed={(next) => void handleBeepConfirmed(next)}
            header={header}
            mediaOnDesktop={project?.origin === "desktop"}
          />
        ) : (
          <>
            <PageHeader
              {...header}
              actions={
                <>
                  <Button type="button" onClick={() => setRepick(true)}>
                    Re-pick beep
                  </Button>
                  <Button
                    type="button"
                    onClick={undo}
                    disabled={undoStackRef.current.length === 0}
                    aria-label={`Undo (${MOD_LABEL}+Z)`}
                  >
                    Undo <Kbd size="sm">{MOD_GLYPH}Z</Kbd>
                  </Button>
                  {saveStatus.kind === "saved" ? <Chip tone="ok">Saved</Chip> : null}
                  {/* The gate's Run button is the primary while the gate shows. */}
                  <Button
                    type="button"
                    variant={prereqShouldShow ? "default" : "primary"}
                    onClick={() => void performSave({ advance: true })}
                    disabled={saving || prereqShouldShow}
                    aria-label={nextStep.label}
                    title={`${nextStep.label} (${MOD_LABEL}+Enter)`}
                  >
                    {saving ? "Saving..." : nextStep.label} <Kbd size="sm">{MOD_GLYPH}&#9166;</Kbd>
                  </Button>
                </>
              }
            />

            {conflict ? (
              <div
                role="alert"
                className="mb-4 flex flex-wrap items-center gap-3 rounded-[10px] border border-destructive px-3.5 py-2.5 text-md text-ink"
              >
                <span className="min-w-0 flex-1">
                  This stage was saved elsewhere since you opened it, with different shots or beep. Your edits are
                  still here and not saved.
                </span>
                <Button type="button" onClick={loadStoredVersion}>
                  Load the saved version
                </Button>
                <Button type="button" onClick={keepMyEdits} disabled={saving}>
                  Keep my edits
                </Button>
              </div>
            ) : null}

            {/* The gate carries its own running state; this line is for a
                chain that runs while the editor is up (a re-run). */}
            {chainRunning && !prereqShouldShow ? (
              <div role="status" className="mb-4 flex flex-wrap items-center gap-3 rounded-[10px] border border-rule bg-surface px-3.5 py-2.5 text-md text-ink-2">
                <span aria-hidden className="size-2 rounded-full bg-live shadow-[0_0_8px_var(--color-live)]" />
                <span>{chainRunning.kind === "trim" ? "Trimming, then detecting shots" : "Detecting shots"}</span>
                <span className="relative h-0.5 w-40 overflow-hidden rounded-full bg-surface-3">
                  <span
                    className="absolute inset-y-0 left-0 rounded-full bg-live"
                    style={{ width: `${Math.round((chainRunning.progress ?? 0) * 100)}%` }}
                  />
                </span>
                {chainRunning.progress != null ? (
                  <span className="numeral text-sm text-muted">{Math.round(chainRunning.progress * 100)}%</span>
                ) : null}
              </div>
            ) : null}

            {/* When the stage isn't ready to audit (trim missing, or no
                candidates yet), the canvas is replaced by PrereqGate. */}
            {prereqShouldShow ? (
              <PrereqGate
                kind={prereqKind!}
                slug={slug}
                stageNumber={stage.stage_number}
                stage={stage}
                blocked={primary.beep_time == null || stage.time_seconds <= 0}
                blockedReason={
                  primary.beep_time == null
                    ? "Detect or set the beep first."
                    : stage.time_seconds <= 0
                      ? "Set the stage time above, or import a scoreboard."
                      : null
                }
                hasSource
                hasStageTime={stage.time_seconds > 0}
                stageEntry={stage}
                primaryVideo={primary}
                hasBeep={primary.beep_time != null}
                beepConfidence={primary.beep_confidence ?? null}
                beepDiagnostic={beepDiagnostic?.reason ?? null}
                beepLowConfThreshold={beepLowConfThreshold}
                beepReviewed={primary.beep_reviewed}
                onRepickBeep={() => setRepick(true)}
                hasTrim={!!peaks?.trimmed}
                onProjectUpdate={(p) => {
                  setProject(p);
                  reloadPeaks();
                }}
                onAuditRefresh={reloadAudit}
              />
            ) : null}

            {!prereqShouldShow && displayPeaks ? (
              <div className="flex flex-col gap-4">
                {/* Top row (the owner's layout, 2026-10-09, as Coach): the
                    video left, the shot list right. Everything else runs
                    full width under it. On lg the row's height is bounded
                    by the viewport so the band below it stays on a laptop
                    screen: the big player's tile flexes into what is left
                    and the shot list scrolls inside its column. VideoPanel's
                    `fill` (h-full, object-contain) applies at every width,
                    so the video letterboxes inside the tile below lg too.
                    The row's floor follows the camera count (#1404): 398 px
                    with several cameras, 300 px with one. The other cameras
                    are a PiP inset over the big player (#1407), so the
                    tile takes the whole row under the column header (at
                    1440x900 with two cameras a 16:9 frame of about
                    648x365). Above the floor the row is the viewport less
                    502 px, the page header, the band and the footer at 1440
                    wide: two cameras at 1440x900 end the band's Audio row
                    at the sticky footer, and a taller screen gives its
                    extra height to the video rather than to empty space
                    under the band. */}
                <div
                  className={cn(
                    "grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-[minmax(0,1fr)]",
                    videos.length > 1
                      ? "lg:h-[max(398px,calc(100dvh-502px))]"
                      : "lg:h-[max(300px,calc(100dvh-502px))]",
                  )}
                >
                  <MultiCamColumn
                    videos={videos}
                    camSyncStates={camSyncStates}
                    primaryBeepTime={primaryBeep}
                    onStartSync={openBeepReview}
                    layout={camLayout}
                    onLayoutChange={setCamLayout}
                    pip={pip}
                    bigVideo={bigVideoEl}
                    insetKind={insetKind}
                    onInsetError={onInsetError}
                  >
                    <VideoPanel
                      ref={setBigVideo}
                      slug={slug}
                      videos={videos}
                      primaryBeepTime={primaryBeep}
                      activeIndex={activeVideoIndex}
                      onActiveIndexChange={(i) => {
                        const v = videos[i];
                        if (v) pip.focus(v.video_id);
                      }}
                      videoSrc={videoSrc}
                      onPlaybackError={() => {
                        if (activeVideo && videoSrc.includes("kind=scrub")) scrub.markFailed(activeVideo);
                      }}
                      proxyReady={auditProxyReady({
                        video: activeVideo,
                        plan: servedPlan,
                        peaksLoaded: peaks != null,
                        peaksFailed: peaksError != null,
                      })}
                      mediaOnDesktop={project?.origin === "desktop"}
                      gridMode={false}
                      onGridModeToggle={handleGridModeToggle}
                      showHeader={false}
                      fill
                      className="size-full"
                    />
                  </MultiCamColumn>
                  <ShotList
                    rows={rows}
                    beep={auditBeep}
                    currentMarkerId={focusedMarkerId ?? currentShot?.id ?? null}
                    onJump={jumpToMarker}
                    className="lg:h-full"
                  />
                </div>

                <Timeline
                  title={null}
                  toolbar={
                    <TransportLine
                      isPlaying={isPlaying}
                      onTogglePlay={togglePlay}
                      currentTime={currentTime}
                      duration={displayPeaks.duration}
                      loopMode={loopMode}
                      onToggleLoop={() => setLoopMode((v) => !v)}
                      camera={chips?.camera ?? "Head cam"}
                      loading={peaksLoading}
                      filters={filters}
                      counts={{
                        detected: detectedCount,
                        rejected: rejectedCount,
                        manual: manualCount,
                        beep: auditBeep != null ? 1 : 0,
                      }}
                      onFiltersChange={setFilters}
                      peeking={peeking}
                      onPeekStart={() => setPeeking(true)}
                      onPeekEnd={() => setPeeking(false)}
                    />
                  }
                  actionsStart={<LegendKey />}
                  actionsEnd={<HelpButton onOpenHelp={() => setShowHelp(true)} />}
                  menuExtra={
                    <TransportMenuItems
                      onStepFrame={stepFrame}
                      kAutoProgress={kAutoProgress}
                      onToggleKAuto={() => setKAutoProgress((v) => !v)}
                      fullResVideo={scrub.fullRes}
                      onToggleFullResVideo={scrub.available ? () => scrub.setFullRes(!scrub.fullRes) : undefined}
                      action={
                        pipelineAction === "trim" ? (
                          <TrimNowBadge
                            slug={slug}
                            stageNumber={stage.stage_number}
                            hasBeep={primary.beep_time != null}
                            hasStageTime={stage.time_seconds > 0}
                            onProjectUpdate={(p) => {
                              setProject(p);
                              reloadPeaks();
                            }}
                          />
                        ) : pipelineAction === "detect" ? (
                          <DetectShotsBadge
                            slug={slug}
                            stageNumber={stage.stage_number}
                            hasBeep={primary.beep_time != null}
                            hasStageTime={stage.time_seconds > 0}
                            hasCandidates={markers.length > 0}
                            onComplete={reloadAudit}
                            desktop={
                              desktopMirror
                                ? {
                                    busy: stageCommand != null && isActiveCommand(stageCommand),
                                    onRequest: () => void desktop.requestRedetect(slug, stage.stage_number),
                                  }
                                : undefined
                            }
                          />
                        ) : null
                      }
                      readouts={bandReadouts({
                        peakCount: displayPeaks.peaks.length,
                        duration: displayPeaks.duration,
                        cameras: videos.length,
                      })}
                    />
                  }
                  duration={displayPeaks.duration}
                  origin={auditBeep ?? 0}
                  fps={30}
                  currentTime={currentTime}
                  playing={isPlaying}
                  onSeek={handleScrub}
                  zoom={zoom}
                  onZoomChange={setZoom}
                  maxZoom={reviewMaxZoom(displayPeaks.duration, window.innerWidth)}
                  tracks={auditTracks(displayPeaks)}
                />

                {stageCommand || desktop.error ? (
                  <div>
                    {stageCommand ? (
                      <DesktopCommandLine
                        command={stageCommand}
                        presence={desktop.presence}
                        onCancel={(id) => void desktop.cancel(id)}
                        className="py-1"
                      />
                    ) : null}
                    {desktop.error ? <p className="py-1 text-sm text-led-text">{desktop.error}</p> : null}
                  </div>
                ) : null}
                {/* CurrentShotLine draws its own top rule; the card's border replaces it. */}
                <div className="overflow-hidden rounded-[10px] border border-rule [&>div]:border-t-0">
                  <CurrentShotLine
                    shots={keptShots}
                    currentIndex={currentShotIndex}
                    beep={auditBeep}
                    onStep={stepShot}
                    flag={currentFlag}
                    onNoteChange={handleNoteChange}
                    onReject={() => {
                      if (currentShot) handleMarkerDelete(currentShot);
                    }}
                    onAddHere={() => handleAddManual(currentTime)}
                    canAddHere={!shotAtPlayhead}
                  />
                </div>
              </div>
            ) : !prereqShouldShow && peaksLoading ? (
              <div className="flex h-32 items-center justify-center gap-2 text-md text-muted">
                <Loader2 className="size-4 animate-spin" /> Computing waveform...
              </div>
            ) : !prereqShouldShow && peaksError ? (
              <p role="alert" className="text-sm text-led-text">
                Couldn't load peaks: {peaksError}
              </p>
            ) : null}


            {/* Fullscreen grid review. The column's "Grid" segment opens
                this; clicking a tile promotes that cam to primary and
                returns to focus mode. */}
            {!prereqShouldShow && camLayout === "grid" && videos.length >= 2 ? (
              <CamGridModal
                videos={videos}
                primaryBeepTime={primaryBeep}
                isPlaying={isPlaying}
                currentTime={currentTime}
                duration={peaks?.duration ?? 0}
                onTogglePlay={togglePlay}
                onClose={() => setCamLayout("focus")}
                onPickFocus={(cam) => {
                  // The picked camera goes big; the one that was big takes
                  // the inset (a camera with no beep cannot be lined up
                  // and changes nothing).
                  pip.focus(cam.video_id);
                  setCamLayout("focus");
                }}
                renderTile={(cam, _i) => {
                  // The actual <video> stays in MultiCamColumn behind the
                  // backdrop. Grid mode is a click-to-focus picker -- a
                  // stylised tile is enough to pick which cam to promote.
                  void cam;
                  return (
                    <div className="absolute inset-0 flex items-center justify-center">
                      <span className="inline-flex size-14 items-center justify-center rounded-full border border-rule-strong bg-surface-2">
                        <span aria-hidden className="size-5 rounded-full bg-ink-2" />
                      </span>
                    </div>
                  );
                }}
              />
            ) : null}
          </>
        )
      ) : null}
      <HelpOverlay open={showHelp} onClose={() => setShowHelp(false)} mode="audit" />
      {!beepStep ? <AuditFooter onOpenHelp={() => setShowHelp(true)} camera={pip.inset == null ? null : pip.counter ? "next" : "swap"} /> : null}
      {saveStatus.kind === "error" ? <SaveToast status={saveStatus} /> : null}
    </div>
  );
}

function SaveToast({ status }: { status: SaveStatus }) {
  // Single aria-live region for save status. We render the container
  // unconditionally so screen readers can pick up status changes; only
  // the inner pill is conditional.
  let label = "";
  let tone = "";
  if (status.kind === "saving") {
    label = "Saving audit...";
    tone = "bg-surface text-ink";
  } else if (status.kind === "saved") {
    label = "Audit saved";
    tone = "bg-status-complete/10 text-ink border-status-complete/40";
  } else if (status.kind === "error") {
    label = `Save failed: ${status.message}`;
    tone = "bg-destructive/10 text-destructive border-destructive/40";
  }
  return (
    <Portal>
      <div
        role="status"
        aria-live={status.kind === "error" ? "assertive" : "polite"}
        className="pointer-events-none fixed bottom-4 right-4 z-toast"
      >
        {label ? (
          <div
            className={`pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-md ${tone}`}
          >
            {label}
          </div>
        ) : null}
      </div>
    </Portal>
  );
}

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

interface DetectShotsBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  hasCandidates: boolean;
  onComplete: () => Promise<void> | void;
  /** On a desktop-synced match: ask the desktop instead (#1100). */
  desktop?: { busy: boolean; onRequest: () => void };
}

export function DetectShotsBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  hasCandidates,
  onComplete,
  desktop,
}: DetectShotsBadgeProps) {
  const confirm = useConfirm();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ErrorLine | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const running = job != null && (job.status === "pending" || job.status === "running");
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Set the stage time in the stage prerequisites, or import a scoreboard."
      : null;

  // Auto-adopt an in-flight shot-detect job after reload. Auto-trim
  // chains shot detection; the user often lands on Audit while it's
  // still mid-flight.
  //
  // Reset local state on stage change before the async lookup -- same
  // reasoning as the trim badge: navigating from a stage with a running
  // job to one without otherwise leaves the stale ``job`` in place.
  useEffect(() => {
    let cancelled = false;
    setJob(null);
    setError(null);
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "shot_detect" &&
            j.shooter_slug === slug &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") await onComplete();
          else if (final.status === "failed")
            setError({ text: final.error ?? "Shot detection failed", refusal: false });
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [slug, stageNumber, onComplete]);

  const runDetect = useCallback(
    async (reset: boolean) => {
      setError(null);
      try {
        const initial = await api.detectShots(slug, stageNumber, { reset });
        setJob(initial);
        const final = await api.pollJob(initial.id, setJob);
        if (final.status === "failed") {
          setError({ text: final.error ?? "Shot detection failed", refusal: false });
          return;
        }
        await onComplete();
      } catch (err) {
        setError(errorLine(err, "Could not start shot detection."));
      } finally {
        setJob(null);
      }
    },
    [slug, stageNumber, onComplete],
  );

  const onClick = useCallback(() => void runDetect(false), [runDetect]);
  const onResetClick = useCallback(async () => {
    const ok = await confirm({
      title: "Reset & re-detect shots for this stage?",
      body: "This wipes your kept / rejected decisions and runs detection from scratch. Use this when the previous detection went badly (bad beep, wrong stage time, etc.) and you want to start over.",
      confirmLabel: "Reset & re-detect",
    });
    if (!ok.confirmed) return;
    void runDetect(true);
  }, [runDetect, confirm]);

  // CSV-only re-run. Reuses the existing exportStage job (the server
  // supports write_csv with everything else off, see ui/exports.py). The
  // jobs rail surfaces progress + the result path; we just kick it off
  // and clear errors here.
  const onExportShotsClick = useCallback(() => {
    setError(null);
    void (async () => {
      try {
        await api.exportStage(slug, stageNumber, {
          write_trim: false,
          write_csv: true,
          write_fcpxml: false,
          write_report: false,
          write_overlay: false,
        });
      } catch (err) {
        setError(errorLine(err, "Could not start the shots export."));
      }
    })();
  }, [slug, stageNumber]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  if (desktop) {
    return (
      <button
        type="button"
        role="menuitem"
        className={MENU_ITEM}
        disabled={blocked || desktop.busy}
        title={reason ?? (desktop.busy ? "Already asked; see the status line." : undefined)}
        onClick={() =>
          void (async () => {
            const ok = await confirm({ ...REDETECT_CONFIRM, destructive: false });
            if (ok.confirmed) desktop.onRequest();
          })()
        }
      >
        Re-detect on desktop&hellip;
      </button>
    );
  }

  // Overflow-menu rows (UX PR 5): the running state is a status row, an
  // empty candidate list gets the one-click detect, otherwise the three
  // utilities. The page's chain line carries the visible progress.
  if (running) {
    return (
      <span role="status" className={MENU_ITEM + " text-muted"}>
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        <span className="numeral">Detecting{pct != null ? ` ${pct}%` : "..."}</span>
      </span>
    );
  }
  return (
    <>
      <button
        type="button"
        role="menuitem"
        className={MENU_ITEM}
        onClick={onClick}
        disabled={blocked}
        title={reason ?? "Run shot detection on the audit clip"}
      >
        {hasCandidates ? "Re-run shot detection" : "Detect shots"}
      </button>
      {hasCandidates ? (
        <>
          <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => void onResetClick()} disabled={blocked}>
            Reset to detector output
          </button>
          <button type="button" role="menuitem" className={MENU_ITEM} onClick={onExportShotsClick}>
            Export shot table
          </button>
        </>
      ) : null}
      {error ? (
        <span className={cn("px-2.5 py-1 text-sm", error.refusal ? "text-muted" : "text-led-text")}>
          {error.text}
        </span>
      ) : null}
    </>
  );
}

interface TrimNowBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  onProjectUpdate: (p: MatchProject) => void;
}

export function TrimNowBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  onProjectUpdate,
}: TrimNowBadgeProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ErrorLine | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Set the stage time in the stage prerequisites, or import a scoreboard."
      : null;
  const running = job != null && (job.status === "pending" || job.status === "running");

  // Auto-adopt an in-flight trim on mount / stage change. After a page
  // reload the server still has the running job; we reattach to it
  // instead of leaving the user a "Trim now" button that double-submits.
  //
  // Reset local state synchronously on stage change before the async
  // lookup -- otherwise navigating from a stage with a running trim to
  // one without leaves the stale ``job`` in place (the effect's no-match
  // branch never cleared it), making every other stage look like it's
  // trimming too.
  useEffect(() => {
    let cancelled = false;
    setJob(null);
    setError(null);
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "trim" &&
            j.shooter_slug === slug &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") onProjectUpdate(await api.getProject(slug));
          else if (final.status === "failed") setError({ text: final.error ?? "Trim failed", refusal: false });
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {
        /* swallow -- the user can still click Trim now to retry */
      });
    return () => {
      cancelled = true;
    };
  }, [slug, stageNumber, onProjectUpdate]);

  const onClick = useCallback(async () => {
    setError(null);
    try {
      // The server returns the existing active job if one is in flight,
      // so two clicks (or a click after reload) don't spawn parallels.
      const initial = await api.trimStage(slug, stageNumber);
      setJob(initial);
      const final = await api.pollJob(initial.id, setJob);
      if (final.status === "failed") {
        setError({ text: final.error ?? "Trim failed", refusal: false });
        return;
      }
      const fresh = await api.getProject(slug);
      onProjectUpdate(fresh);
    } catch (err) {
      setError(errorLine(err, "Could not start the trim."));
    } finally {
      setJob(null);
    }
  }, [slug, stageNumber, onProjectUpdate]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  return (
    <>
      <button
        type="button"
        role="menuitem"
        className={MENU_ITEM}
        onClick={() => void onClick()}
        disabled={running || blocked}
        title={reason ?? "Re-encode with short GOP for scrub-friendly playback"}
      >
        {running ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
        <span className="numeral">{running ? `Trimming${pct != null ? ` ${pct}%` : "..."}` : "Trim now"}</span>
        <span className="ml-auto text-sm text-muted">untrimmed</span>
      </button>
      {error ? (
        <span className={cn("px-2.5 py-1 text-sm", error.refusal ? "text-muted" : "text-led-text")}>
          {error.text}
        </span>
      ) : null}
    </>
  );
}

