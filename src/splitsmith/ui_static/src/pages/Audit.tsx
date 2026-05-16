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

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsRight,
  Crosshair,
  HelpCircle,
  ListChecks,
  Loader2,
  Pause,
  Play,
  Repeat,
  Save,
  Undo2,
} from "lucide-react";

import {
  DEFAULT_FILTERS,
  FilterBar,
  ZoomControls,
  type MarkerFilters,
  visibleKindsFromFilters,
  zoomToPixelsPerSecond,
} from "@/components/AuditControls";
import { HelpOverlay } from "@/components/HelpOverlay";
import { ListDrawer } from "@/components/ListDrawer";
import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
import { MountSelect } from "@/components/MountSelect";
import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { ShotStepper } from "@/components/ShotStepper";
import { VideoPanel } from "@/components/VideoPanel";
import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  api,
  type AuditEvent,
  type AuditShot,
  type Job,
  type MatchProject,
  type PeaksResult,
  type ShooterListEntry,
  type StageAudit,
  type StageVideo,
} from "@/lib/api";
import { isTypingTextTarget, useBlurOnPointerClick } from "@/lib/audit-input";
import { cn } from "@/lib/utils";

const PEAK_BINS = 1500;
const MAX_UNDO = 50;
const K_AUTO_PROGRESS_KEY = "splitsmith.audit.k_auto_progress";

export function Audit() {
  // ShooterScopedRoute canonicalises every Audit entry to /audit/:slug/:stage
  // (or /audit/:slug when no stage yet), so slug is always populated by the
  // time we render. The slug also keys the component remount on switch.
  const { slug: slugParam, stage: stageParam } = useParams<{
    slug?: string;
    stage?: string;
  }>();
  const navigate = useNavigate();

  // Drop button / chip focus after a mouse click so the next Space press
  // toggles playback instead of re-clicking the last-touched control.
  useBlurOnPointerClick();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  // ShooterScopedRoute remounts this whole component on slug change so we
  // no longer need explicit switching state -- the URL change is the
  // single source of truth.

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [auditLoaded, setAuditLoaded] = useState(false);

  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const undoStackRef = useRef<AuditMarker[][]>([]);
  const [focusedMarkerId, setFocusedMarkerId] = useState<string | null>(null);

  // Stepper navigates kept shots (detected + manual) in time order. The
  // index is decoupled from the playhead -- scrubbing doesn't reset it.
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [showDrawer, setShowDrawer] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  // Save flow (Step 5).
  // sessionEventsRef accumulates audit_events for this session; appended
  // to the saved JSON's audit_events[] on save and cleared. isDirtyRef
  // controls whether stage-switch / Cmd+S actually fires a write.
  const sessionEventsRef = useRef<AuditEvent[]>([]);
  const isDirtyRef = useRef(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);
  const [loopMode, setLoopMode] = useState(false);
  const [gridMode, setGridMode] = useState(true);
  // Stable refs used by grid-mode callbacks to avoid stale closures without
  // adding them to useCallback / useEffect dep arrays.
  const isPlayingRef = useRef(false);
  const currentTimeRef = useRef(0);
  // Map of secondary video path -> element, populated by SecondarySlot mounts.
  const secondaryRefsMap = useRef<Map<string, HTMLVideoElement>>(new Map());
  // Map of secondary video path -> beep offset (secondary.beep_time - auditBeep).
  // Rebuilt whenever videos or auditBeep change so the rAF loop reads stable values.
  const secondaryOffsetsRef = useRef<Map<string, number>>(new Map());
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
  // Anchor for loop-to-start semantics: the audit-timeline position
  // playback last started from (or where the user last scrubbed). On
  // pause / end-of-clip while loopMode is on, the playhead snaps back
  // here. Matches the old review SPA's "Loop: pause snaps the playhead
  // back to where playback started" behavior.
  const loopAnchorRef = useRef<number | null>(null);

  // Keep stable refs in sync so callbacks that can't take deps use them.
  useEffect(() => { isPlayingRef.current = isPlaying; }, [isPlaying]);
  useEffect(() => { currentTimeRef.current = currentTime; }, [currentTime]);

  const [filters, setFilters] = useState<MarkerFilters>(DEFAULT_FILTERS);
  // ``null`` = fit-to-width; numeric multiplier scales pixels-per-second
  // relative to fit. Reset on stage change.
  const [zoom, setZoom] = useState<number | null>(null);
  // Callback ref so we attach the ResizeObserver exactly when the
  // wrapper mounts. A useEffect-based ref + ``[]`` deps wouldn't fire
  // again once peaks load and the conditional render finally inserts
  // the div, so viewportWidth would stay at 0 and zoom would be a
  // no-op (zoomToPixelsPerSecond returns null when viewport is 0).
  const [waveformViewport, setWaveformViewport] = useState(0);
  const waveformObserverRef = useRef<ResizeObserver | null>(null);
  const waveformWrapperRef = useCallback((el: HTMLDivElement | null) => {
    waveformObserverRef.current?.disconnect();
    if (!el) {
      waveformObserverRef.current = null;
      return;
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setWaveformViewport(w);
      }
    });
    observer.observe(el);
    setWaveformViewport(Math.floor(el.getBoundingClientRect().width));
    waveformObserverRef.current = observer;
  }, []);
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

  // Load shooters when the bound project is part of a multi-shooter match.
  // Empty list means we're inside a legacy single-shooter project; the
  // switcher hides itself in that case.
  useEffect(() => {
    let alive = true;
    api
      .listMatchShooters()
      .then((r) => {
        if (alive) setShooters(r.shooters);
      })
      .catch(() => {
        if (alive) setShooters([]);
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

  // Stable identity so <StageSelector> can memo: a fresh array each render
  // makes Chromium close the open <select> dropdown when polling re-renders
  // the page every 750 ms.
  const stageSelectorOptions = useMemo(
    () =>
      stagesWithPrimary.map((s) => ({
        stageNumber: s.stage_number,
        stageName: s.stage_name,
      })),
    [stagesWithPrimary],
  );

  // Neighbour stage numbers for prev/next nav. `null` at the boundaries
  // so the header buttons disable instead of wrapping -- accidental wrap
  // is worse than a dead key when the user is moving fast.
  const nextStageNumberRef = useRef<number | null>(null);
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
    nextStageNumberRef.current = nextStageNumber;
  }, [nextStageNumber]);

  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    const target = slugParam
      ? `/audit/${slugParam}/${stagesWithPrimary[0].stage_number}`
      : `/audit/${stagesWithPrimary[0].stage_number}`;
    navigate(target, { replace: true });
  }, [stageNumber, stagesWithPrimary, navigate, slugParam]);

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
  const activeVideo = videos[activeVideoIndex] ?? primary;
  const primaryBeep = primary?.beep_time ?? null;
  const activeBeep = activeVideo?.beep_time ?? null;
  // Beep position **on the audit timeline** -- this is the X where the
  // waveform draws the dashed beep line and where audit-time = beep-time.
  // When the server is serving trimmed audio, peaks.beep_time is the
  // clip-local beep (typically near the trim buffer of 5 s). When the
  // server falls back to full-source audio, peaks.beep_time mirrors
  // primary.beep_time. Either way, this value is the correct anchor.
  const auditBeep = peaks?.beep_time ?? primaryBeep;

  // Rebuild the secondary offset table whenever videos or auditBeep change.
  // The rAF tick loop reads this map without needing it in its dep array.
  //
  // Also catch up any already-mounted secondaries: the SecondarySlot ref
  // callback fires during the commit phase (before this effect), so on the
  // initial render after data loads -- and on stage change -- the new
  // <video> elements land in secondaryRefsMap *before* this map is
  // populated. Without a re-seek here they stay parked at source time 0
  // and play out of sync once the user hits play.
  useEffect(() => {
    const map = new Map<string, number>();
    if (auditBeep != null) {
      for (const v of videos.slice(1)) {
        if (v.beep_time != null) {
          map.set(v.path, v.beep_time - auditBeep);
        }
      }
    }
    secondaryOffsetsRef.current = map;
    for (const [path, sv] of secondaryRefsMap.current) {
      const off = map.get(path);
      if (off == null) continue;
      const target = currentTimeRef.current + off;
      if (sv.readyState >= 1) {
        sv.currentTime = target;
      } else {
        sv.addEventListener(
          "loadedmetadata",
          () => {
            sv.currentTime = target;
          },
          { once: true },
        );
      }
    }
  }, [videos, auditBeep]);

  const beepOffset = useMemo(() => {
    // Primary tab: served clip *is* the audit timeline (trimmed primary
    // serves trimmed; untrimmed primary serves source). Offset is zero.
    if (activeVideoIndex === 0) return 0;
    // Secondary tab: served clip is always the source for now (per-video
    // trimming isn't wired through the production UI yet). Map audit time
    // T to secondary source time via beep alignment.
    if (activeBeep == null || auditBeep == null) return 0;
    return activeBeep - auditBeep;
  }, [activeBeep, auditBeep, activeVideoIndex]);

  // Reset state on stage change. Anything dirty has already been
  // auto-saved in the StageSelector handler before navigating.
  useEffect(() => {
    setCurrentTime(0);
    setIsPlaying(false);
    setActiveVideoIndex(0);
    setFocusedMarkerId(null);
    setCurrentShotIndex(0);
    setShowDrawer(false);
    setZoom(null);
    setFilters(DEFAULT_FILTERS);
    undoStackRef.current = [];
    sessionEventsRef.current = [];
    isDirtyRef.current = false;
    setSaveStatus({ kind: "idle" });
    setGridMode(true);
    // Don't clear secondaryRefsMap here. Refs attach during commit before
    // this effect runs, so a clear() would wipe the freshly-mounted new
    // stage's <video> elements. SecondarySlot's unmount path calls
    // setRef(null) -> handleSecondaryRef deletes stale entries, which is
    // sufficient to keep the map clean across stage transitions.
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
  }, [stageNumber]);

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

  // Load audit JSON. 404 means "no audit yet" -- start with empty markers.
  useEffect(() => {
    if (stageNumber == null) {
      setAudit(null);
      setAuditLoaded(false);
      return;
    }
    let alive = true;
    setAuditLoaded(false);
    api
      .getStageAudit(slug, stageNumber)
      .then((a) => {
        if (!alive) return;
        setAudit(a);
        setMarkers(deriveMarkers(a));
        setAuditLoaded(true);
      })
      .catch(() => {
        if (!alive) return;
        setAudit(null);
        setMarkers([]);
        setAuditLoaded(true);
      });
    return () => {
      alive = false;
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
        // Loop wrap at end-of-clip -- snap to the anchor (where play
        // started) so the user can hear a section repeatedly without
        // re-clicking. Falls back to 0 on the rare case the anchor is
        // unset (loop toggled mid-playback before any anchor recorded).
        if (loopMode && dur != null && auditT >= dur - 0.05) {
          const target = loopAnchorRef.current ?? 0;
          v.currentTime = target + beepOffset;
          setCurrentTime(target);
          // Snap secondaries to the loop anchor too.
          for (const [path, sv] of secondaryRefsMap.current) {
            const off = secondaryOffsetsRef.current.get(path);
            if (off != null) sv.currentTime = target + off;
          }
        } else {
          setCurrentTime(auditT);
          // Drift-correct secondaries: nudge if >50 ms off. Auto-resume is
          // handled event-driven via the primary's "timeupdate" listener -- not
          // here -- to avoid seek-thrashing at 60 fps.
          for (const [path, sv] of secondaryRefsMap.current) {
            const off = secondaryOffsetsRef.current.get(path);
            if (off == null) continue;
            const expected = auditT + off;
            if (!sv.paused && Math.abs(sv.currentTime - expected) > 0.05) {
              sv.currentTime = expected;
            }
          }
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, beepOffset, loopMode, peaks]);

  // Master/slave sync: invoked from the primary's <video onTimeUpdate>.
  // Browser-throttled to ~4 Hz, which is the right rate for reconciliation.
  // For each secondary: if the primary is inside the secondary's content
  // range and it's paused, seek+play it; if out of range and playing, pause.
  // Standard multi-video sync recipe -- rAF would seek-thrash.
  const handlePrimaryTimeUpdate = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const auditT = v.currentTime - beepOffset;
    for (const [path, sv] of secondaryRefsMap.current) {
      const off = secondaryOffsetsRef.current.get(path);
      if (off == null) continue;
      const expected = auditT + off;
      const dur = Number.isFinite(sv.duration) ? sv.duration : null;
      const inRange = expected >= 0 && (dur == null || expected <= dur);
      if (inRange && sv.paused && isPlayingRef.current) {
        sv.currentTime = expected;
        void sv.play().catch(() => {});
      } else if (!inRange && !sv.paused) {
        sv.pause();
      }
    }
  }, [beepOffset]);

  const handleScrub = useCallback(
    (primaryTime: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = primaryTime + beepOffset;
      setCurrentTime(primaryTime);
      // Manual scrub re-anchors the loop. Without this, hitting R, then
      // dragging to a candidate, then play-pausing would yank the
      // playhead back to the OLD anchor instead of the new one.
      loopAnchorRef.current = primaryTime;
      // Seek all secondaries to the equivalent position. The primary's
      // timeupdate listener will resume any that are in their content range.
      for (const [path, sv] of secondaryRefsMap.current) {
        const off = secondaryOffsetsRef.current.get(path);
        if (off == null) continue;
        const expected = primaryTime + off;
        const dur = Number.isFinite(sv.duration) ? sv.duration : null;
        const inRange = expected >= 0 && (dur == null || expected <= dur);
        if (inRange) {
          sv.currentTime = expected;
          if (isPlayingRef.current && sv.paused) void sv.play().catch(() => {});
        }
      }
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
      for (const sv of secondaryRefsMap.current.values()) {
        void sv.play();
      }
      setIsPlaying(true);
    } else {
      v.pause();
      for (const sv of secondaryRefsMap.current.values()) {
        sv.pause();
      }
      setIsPlaying(false);
      // Loop semantics: pause snaps back to where play started.
      if (loopMode && loopAnchorRef.current != null) {
        const target = loopAnchorRef.current;
        v.currentTime = target + beepOffset;
        setCurrentTime(target);
        for (const [path, sv] of secondaryRefsMap.current) {
          const off = secondaryOffsetsRef.current.get(path);
          if (off != null) sv.currentTime = target + off;
        }
      }
    }
  }, [beepOffset, loopMode]);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind,
      payload,
    });
    isDirtyRef.current = true;
  }, []);

  // ---- Grid mode callbacks (#128) -----------------------------------------

  // Called by VideoPanel's SecondarySlot when a secondary video mounts/unmounts.
  const handleSecondaryRef = useCallback((path: string, el: HTMLVideoElement | null) => {
    if (el) {
      secondaryRefsMap.current.set(path, el);
      const off = secondaryOffsetsRef.current.get(path);
      if (off != null) {
        const target = currentTimeRef.current + off;
        if (el.readyState >= 1) {
          el.currentTime = target;
        } else {
          el.addEventListener("loadedmetadata", () => { el.currentTime = target; }, { once: true });
        }
        if (isPlayingRef.current) void el.play();
      }
    } else {
      secondaryRefsMap.current.delete(path);
    }
  }, []);

  // Buffering events are no-ops at the page level: each secondary plays
  // independently. The primary's timeupdate listener reconciles state on the
  // fly, so a secondary that stalls or runs off the end of its source just
  // pauses naturally and the primary keeps going. SecondarySlot still shows
  // its own buffering overlay locally.
  const handleSecondaryBuffering = useCallback(
    (_path: string, _isBuffering: boolean) => {},
    [],
  );

  const handleGridModeToggle = useCallback(() => {
    setGridMode((prev) => {
      if (!prev) {
        // Entering grid mode: lock to primary so beepOffset stays at 0.
        setActiveVideoIndex(0);
      } else {
        // Leaving grid mode: clean up secondary refs.
        secondaryRefsMap.current.clear();
      }
      return !prev;
    });
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
    (time: number) => {
      const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      recordEvent("marker_added_manual", { id, time });
      mutate([
        ...markers,
        {
          id,
          kind: "manual",
          time,
          candidateNumber: null,
          confidence: null,
          peakAmplitude: null,
          note: "",
        },
      ]);
      setFocusedMarkerId(id);
    },
    [markers, mutate, recordEvent],
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

  const visibleKinds = useMemo(() => visibleKindsFromFilters(filters), [filters]);

  // Markers in time order, filtered to currently-visible kinds. N and
  // K-auto-progress walk this list -- a marker that's filtered out of
  // view shouldn't be a navigation target either, otherwise the
  // playhead jumps to a marker the user can't see.
  const visibleMarkersSorted = useMemo(
    () =>
      markers
        .filter((m) => visibleKinds.has(m.kind))
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers, visibleKinds],
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

  const pixelsPerSecond = useMemo(
    () => zoomToPixelsPerSecond(zoom, waveformViewport, peaks?.duration ?? 0),
    [zoom, waveformViewport, peaks],
  );

  // ---- Save flow (Step 5) ------------------------------------------------

  const performSave = useCallback(
    async (opts: { silent?: boolean; advance?: boolean } = {}): Promise<boolean> => {
      if (stageNumber == null || !stage) return false;
      if (!isDirtyRef.current && opts.silent) return true; // nothing to save
      const beepInClip = peaks?.beep_time ?? primary?.beep_time ?? null;
      const appendEvents = sessionEventsRef.current;
      const payload = buildAuditJson({
        base: audit,
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
      setSaveStatus({ kind: "saving" });
      try {
        const saved = await api.saveStageAudit(slug, stageNumber, payload);
        setAudit(saved);
        sessionEventsRef.current = [];
        isDirtyRef.current = false;
        setSaveStatus({ kind: "saved", at: Date.now() });
        // Auto-advance on explicit Save (Cmd+S or the Save button): the
        // common audit loop is "run detect -> Save -> next stage", so we
        // jump immediately after the write returns. Silent saves (the
        // dirty-flush during stage switch) never advance.
        if (opts.advance && nextStageNumberRef.current != null) {
          const target = slugParam
            ? `/audit/${slugParam}/${nextStageNumberRef.current}`
            : `/audit/${nextStageNumberRef.current}`;
          navigate(target);
        }
        return true;
      } catch (err) {
        const message = err instanceof ApiError ? err.detail : String(err);
        setSaveStatus({ kind: "error", message });
        return false;
      }
    },
    [stageNumber, stage, peaks, primary, audit, markers, navigate, slugParam],
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
      if (isDirtyRef.current) {
        await performSave({ silent: true });
      }
      const target = slugParam ? `/audit/${slugParam}/${n}` : `/audit/${n}`;
      navigate(target);
    },
    [navigate, performSave, stageNumber, slugParam],
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
      if ((e.metaKey || e.ctrlKey) && (e.key === "1" || e.key === "2" || e.key === "3")) {
        // Cmd+1 zoom in / Cmd+2 fit / Cmd+3 zoom out -- matches the old
        // review SPA's bindings so muscle memory carries over.
        e.preventDefault();
        if (e.key === "2") setZoom(null);
        else if (e.key === "1")
          setZoom((z) => Math.min(16, (z ?? 1) * 1.5));
        else setZoom((z) => {
          const next = (z ?? 1) / 1.5;
          return next <= 0.25 ? null : next;
        });
        return;
      }
      // Alt+Arrow nudges the focused marker (or the current shot) by
      // detector resolution; Alt+Shift+Arrow is sample-precise (~1 ms).
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
        const step = e.shiftKey ? 0.001 : 0.0107;
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
          e.preventDefault();
          setShowDrawer((v) => !v);
          return;
        }
        if (e.key === "r" || e.key === "R") {
          // Old review SPA used L for loop; L is now the drawer here, so
          // loop moves to R ("repeat"). Visible button next to play/pause
          // matches the same icon.
          e.preventDefault();
          setLoopMode((v) => !v);
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
  ]);

  // Stage switch / unmount: flush any pending nudge bracket so the
  // commit doesn't fire after the markers list has been swapped out.
  useEffect(() => {
    return () => flushAltNudge();
  }, [stageNumber, flushAltNudge]);

  // Pin the served file to either trim or source for the lifetime of
  // this <video> element. Without this, a background trim job that
  // completes mid-playback would flip the server's auto-pick from
  // source to trim and the browser's next Range request would fall
  // past the (shorter) trim's EOF -- the player errors out with
  // "source not found" and only a full reload recovers. Wait for
  // peaks to load so we know which kind to pin to; when peaks fails
  // (no beep yet, etc.) the server's ``auto`` still does the right
  // thing because the trim can't exist without a beep.
  const videoSrc = activeVideo
    ? peaks
      ? api.videoStreamUrl(slug, activeVideo.path, peaks.trimmed ? "trim" : "source")
      : peaksError != null
        ? api.videoStreamUrl(slug, activeVideo.path)
        : ""
    : "";

  // ---- Render ------------------------------------------------------------

  if (projectError) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle>Failed to load project</CardTitle>
            <CardDescription>{projectError}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading project...
          </CardContent>
        </Card>
      </div>
    );
  }

  if (stagesWithPrimary.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Crosshair className="size-5" /> Nothing to audit yet
            </CardTitle>
            <CardDescription>
              Assign a primary video to at least one stage on the Ingest screen.
              Audit always operates on a stage's primary audio.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const detectedCount = markers.filter((m) => m.kind === "detected").length;
  const rejectedCount = markers.filter((m) => m.kind === "rejected").length;
  const manualCount = markers.filter((m) => m.kind === "manual").length;

  return (
    <div className="flex min-h-full flex-col gap-4 px-7 py-5 text-ink">
      {stage && primary ? (
        <>
          {/* Compact stage header -- carries #318 prev/next nav + viewport-stable
              swap. Replaces the legacy CardHeader. */}
          <div className="flex flex-wrap items-center gap-4 border-b border-rule pb-4">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() =>
                  prevStageNumber != null &&
                  void navigateToStage(prevStageNumber)
                }
                disabled={prevStageNumber == null}
                aria-label="Previous stage ([)"
                title="Previous stage ([)"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:border-rule-strong hover:bg-surface-3 hover:text-ink disabled:opacity-40"
              >
                <ChevronLeft className="size-4" />
              </button>
              <button
                type="button"
                onClick={() =>
                  nextStageNumber != null &&
                  void navigateToStage(nextStageNumber)
                }
                disabled={nextStageNumber == null}
                aria-label="Next stage (])"
                title="Next stage (])"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:border-rule-strong hover:bg-surface-3 hover:text-ink disabled:opacity-40"
              >
                <ChevronRight className="size-4" />
              </button>
            </div>
            <h1 className="font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink">
              <span className="text-led">
                STAGE {pad2(stage.stage_number)}
              </span>
              <span className="mx-2 text-whisper">·</span>
              <span>{stage.stage_name}</span>
            </h1>
            <div className="ml-auto inline-flex items-center gap-2.5">
              <StageSelector
                stages={stageSelectorOptions}
                selected={stageNumber ?? null}
                onSelect={navigateToStage}
              />
              <nav
                aria-label="Stage views"
                className="inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5"
              >
                <span className="tab-pill-led-fill inline-flex min-h-9 items-center rounded-md px-3.5">
                  Audit
                </span>
                <button
                  type="button"
                  onClick={() =>
                    stageNumber != null && navigate(`/compare/${stageNumber}`)
                  }
                  className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Compare
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (stageNumber == null) return;
                    const target = slugParam
                      ? `/coach/${slugParam}/${stageNumber}`
                      : `/coach/${stageNumber}`;
                    navigate(target);
                  }}
                  className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Coach
                </button>
              </nav>
            </div>
          </div>

          {/* Shooter switcher: only renders for multi-shooter matches.
              Chip is a Link to /audit/:newSlug/:stage; ShooterScopedRoute
              keys the page on slug so the switch remounts cleanly (#353). */}
          <ShooterChipStrip
            shooters={shooters}
            stage={stageNumber}
            activeSlug={slugParam}
            urlBase="audit"
            label="Auditing"
          />

          {/* Toolbar: Save + Undo + status badges + filter chips + zoom */}
          <div className="flex flex-wrap items-center gap-2.5">
            <Button
              type="button"
              onClick={() => void performSave({ advance: true })}
              disabled={saveStatus.kind === "saving"}
              aria-label="Save and go to next stage (Cmd+S)"
              title="Save and advance (Cmd+S)"
              className="btn-led-fill"
            >
              {saveStatus.kind === "saving" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : saveStatus.kind === "saved" ? (
                <CheckCircle2 className="size-3.5" />
              ) : (
                <Save className="size-3.5" />
              )}
              <span>{saveStatus.kind === "saving" ? "Saving" : "Save & next"}</span>
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={undo}
              disabled={undoStackRef.current.length === 0}
              aria-label="Undo (Cmd+Z)"
              title="Undo (Cmd+Z)"
            >
              <Undo2 className="size-3.5" />
              <span className="font-display uppercase tracking-[0.08em]">
                Undo
              </span>
            </Button>
            {peaks && !peaks.trimmed ? (
              <TrimNowBadge
                slug={slug}
                stageNumber={stage.stage_number}
                hasBeep={primary.beep_time != null}
                hasStageTime={stage.time_seconds > 0}
                onProjectUpdate={(p) => {
                  setProject(p);
                  if (stageNumber != null) {
                    api
                      .getStagePeaks(slug, stageNumber, PEAK_BINS)
                      .then((np) => setPeaks(np))
                      .catch(() => {});
                  }
                }}
              />
            ) : null}
            {peaks && peaks.trimmed ? (
              <DetectShotsBadge
                slug={slug}
                stageNumber={stage.stage_number}
                hasBeep={primary.beep_time != null}
                hasStageTime={stage.time_seconds > 0}
                hasCandidates={markers.length > 0}
                onComplete={async () => {
                  if (stageNumber == null) return;
                  const a = await api.getStageAudit(slug, stageNumber);
                  setAudit(a);
                  setMarkers(deriveMarkers(a));
                }}
              />
            ) : null}
            {peaks ? (
              <FilterBar
                filters={filters}
                counts={{
                  detected: detectedCount,
                  rejected: rejectedCount,
                  manual: manualCount,
                }}
                onChange={setFilters}
              />
            ) : null}
            <div className="ml-auto inline-flex items-center gap-2">
              {peaks ? <ZoomControls zoom={zoom} onZoomChange={setZoom} /> : null}
              <button
                type="button"
                onClick={() => setShowDrawer((v) => !v)}
                aria-label="Toggle marker drawer (L)"
                aria-pressed={showDrawer}
                title="Marker list (L)"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-muted transition-colors hover:bg-surface-3 hover:text-ink"
              >
                <ListChecks className="size-4" />
              </button>
              <button
                type="button"
                onClick={() => setShowHelp(true)}
                aria-label="Keyboard shortcuts (?)"
                title="Keyboard shortcuts (?)"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-muted transition-colors hover:bg-surface-3 hover:text-ink"
              >
                <HelpCircle className="size-4" />
              </button>
            </div>
          </div>

          {/* Shortcuts strip -- the kbd panel #327 calls out */}
          <ShortcutsStrip />

          {/* Active video info -- path + camera + mount, formerly the
              CardDescription line. Sits between toolbar and video tile. */}
          <div className="flex flex-wrap items-center gap-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
            <span>
              <b className="font-semibold text-ink-2">
                {activeVideoIndex === 0 ? "Primary" : `Cam ${activeVideoIndex + 1}`}
              </b>{" "}
              &middot;{" "}
              <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.625rem] tracking-normal text-ink-2 normal-case">
                {activeVideo?.path ?? primary.path}
              </code>
            </span>
            {primary.beep_time != null ? (
              <span className="rounded border border-beep/40 bg-beep-tint px-2 py-0.5 font-bold tabular-nums text-beep">
                beep at {primary.beep_time.toFixed(3)}s
              </span>
            ) : (
              <span className="rounded border border-led/40 bg-led-tint px-2 py-0.5 font-bold text-led">
                no beep yet
              </span>
            )}
            {videos.length > 1 ? (
              <span className="rounded border border-rule-strong bg-surface-2 px-2 py-0.5 font-bold tabular-nums text-ink-2">
                {videos.length} cams
              </span>
            ) : null}
            {auditLoaded && audit ? (
              <span className="rounded border border-done/40 bg-done/10 px-2 py-0.5 font-bold text-done">
                audit loaded
              </span>
            ) : auditLoaded ? (
              <span className="rounded border border-rule-strong bg-surface-2 px-2 py-0.5 font-bold text-muted">
                no audit yet
              </span>
            ) : null}
            {activeVideo && stageNumber != null ? (
              <MountSelect
                slug={slug}
                video={activeVideo}
                stageNumber={stageNumber}
                label="Mount"
                onProjectUpdate={setProject}
                setError={setProjectError}
              />
            ) : null}
          </div>

          {/* Video panel wrapped in instrument-panel frame */}
          <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
            <VideoPanel
              ref={videoRef}
              videos={videos}
              primaryBeepTime={primaryBeep}
              activeIndex={activeVideoIndex}
              onActiveIndexChange={setActiveVideoIndex}
              videoSrc={videoSrc}
              gridMode={gridMode}
              onGridModeToggle={handleGridModeToggle}
              onSecondaryRef={handleSecondaryRef}
              onSecondaryBuffering={handleSecondaryBuffering}
              onPrimaryTimeUpdate={handlePrimaryTimeUpdate}
              className="[&_video]:!max-h-[max(180px,calc(100vh-32rem))]"
            />
          </div>

          {peaks ? (
            <>
              {/* Transport bar */}
              <div className="flex flex-wrap items-center gap-3 rounded-xl border border-rule bg-surface-2 px-4 py-3">
                <button
                  type="button"
                  onClick={togglePlay}
                  aria-label={isPlaying ? "Pause (Space)" : "Play (Space)"}
                  title={isPlaying ? "Pause (Space)" : "Play (Space)"}
                  className="inline-flex size-11 items-center justify-center rounded-full bg-led text-bg shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
                >
                  {isPlaying ? (
                    <Pause className="size-5" />
                  ) : (
                    <Play className="size-5" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setLoopMode((v) => !v)}
                  aria-pressed={loopMode}
                  title="Loop the audit clip (R)"
                  aria-label={loopMode ? "Loop on (R)" : "Loop off (R)"}
                  className={cn(
                    "inline-flex size-9 items-center justify-center rounded-md border transition-colors",
                    loopMode
                      ? "border-led bg-led/10 text-led shadow-[0_0_10px_var(--color-led-glow)]"
                      : "border-rule bg-surface-3 text-muted hover:bg-surface-4 hover:text-ink",
                  )}
                >
                  <Repeat className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setKAutoProgress((v) => !v)}
                  aria-pressed={kAutoProgress}
                  title={
                    kAutoProgress
                      ? "Auto-advance on K is on"
                      : "Auto-advance on K is off"
                  }
                  className={cn(
                    "inline-flex size-9 items-center justify-center rounded-md border transition-colors",
                    kAutoProgress
                      ? "border-led bg-led/10 text-led"
                      : "border-rule bg-surface-3 text-muted hover:bg-surface-4 hover:text-ink",
                  )}
                >
                  <ChevronsRight className="size-4" />
                </button>
                <div className="ml-2 flex items-center gap-5 font-mono tabular-nums">
                  <Readout
                    label="Position"
                    value={formatTime(currentTime)}
                  />
                  <Readout
                    label="Clip"
                    value={formatTime(peaks.duration)}
                  />
                  {stage.time_seconds > 0 && (
                    <Readout
                      label="Stage"
                      value={`${stage.time_seconds.toFixed(3)}s`}
                    />
                  )}
                </div>
                <div className="ml-auto flex items-center gap-4 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted tabular-nums">
                  <span>
                    <b className="font-bold text-done">{detectedCount}</b> kept
                  </span>
                  <span>
                    <b className="font-bold text-muted">{rejectedCount}</b> rejected
                  </span>
                  <span>
                    <b className="font-bold text-manual">{manualCount}</b> manual
                  </span>
                </div>
              </div>

              {/* Scope waveform with framed chrome */}
              <div className="overflow-hidden rounded-2xl border border-rule-strong bg-bg-glow shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule bg-gradient-to-b from-surface to-transparent px-5 py-3">
                  <div className="inline-flex items-center gap-2.5 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
                    <Crosshair className="size-4 text-led" />
                    Oscilloscope
                  </div>
                  <div className="flex items-center gap-3 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted tabular-nums">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="inline-block size-1.5 rounded-full bg-beep shadow-[0_0_5px_rgba(6,182,212,0.6)]"
                      />
                      Beep
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="inline-block size-1.5 rounded-full bg-ink"
                      />
                      Accepted <b className="font-bold text-ink">{detectedCount}</b>
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="inline-block size-1.5 rounded-full bg-manual"
                      />
                      Manual <b className="font-bold text-ink">{manualCount}</b>
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_5px_var(--color-led-glow)]"
                      />
                      Rejected <b className="font-bold text-ink">{rejectedCount}</b>
                    </span>
                    {peaksLoading ? (
                      <span
                        className="inline-flex items-center gap-1.5 text-led"
                        aria-live="polite"
                      >
                        <Loader2 className="size-3 animate-spin" aria-hidden />
                        Loading
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="relative px-4 py-3" ref={waveformWrapperRef}>
                  <Waveform
                    peaks={peaks.peaks}
                    duration={peaks.duration}
                    currentTime={currentTime}
                    beepTime={filters.beep ? auditBeep : null}
                    pixelsPerSecond={pixelsPerSecond}
                    onScrub={handleScrub}
                    onDoubleClick={handleAddManual}
                    height={180}
                  >
                    <MarkerLayer
                      markers={markers}
                      duration={peaks.duration}
                      focusedId={focusedMarkerId}
                      onFocusChange={setFocusedMarkerId}
                      onClick={handleMarkerClick}
                      onDelete={handleMarkerDelete}
                      onTimeChange={handleMarkerTimeChange}
                      onTimeChangeBegin={handleMarkerTimeChangeBegin}
                      onTimeChangeCommit={handleMarkerTimeChangeCommit}
                      visibleKinds={visibleKinds}
                    />
                  </Waveform>
                </div>
                {beepOffset !== 0 ? (
                  <div className="border-t border-rule px-5 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle tabular-nums">
                    cam offset {beepOffset >= 0 ? "+" : ""}
                    {beepOffset.toFixed(3)}s applied to active camera
                  </div>
                ) : null}
              </div>

              {/* Shot stepper */}
              <ShotStepper
                shots={keptShots}
                currentIndex={currentShotIndex}
                onStep={stepShot}
                onNoteChange={handleNoteChange}
              />
            </>
          ) : peaksLoading ? (
            <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" /> Computing waveform...
            </div>
          ) : peaksError ? (
            <div className="rounded-md border border-led/40 bg-led/10 p-4 text-sm text-led">
              Couldn't load peaks: {peaksError}
            </div>
          ) : null}
        </>
      ) : null}
      <ListDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        markers={markers}
        currentMarkerId={focusedMarkerId}
        onJumpTo={jumpToMarker}
        onDelete={handleMarkerDelete}
      />
      <HelpOverlay
        open={showHelp}
        onClose={() => setShowHelp(false)}
        mode="audit"
      />
      <SaveToast status={saveStatus} />
    </div>
  );
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-start gap-0.5">
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        {label}
      </span>
      <span className="font-mono text-base font-bold leading-none text-ink">
        {value}
      </span>
    </div>
  );
}

function ShortcutsStrip() {
  const items: { keys: string; label: string }[] = [
    { keys: "Space", label: "Play / Pause" },
    { keys: "← →", label: "Step playhead" },
    { keys: "M / Shift+M", label: "Step shots" },
    { keys: "K", label: "Toggle accept" },
    { keys: "A / dblclick", label: "Add manual" },
    { keys: "Alt+← →", label: "Nudge focused" },
    { keys: "R", label: "Loop" },
    { keys: "L", label: "Marker list" },
    { keys: "[ / ]", label: "Prev / next stage" },
    { keys: "⌘S", label: "Save & next" },
    { keys: "⌘Z", label: "Undo" },
    { keys: "?", label: "Shortcuts" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-md border border-rule bg-surface-2/60 px-3.5 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
      {items.map((it) => (
        <span key={it.keys} className="inline-flex items-center gap-1.5">
          <kbd className="rounded border border-rule-strong bg-surface-3 px-1.5 py-0.5 font-mono text-[0.625rem] font-semibold text-ink-2">
            {it.keys}
          </kbd>
          <span>{it.label}</span>
        </span>
      ))}
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function SaveToast({ status }: { status: SaveStatus }) {
  // Single aria-live region for save status. We render the container
  // unconditionally so screen readers can pick up status changes; only
  // the inner pill is conditional.
  let label = "";
  let tone = "";
  if (status.kind === "saving") {
    label = "Saving audit...";
    tone = "bg-card text-foreground";
  } else if (status.kind === "saved") {
    label = "Audit saved";
    tone = "bg-status-complete/10 text-foreground border-status-complete/40";
  } else if (status.kind === "error") {
    label = `Save failed: ${status.message}`;
    tone = "bg-destructive/10 text-destructive border-destructive/40";
  }
  return (
    <div
      role="status"
      aria-live={status.kind === "error" ? "assertive" : "polite"}
      className="pointer-events-none fixed bottom-4 right-4 z-50"
    >
      {label ? (
        <div
          className={`pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-md ${tone}`}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
}

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

function buildAuditJson(opts: {
  base: StageAudit | null;
  stage: { stage_number: number; stage_name: string; time_seconds: number };
  primaryBeepInClip: number | null;
  markers: AuditMarker[];
  appendEvents: AuditEvent[];
}): StageAudit {
  const { base, stage, primaryBeepInClip, markers, appendEvents } = opts;

  // Kept = detected + manual, sorted by time. Each gets a sequential
  // shot_number; we preserve the candidate_number when the marker came
  // from a detected candidate so the SSI cross-reference stays intact.
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const shots: AuditShot[] = kept.map((m, i) => {
    const ms_after_beep =
      primaryBeepInClip != null ? Math.round((m.time - primaryBeepInClip) * 1000) : 0;
    return {
      shot_number: i + 1,
      candidate_number: m.candidateNumber,
      time: round3(m.time),
      ms_after_beep,
      source: m.kind === "manual" ? "manual" : "detected",
      ...(m.note ? { note: m.note } : {}),
    } as AuditShot & { note?: string };
  });

  const previousEvents = base?.audit_events ?? [];
  const audit_events = [...previousEvents, ...appendEvents];

  return {
    ...(base ?? {}),
    stage_number: stage.stage_number,
    stage_name: stage.stage_name,
    stage_time_seconds: stage.time_seconds,
    beep_time: primaryBeepInClip ?? base?.beep_time,
    shots,
    _candidates_pending_audit: base?._candidates_pending_audit,
    audit_events,
  };
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

interface StageSelectorProps {
  stages: { stageNumber: number; stageName: string }[];
  selected: number | null;
  onSelect: (n: number) => void | Promise<void>;
}

// Custom button + popover instead of <select>. The native <select>
// dropdown was closing immediately on mouse-click in this environment
// (multiple suspected causes; we stopped fighting native semantics).
// Keyboard semantics are preserved: ArrowUp/Down/Home/End/Enter/Esc.
const StageSelector = memo(function StageSelector({
  stages,
  selected,
  onSelect,
}: StageSelectorProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const selectedIndex = useMemo(
    () => stages.findIndex((s) => s.stageNumber === selected),
    [stages, selected],
  );
  const [highlightIndex, setHighlightIndex] = useState(selectedIndex < 0 ? 0 : selectedIndex);

  useEffect(() => {
    if (open) setHighlightIndex(selectedIndex < 0 ? 0 : selectedIndex);
  }, [open, selectedIndex]);

  // Close on outside click + Escape.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!containerRef.current) return;
      if (containerRef.current.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Keep the highlighted option in view.
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    if (!list) return;
    const child = list.children[highlightIndex] as HTMLElement | undefined;
    child?.scrollIntoView({ block: "nearest" });
  }, [open, highlightIndex]);

  // Focus the listbox so ArrowUp/Down/Enter work without a second click.
  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  const commit = (idx: number) => {
    const s = stages[idx];
    if (!s) return;
    setOpen(false);
    void onSelect(s.stageNumber);
    buttonRef.current?.focus();
  };

  const onListKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIndex((i) => Math.min(stages.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setHighlightIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setHighlightIndex(stages.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commit(highlightIndex);
    }
  };

  const onButtonKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  };

  const current = stages.find((s) => s.stageNumber === selected);

  return (
    <div className="relative flex items-center gap-2 text-sm" ref={containerRef}>
      <span className="text-muted-foreground">Stage</span>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onButtonKeyDown}
        className="inline-flex min-w-[14rem] items-center justify-between rounded-md border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="truncate">
          {current ? `${current.stageNumber} -- ${current.stageName}` : "Select stage..."}
        </span>
        <span aria-hidden className="ml-2 opacity-60">
          v
        </span>
      </button>
      {open ? (
        <div
          ref={listRef}
          role="listbox"
          tabIndex={-1}
          aria-activedescendant={
            stages[highlightIndex]
              ? `audit-stage-opt-${stages[highlightIndex].stageNumber}`
              : undefined
          }
          onKeyDown={onListKeyDown}
          onMouseDown={(e) => e.stopPropagation()}
          className="absolute right-0 top-full z-50 mt-1 max-h-72 w-[18rem] overflow-y-auto rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md focus:outline-none"
        >
          {stages.map((s, i) => {
            const isHighlighted = i === highlightIndex;
            const isSelected = s.stageNumber === selected;
            return (
              <div
                key={s.stageNumber}
                id={`audit-stage-opt-${s.stageNumber}`}
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setHighlightIndex(i)}
                onClick={() => commit(i)}
                className={cn(
                  "cursor-pointer rounded-sm px-2 py-1",
                  isHighlighted && "bg-accent text-accent-foreground",
                  isSelected && "font-medium",
                )}
              >
                {s.stageNumber} -- {s.stageName}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
});

interface DetectShotsBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  hasCandidates: boolean;
  onComplete: () => Promise<void> | void;
}

function DetectShotsBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  hasCandidates,
  onComplete,
}: DetectShotsBadgeProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const running = job != null && (job.status === "pending" || job.status === "running");
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Import a scoreboard so the stage time is known."
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
            setError(final.error ?? "Shot detection failed");
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [stageNumber, onComplete]);

  const runDetect = useCallback(
    async (reset: boolean) => {
      setError(null);
      try {
        const initial = await api.detectShots(slug, stageNumber, { reset });
        setJob(initial);
        const final = await api.pollJob(initial.id, setJob);
        if (final.status === "failed") {
          setError(final.error ?? "Shot detection failed");
          return;
        }
        await onComplete();
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        setJob(null);
      }
    },
    [slug, stageNumber, onComplete],
  );

  const onClick = useCallback(() => void runDetect(false), [runDetect]);
  const onResetClick = useCallback(() => {
    if (
      !window.confirm(
        "Reset & re-detect shots for this stage?\n\n" +
          "This wipes your kept / rejected decisions and runs detection from " +
          "scratch. Use this when the previous detection went badly (bad beep, " +
          "wrong stage time, etc.) and you want to start over.",
      )
    )
      return;
    void runDetect(true);
  }, [runDetect]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  const idleLabel = hasCandidates ? "Re-run detection" : "Detect shots";
  return (
    <span className="flex items-center gap-2">
      {hasCandidates ? null : (
        <Badge variant="secondary" title="No candidates yet -- run shot detection">
          no candidates
        </Badge>
      )}
      <Button
        size="sm"
        variant="outline"
        onClick={onClick}
        disabled={running || blocked}
        // Fixed width: progress text shouldn't reflow the row mid-poll.
        className="min-w-[12rem] justify-center"
        title={
          reason ??
          (hasCandidates
            ? "Re-run shot detection (refreshes candidates; kept shots are preserved)"
            : "Run splitsmith.shot_detect on the audit clip")
        }
      >
        {running ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
        <span className="tabular-nums">
          {running ? "Detecting..." : idleLabel}
          {running && pct != null ? ` (${pct.toString().padStart(2, " ")}%)` : null}
        </span>
      </Button>
      {hasCandidates ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={onResetClick}
          disabled={running || blocked}
          title="Reset & re-detect: wipes kept / rejected decisions and starts over"
          className="text-destructive hover:text-destructive"
        >
          Reset
        </Button>
      ) : null}
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </span>
  );
}

interface TrimNowBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  onProjectUpdate: (p: MatchProject) => void;
}

function TrimNowBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  onProjectUpdate,
}: TrimNowBadgeProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Import a scoreboard so the stage time is known."
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
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") onProjectUpdate(await api.getProject(slug));
          else if (final.status === "failed") setError(final.error ?? "Trim failed");
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
  }, [stageNumber, onProjectUpdate]);

  const onClick = useCallback(async () => {
    setError(null);
    try {
      // The server returns the existing active job if one is in flight,
      // so two clicks (or a click after reload) don't spawn parallels.
      const initial = await api.trimStage(slug, stageNumber);
      setJob(initial);
      const final = await api.pollJob(initial.id, setJob);
      if (final.status === "failed") {
        setError(final.error ?? "Trim failed");
        return;
      }
      const fresh = await api.getProject(slug);
      onProjectUpdate(fresh);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setJob(null);
    }
  }, [stageNumber, onProjectUpdate]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  return (
    <span className="flex items-center gap-2">
      <Badge
        variant="destructive"
        title="The audit screen is reading the full source clip. Trim makes scrubbing frame-accurate."
      >
        untrimmed -- scrubbing will be slow
      </Badge>
      <Button
        size="sm"
        variant="outline"
        onClick={onClick}
        disabled={running || blocked}
        // Fixed width so progress text changes (e.g. "Trimming... (12%)" ->
        // "(45%)") don't reflow the row. Reflow on Chromium closes any open
        // native <select> dropdown elsewhere on the page.
        className="min-w-[12rem] justify-center"
        title={reason ?? "Re-encode with short GOP for scrub-friendly playback"}
      >
        {running ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
        <span className="tabular-nums">
          {running ? "Trimming..." : "Trim now"}
          {running && pct != null ? ` (${pct.toString().padStart(2, " ")}%)` : null}
        </span>
      </Button>
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </span>
  );
}

function deriveMarkers(audit: StageAudit | null): AuditMarker[] {
  if (!audit) return [];
  const candidates = audit._candidates_pending_audit?.candidates ?? [];
  const shotsByCandidateNumber = new Map<number, true>();
  for (const s of audit.shots ?? []) {
    if (s.candidate_number != null) shotsByCandidateNumber.set(s.candidate_number, true);
  }
  const markers: AuditMarker[] = candidates.map((c) => ({
    id: `cand-${c.candidate_number}`,
    kind: shotsByCandidateNumber.has(c.candidate_number) ? "detected" : "rejected",
    time: c.time,
    candidateNumber: c.candidate_number,
    confidence: c.confidence ?? null,
    peakAmplitude: c.peak_amplitude ?? null,
    note: "",
  }));
  // Manual shots: those without a matching candidate_number.
  // Derived (promoted) fixtures may include shots with ``time: null`` for
  // anchor shots that the secondary couldn't snap; skip those here so the
  // marker drawer doesn't crash on ``time.toFixed(...)``.
  for (const s of audit.shots ?? []) {
    if (s.time == null) continue;
    if (s.candidate_number == null || s.source === "manual") {
      markers.push({
        id: `manual-shot-${s.shot_number}`,
        kind: "manual",
        time: s.time,
        candidateNumber: s.candidate_number ?? null,
        confidence: null,
        peakAmplitude: null,
        note: "",
      });
    }
  }
  return markers;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.000";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds - Math.floor(seconds)) * 1000);
  return `${m}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`;
}

