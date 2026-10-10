/**
 * Standalone fixture review (#19, folded into the production UI as part
 * of #15 step 6).
 *
 * Reads a single audit fixture (a JSON file with sibling .wav + optional
 * video) and edits it in place. No project context, no stages, no jobs.
 * Same primitives as the project-mode audit screen so the UX stays
 * identical: <Waveform> + <MarkerLayer> over the top, <ShotStepper> +
 * <ListDrawer> for navigation, Cmd+S to save, Cmd+Z to undo.
 *
 * URL: /review?fixture=<absolute-or-relative-path>&video=<optional-path>
 *
 * The CLI command ``splitsmith review fixture.json [--video x.mp4]``
 * launches the production UI server and opens this page (Step 6 of #15
 * also rewires the CLI; the page itself is fully driven by query params).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  CheckCircle2,
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
import { ShotStepper } from "@/components/ShotStepper";
import { Walk } from "@/components/review/Walk";
import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
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
  type PeaksResult,
  type StageAudit,
} from "@/lib/api";
import { deriveMarkers } from "@/lib/audit-doc";
import { buildFixtureJson } from "@/lib/fixtureDoc";
import { useDecodedAudio } from "@/lib/useDecodedAudio";
import { isTypingTextTarget, useBlurOnPointerClick } from "@/lib/audit-input";
import { zoomActionForKey } from "@/lib/zoomKeys";
import { placeTime, type SnapPeaks } from "@/lib/peak-snap";
import { displayBins, reviewMaxZoom } from "@/lib/reviewZoom";
import {
  BEEP_GUARD_S,
  defaultScope,
  isSnapped,
  WALK_DECIDED_EVENT,
  WALK_METHOD,
  nextFixtureToReview,
  readGuideOpen,
  walkHref,
  writeGuideOpen,
  type WalkScope,
} from "@/lib/walk";
import { useReleaseMediaOnUnmount } from "@/lib/utils";

const PEAK_BINS = 1500;
/** The fixture peaks route's cap: 1 ms bins on a clip up to ~131 s. */
const FIXTURE_PEAKS_MAX_BINS = 131_072;
const MAX_UNDO = 50;

/** Region-loop window around the focused marker (#29): 0.5 s of pre-roll
 *  to hear the shot coming, 0.7 s of tail to catch echo/AGC behavior. */
const LOOP_PRE_S = 0.5;
const LOOP_POST_S = 0.7;

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

export function Review() {
  const [params] = useSearchParams();
  const fixturePath = params.get("fixture");
  const videoPath = params.get("video");
  const navigate = useNavigate();
  // The walk (lib/walk): every candidate, manual shot and unmarked burst,
  // one stop at a time. ``?walk=1`` opens it, which is how the review queue
  // and the sign-off's "next fixture" link here (``?step=1``, the old link,
  // still does).
  // The walk's guide sits under the waveform, so the stop and its context
  // stay together on screen; open until hidden once (lib/walk).
  const [guideOpen, setGuideOpen] = useState(readGuideOpen);
  const toggleGuide = useCallback(() => {
    setGuideOpen((open) => {
      writeGuideOpen(!open);
      return !open;
    });
  }, []);
  const [videoLarge, setVideoLarge] = useState(false);
  // What the walk visits: set once per loaded fixture (editing shots must not
  // flip it mid-walk), overridden by the walk's switch.
  const [initialScope, setInitialScope] = useState<WalkScope>("all");
  const [scopeOverride, setScopeOverride] = useState<WalkScope | null>(null);
  const walkScope = scopeOverride ?? initialScope;
  useEffect(() => {
    if (!videoLarge) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setVideoLarge(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [videoLarge]);
  const walkParam = params.get("walk") === "1" || params.get("step") === "1";
  const [walkMode, setWalkMode] = useState(walkParam);
  useEffect(() => {
    setWalkMode(walkParam);
  }, [fixturePath, walkParam]);
  // The walk draws the wave itself: the fixture's samples, decoded once.
  const decodedAudio = useDecodedAudio(walkMode && fixturePath ? api.fixtureAudioUrl(fixturePath) : null);

  // Drop button / chip focus after a mouse click so the next Space press
  // toggles playback instead of re-clicking the last-touched control.
  useBlurOnPointerClick();

  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [auditLoaded, setAuditLoaded] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);
  // Peaks drawn at the current zoom (about one bin per pixel, down to 1 ms);
  // ``null`` until fetched, then the last fetched set stays up while a
  // deeper one loads.
  const [displayPeaks, setDisplayPeaks] = useState<{ bins: number; peaks: number[] } | null>(
    null,
  );

  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const [focusedMarkerId, setFocusedMarkerId] = useState<string | null>(null);
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [showDrawer, setShowDrawer] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const undoStackRef = useRef<AuditMarker[][]>([]);

  const sessionEventsRef = useRef<AuditEvent[]>([]);
  // The save path's view of the page (see performSave): the document as last
  // loaded or saved, the newest markers, the save queue, and which fixture
  // the document belongs to.
  const auditRef = useRef(audit);
  auditRef.current = audit;
  const markersRef = useRef(markers);
  markersRef.current = markers;
  const saveChainRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const loadedPathRef = useRef<string | null>(null);
  const isDirtyRef = useRef(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });

  // Single-element playback (no multi-video). Audio drives time when
  // there's no video; the video drives when present.
  //
  // Coordinate-system note: the trimmed sibling WAV has its origin at
  // ``fixture_window_in_source[0]`` of the source video. The waveform
  // and all audit markers live in that trimmed coordinate space. When a
  // video is bound, we offset reads / writes of ``video.currentTime``
  // by ``videoOffset`` so scrubbing the waveform at audio_t lands the
  // video at audio_t + offset (and vice versa).
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  useReleaseMediaOnUnmount(audioRef);
  useReleaseMediaOnUnmount(videoRef);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [loopMode, setLoopMode] = useState(false);
  // Anchor for loop-to-start semantics: position where playback last
  // started (or where the user last scrubbed). On pause / end-of-clip
  // while loopMode is on, the playhead snaps back here.
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
  const [zoom, setZoom] = useState<number | null>(null);
  // Callback ref attaches the ResizeObserver exactly when the wrapper
  // mounts. The wrapper is inside a {peaks ? ...} conditional render;
  // a useEffect-based observer with [] deps would run before peaks
  // load, see a null ref, bail, and never re-fire -- leaving zoom
  // permanently a no-op.
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

  const visibleKinds = useMemo(() => {
    const kinds = visibleKindsFromFilters(filters);
    // The walk visits rejected candidates too; hiding them below would make
    // the two views disagree about what exists.
    if (peeking || walkMode) kinds.add("rejected");
    // Keep a keyboard-focused rejected marker visible even when the
    // rejected filter is off, so `n`-stepping onto it never focuses
    // an invisible marker (the user can then K it back to kept).
    if (focusedMarkerId) {
      const f = markers.find((x) => x.id === focusedMarkerId);
      if (f?.kind === "rejected") kinds.add("rejected");
    }
    return kinds;
  }, [filters, peeking, walkMode, focusedMarkerId, markers]);

  // Load fixture JSON.
  useEffect(() => {
    if (!fixturePath) {
      setAuditLoaded(true);
      setAuditError("Missing ?fixture=<path> query parameter");
      return;
    }
    let alive = true;
    setAuditLoaded(false);
    setAuditError(null);
    // A new fixture starts a new session: nothing of the last one's events,
    // undo or dirtiness may reach this one's file, and no save runs until
    // this fixture's own document is in hand.
    loadedPathRef.current = null;
    sessionEventsRef.current = [];
    undoStackRef.current = [];
    isDirtyRef.current = false;
    api
      .getFixtureAudit(fixturePath)
      .then((a) => {
        if (!alive) return;
        auditRef.current = a;
        loadedPathRef.current = fixturePath;
        setAudit(a);
        setMarkers(deriveMarkers(a));
        setScopeOverride(null);
        setInitialScope(
          defaultScope({
            snapped: isSnapped(a as never),
            kept: (a.shots ?? []).filter((s) => s.time != null).length,
            expectedRounds: (a as unknown as { stage_rounds?: { expected?: number } }).stage_rounds?.expected ?? null,
          }),
        );
        setAuditLoaded(true);
      })
      .catch((err) => {
        if (!alive) return;
        setAudit(null);
        setMarkers([]);
        setAuditLoaded(true);
        setAuditError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      alive = false;
    };
  }, [fixturePath]);

  // Load peaks.
  useEffect(() => {
    if (!fixturePath) return;
    let alive = true;
    setPeaksLoading(true);
    setPeaksError(null);
    api
      .getFixturePeaks(fixturePath, PEAK_BINS)
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
  }, [fixturePath]);

  // Waveform detail follows the zoom: a fixture shot is placed on its onset
  // by eye, which the 1500 fit bins (~35 ms each on a long stage) cannot
  // show. Debounced so stepping through zoom levels fetches once.
  const pixelsPerSecond = peaks
    ? zoomToPixelsPerSecond(zoom, waveformViewport, peaks.duration)
    : null;
  const wantedBins = peaks ? displayBins(peaks.duration, pixelsPerSecond, PEAK_BINS) : PEAK_BINS;
  useEffect(() => {
    setDisplayPeaks(null);
  }, [fixturePath]);
  useEffect(() => {
    if (!peaks || !fixturePath || wantedBins <= PEAK_BINS) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      api
        .getFixturePeaks(fixturePath, wantedBins)
        .then((p) => {
          if (alive) setDisplayPeaks({ bins: wantedBins, peaks: p.peaks });
        })
        .catch(() => {
          // Keep drawing what we have; the fit peaks are always there.
        });
    }, 150);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [peaks, fixturePath, wantedBins]);
  const drawnPeaks =
    peaks && wantedBins > PEAK_BINS && displayPeaks ? displayPeaks.peaks : peaks?.peaks;

  // 1 ms peaks once per fixture: a drop or a double-click while zoomed out
  // snaps to the shot's leading edge from these (lib/peak-snap.placeTime).
  const [edgePeaks, setEdgePeaks] = useState<SnapPeaks | null>(null);
  useEffect(() => {
    setEdgePeaks(null);
    if (!peaks || !fixturePath) return;
    let alive = true;
    const bins = Math.min(FIXTURE_PEAKS_MAX_BINS, Math.max(PEAK_BINS, Math.ceil(peaks.duration / 0.001)));
    api
      .getFixturePeaks(fixturePath, bins)
      .then((p) => {
        if (alive) setEdgePeaks({ peaks: p.peaks, duration: p.duration });
      })
      .catch(() => {
        // Without them every drop lands exactly; nothing else depends on them.
      });
    return () => {
      alive = false;
    };
  }, [peaks, fixturePath]);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  // Bumped by every walk decision; an effect saves after the render that
  // carries the decision's markers, so closing the tab loses at most the
  // stop in hand.
  const [autosaveTick, setAutosaveTick] = useState(0);
  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({ ts: new Date().toISOString(), kind, payload });
    isDirtyRef.current = true;
    if (kind === WALK_DECIDED_EVENT) setAutosaveTick((n) => n + 1);
  }, []);

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
      if (m.kind === "manual") return;
      const next = m.kind === "detected" ? "rejected" : "detected";
      recordEvent(next === "detected" ? "marker_kept" : "marker_rejected", {
        id: m.id,
        time: m.time,
      });
      mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: next } : x)));
    },
    [markers, mutate, recordEvent],
  );

  const handleMarkerDelete = useCallback(
    (m: AuditMarker) => {
      if (m.kind === "manual") {
        recordEvent("marker_deleted", { id: m.id, time: m.time });
        mutate(markers.filter((x) => x.id !== m.id));
      } else if (m.kind === "detected") {
        recordEvent("marker_rejected", { id: m.id, time: m.time });
        mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: "rejected" } : x)));
      }
    },
    [markers, mutate, recordEvent],
  );

  const handleMarkerTimeChange = useCallback((id: string, time: number) => {
    setMarkers((prev) => {
      const target = prev.find((x) => x.id === id);
      if (target && target.time !== time) {
        sessionEventsRef.current.push({
          ts: new Date().toISOString(),
          kind: "marker_time_changed",
          payload: { id, from_time: target.time, to_time: time },
        });
        isDirtyRef.current = true;
      }
      const nextList = prev.map((x) => (x.id === id ? { ...x, time } : x));
      if (
        undoStackRef.current.length === 0 ||
        undoStackRef.current[undoStackRef.current.length - 1] !== prev
      ) {
        undoStackRef.current.push(prev);
        if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
      }
      return nextList;
    });
  }, []);

  const handleAddManual = useCallback(
    (time: number, shiftKey = false) => {
      // Zoomed out a new marker snaps to the shot's leading edge; zoomed in
      // (2 ms per pixel or finer) or with Shift it lands exactly.
      const pxPerSecond =
        pixelsPerSecond ?? (peaks && peaks.duration > 0 ? waveformViewport / peaks.duration : 0);
      const t = placeTime(time, { pxPerSecond, shiftKey, peaks: edgePeaks });
      const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      recordEvent("marker_added_manual", { id, time: t });
      mutate([
        ...markers,
        {
          id,
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
    [markers, mutate, recordEvent, pixelsPerSecond, peaks, waveformViewport, edgePeaks],
  );

  const handleNoteChange = useCallback((id: string, note: string) => {
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind: "note_changed",
      payload: { id, note },
    });
    isDirtyRef.current = true;
    setMarkers((prev) => prev.map((m) => (m.id === id ? { ...m, note } : m)));
  }, []);

  // Kept shots in time order.
  const keptShots = useMemo(
    () =>
      markers
        .filter((m) => m.kind === "detected" || m.kind === "manual")
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers],
  );

  useEffect(() => {
    if (keptShots.length === 0) {
      if (currentShotIndex !== 0) setCurrentShotIndex(0);
      return;
    }
    if (currentShotIndex >= keptShots.length) {
      setCurrentShotIndex(keptShots.length - 1);
    }
  }, [keptShots, currentShotIndex]);

  // ---- Playback ----------------------------------------------------------

  const playbackEl = (): HTMLMediaElement | null =>
    videoRef.current ?? audioRef.current;

  // Trimmed-audio-time → media-element-time mapping. The video element
  // plays the full untrimmed source so its time origin is the source's
  // time origin, while the waveform / markers live in the trimmed
  // window's local time. Audio element is the trimmed WAV, no offset.
  const elapsedFromMedia = useCallback(
    (mediaT: number, el: HTMLMediaElement | null) =>
      el && el === videoRef.current && audit?.fixture_window_in_source
        ? mediaT - audit.fixture_window_in_source[0]
        : mediaT,
    [audit],
  );
  const mediaFromElapsed = useCallback(
    (audioT: number, el: HTMLMediaElement | null) =>
      el && el === videoRef.current && audit?.fixture_window_in_source
        ? audioT + audit.fixture_window_in_source[0]
        : audioT,
    [audit],
  );

  const handleScrub = useCallback(
    (t: number) => {
      const el = playbackEl();
      if (el) el.currentTime = mediaFromElapsed(t, el);
      setCurrentTime(t);
      loopAnchorRef.current = t;
    },
    [mediaFromElapsed],
  );

  const togglePlay = useCallback(() => {
    const el = playbackEl();
    if (!el) return;
    if (el.paused) {
      loopAnchorRef.current = elapsedFromMedia(el.currentTime, el);
      void el.play();
      setIsPlaying(true);
    } else {
      el.pause();
      setIsPlaying(false);
      // Loop semantics: pause snaps back to region start (or play anchor if
      // no region is active).
      if (loopMode && (loopRegion != null || loopAnchorRef.current != null)) {
        const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
        el.currentTime = mediaFromElapsed(target, el);
        setCurrentTime(target);
      }
    }
  }, [loopMode, loopRegion, elapsedFromMedia, mediaFromElapsed]);

  // Stepping focus to another marker while region-looping seeks to the
  // new region's pre-roll so the "step -> loop -> K -> step" review flow
  // needs no extra scrubbing. Keyed on focusedMarkerId only: marker drags
  // recompute loopRegion each frame and must not re-trigger the seek.
  useEffect(() => {
    if (!loopMode || !isPlaying || !loopRegion) return;
    handleScrub(loopRegion.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedMarkerId]);

  // rAF loop -- pulls currentTime out of whichever element is playing
  // and translates it back into the trimmed coordinate space.
  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const el = playbackEl();
      if (el) {
        const t = elapsedFromMedia(el.currentTime, el);
        const dur = peaks?.duration ?? null;
        // Loop wrap: region end when a marker is focused, clip end otherwise.
        const regionEnd = loopRegion?.end ?? (dur != null ? dur - 0.05 : null);
        if (loopMode && regionEnd != null && t >= regionEnd) {
          const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
          el.currentTime = mediaFromElapsed(target, el);
          setCurrentTime(target);
        } else {
          setCurrentTime(t);
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, loopMode, peaks, loopRegion, elapsedFromMedia, mediaFromElapsed]);

  const stepShot = useCallback(
    (delta: number) => {
      if (keptShots.length === 0) return;
      const next = Math.min(Math.max(currentShotIndex + delta, 0), keptShots.length - 1);
      setCurrentShotIndex(next);
      setFocusedMarkerId(keptShots[next].id);
      handleScrub(keptShots[next].time);
    },
    [keptShots, currentShotIndex, handleScrub],
  );

  const allMarkersSorted = useMemo(
    () => markers.slice().sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers],
  );

  const stepAnyMarker = useCallback(
    (delta: number) => {
      if (allMarkersSorted.length === 0) return;
      let curIdx = -1;
      if (focusedMarkerId) {
        curIdx = allMarkersSorted.findIndex((m) => m.id === focusedMarkerId);
      }
      if (curIdx < 0) {
        for (let i = 0; i < allMarkersSorted.length; i++) {
          if (allMarkersSorted[i].time <= currentTime) curIdx = i;
          else break;
        }
        if (curIdx < 0) curIdx = delta > 0 ? -1 : 0;
      }
      const nextIdx = Math.min(
        Math.max(curIdx + delta, 0),
        allMarkersSorted.length - 1,
      );
      const target = allMarkersSorted[nextIdx];
      setFocusedMarkerId(target.id);
      handleScrub(target.time);
      const keptIdx = keptShots.findIndex((k) => k.id === target.id);
      if (keptIdx >= 0) setCurrentShotIndex(keptIdx);
    },
    [allMarkersSorted, focusedMarkerId, currentTime, handleScrub, keptShots],
  );

  const jumpToMarker = useCallback(
    (m: AuditMarker) => {
      setFocusedMarkerId(m.id);
      handleScrub(m.time);
      const idx = keptShots.findIndex((k) => k.id === m.id);
      if (idx >= 0) setCurrentShotIndex(idx);
    },
    [handleScrub, keptShots],
  );

  // ---- Save flow ---------------------------------------------------------

  // Saves run one at a time, each from the newest markers and the document
  // as last saved (refs, not a render's state), and each sends only the
  // events no save has written yet: the walk saves after every decision, and
  // two saves in flight must never drop one or write an older document back.
  // A save for a fixture that is not the one loaded (the page moved on to
  // the next fixture) writes nothing.
  const performSave = useCallback((): Promise<boolean> => {
    const run = async (): Promise<boolean> => {
      if (loadedPathRef.current !== fixturePath) return false;
      const base = auditRef.current;
      if (!fixturePath || !base) return false;
      const sent = sessionEventsRef.current.length;
      const sentMarkers = markersRef.current;
      const payload = buildFixtureJson({
        base,
        markers: sentMarkers,
        appendEvents: [
          ...sessionEventsRef.current.slice(0, sent),
          { ts: new Date().toISOString(), kind: "save", payload: { shots_count: 0 } },
        ],
      });
      const lastEv = payload.audit_events?.[payload.audit_events.length - 1];
      if (lastEv && lastEv.kind === "save") {
        lastEv.payload = { shots_count: payload.shots.length };
      }
      setSaveStatus({ kind: "saving" });
      try {
        const saved = await api.saveFixtureAudit(fixturePath, payload);
        auditRef.current = saved;
        setAudit(saved);
        sessionEventsRef.current = sessionEventsRef.current.slice(sent);
        isDirtyRef.current = sessionEventsRef.current.length > 0 || markersRef.current !== sentMarkers;
        setSaveStatus({ kind: "saved", at: Date.now() });
        return true;
      } catch (err) {
        setSaveStatus({
          kind: "error",
          message: err instanceof ApiError ? err.detail : String(err),
        });
        return false;
      }
    };
    const next = saveChainRef.current.then(run, run);
    saveChainRef.current = next;
    return next;
  }, [fixturePath]);

  useEffect(() => {
    if (autosaveTick > 0) void performSave();
  }, [autosaveTick, performSave]);

  useEffect(() => {
    if (saveStatus.kind !== "saved") return;
    const timer = window.setTimeout(() => setSaveStatus({ kind: "idle" }), 2500);
    return () => window.clearTimeout(timer);
  }, [saveStatus]);

  // "Mark reviewed" (#1363): the person has checked every shot on this
  // fixture's own audio. Saves first, signs off through the review queue's
  // approve route, then reloads the fixture so a later save cannot write
  // the stale review block back.
  const [marking, setMarking] = useState(false);
  const reviewStatus =
    (audit as unknown as { review?: { status?: string } } | null)?.review?.status ?? null;
  const markReviewed = useCallback(async (method?: string): Promise<boolean> => {
    if (!fixturePath) return false;
    setMarking(true);
    try {
      // A save still in flight would write the document back without the
      // sign-off: wait for it, then save what it did not carry.
      await saveChainRef.current;
      if (isDirtyRef.current && !(await performSave())) return false;
      const slug = fixturePath.split("/").pop()!.replace(/\.json$/, "");
      const { confirmed_at } = await api.confirmReviewFixture(slug, method);
      // A save replaces the whole file from this document, so the sign-off
      // goes into it at once, before the reload that confirms it: a failed
      // reload must not leave a document that a later save would write back
      // without its review block.
      const base = auditRef.current as (StageAudit & { review?: Record<string, unknown> }) | null;
      if (base) {
        const review: Record<string, unknown> = {
          ...(base.review ?? {}),
          status: "reviewed",
          reviewed_at: confirmed_at,
          confirmed_at,
        };
        if (method) review.method = method;
        else delete review.method;
        auditRef.current = { ...base, review } as StageAudit;
        setAudit(auditRef.current);
      }
      try {
        const fresh = await api.getFixtureAudit(fixturePath);
        auditRef.current = fresh;
        setAudit(fresh);
      } catch {
        // The folded-in sign-off above stands until the next load.
      }
      return true;
    } catch (err) {
      setSaveStatus({ kind: "error", message: err instanceof ApiError ? err.detail : String(err) });
      return false;
    } finally {
      setMarking(false);
    }
  }, [fixturePath, performSave]);

  // The walk's sign-off: mark reviewed (stamped with the walk's version),
  // then open the queue's next fixture that needs checking, in its walk.
  const finishWalk = useCallback(async () => {
    if (!fixturePath || !(await markReviewed(WALK_METHOD))) return;
    const slug = fixturePath.split("/").pop()!.replace(/\.json$/, "");
    try {
      const next = nextFixtureToReview((await api.getDevReviewQueue()).pending, slug);
      if (next) navigate(walkHref(next.audit_path, next.video_path ?? null));
      else setWalkMode(false);
    } catch {
      setWalkMode(false);
    }
  }, [fixturePath, markReviewed, navigate]);

  const focusFromWalk = useCallback(
    (id: string | null, time: number) => {
      setFocusedMarkerId(id);
      handleScrub(time);
    },
    [handleScrub],
  );

  const setMarkerKind = useCallback(
    (id: string, kind: "detected" | "rejected") => {
      const m = markers.find((x) => x.id === id);
      if (!m || m.kind === kind || m.kind === "manual") return;
      recordEvent(kind === "detected" ? "marker_kept" : "marker_rejected", { id, time: m.time });
      mutate(markers.map((x) => (x.id === id ? { ...x, kind } : x)));
    },
    [markers, mutate, recordEvent],
  );

  // The walk places a new shot exactly where it was decided; the editor's
  // own double-click snaps (``handleAddManual``).
  const addShotExact = useCallback(
    (time: number): string => {
      const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const t = Math.round(time * 1000) / 1000;
      recordEvent("marker_added_manual", { id, time: t });
      mutate([
        ...markers,
        { id, kind: "manual", time: t, candidateNumber: null, confidence: null, peakAmplitude: null, note: "" },
      ]);
      return id;
    },
    [markers, mutate, recordEvent],
  );

  const removeManual = useCallback(
    (id: string) => {
      const m = markers.find((x) => x.id === id);
      if (!m || m.kind !== "manual") return;
      recordEvent("marker_deleted", { id, time: m.time });
      mutate(markers.filter((x) => x.id !== id));
    },
    [markers, mutate, recordEvent],
  );

  // Space in the walk: hear the stop once, from half a second before it to
  // 0.7 s after; Space again stops it.
  const listenTimerRef = useRef<number | null>(null);
  const listenAround = useCallback(
    (t: number) => {
      const el = playbackEl();
      if (!el) return;
      if (listenTimerRef.current != null) window.clearTimeout(listenTimerRef.current);
      listenTimerRef.current = null;
      if (!el.paused) {
        el.pause();
        setIsPlaying(false);
        return;
      }
      const start = Math.max(0, t - LOOP_PRE_S);
      handleScrub(start);
      void el.play();
      setIsPlaying(true);
      listenTimerRef.current = window.setTimeout(
        () => {
          el.pause();
          setIsPlaying(false);
          listenTimerRef.current = null;
        },
        (t + LOOP_POST_S - start) * 1000,
      );
    },
    [handleScrub],
  );

  const snapDisplacementMs = useCallback(
    (markerId: string): number | null => {
      const m = markers.find((x) => x.id === markerId);
      if (!m || m.candidateNumber == null) return null;
      const shot = (audit?.shots ?? []).find((s) => s.candidate_number === m.candidateNumber) as
        | { snap_displacement_ms?: number }
        | undefined;
      return typeof shot?.snap_displacement_ms === "number" ? shot.snap_displacement_ms : null;
    },
    [markers, audit],
  );

  // The bottom waveform shows the walk's context: about six seconds around
  // the stop (it scrolls along with each stop's scrub).
  useEffect(() => {
    if (!walkMode || !peaks || peaks.duration <= 8 || waveformViewport <= 0) return;
    setZoom(Math.min(reviewMaxZoom(peaks.duration, waveformViewport), peaks.duration / 6));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walkMode, peaks, waveformViewport > 0]);

  // ---- Global hotkeys ----------------------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
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
        void performSave();
        return;
      }
      // Waveform zoom: + / 0 / - (Cmd+1/2/3 kept as aliases); see lib/zoomKeys.
      const zoomAction = zoomActionForKey(e);
      if (zoomAction) {
        e.preventDefault();
        if (zoomAction === "fit") setZoom(null);
        else if (zoomAction === "in")
          setZoom((z) =>
            Math.min(reviewMaxZoom(peaks?.duration ?? 0, waveformViewport), (z ?? 1) * 1.5),
          );
        else setZoom((z) => {
          const next = (z ?? 1) / 1.5;
          return next <= 0.25 ? null : next;
        });
        return;
      }
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
          e.preventDefault();
          setLoopMode((v) => !v);
          return;
        }
        if (e.key === "k" || e.key === "K") {
          e.preventDefault();
          let target: AuditMarker | null = null;
          if (focusedMarkerId) {
            target = markers.find((x) => x.id === focusedMarkerId) ?? null;
          }
          if (!target && keptShots.length > 0) {
            const idx = Math.min(currentShotIndex, keptShots.length - 1);
            target = keptShots[idx];
          }
          if (target) handleMarkerClick(target);
          return;
        }
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          const el = playbackEl();
          if (!el) return;
          const dir = e.key === "ArrowRight" ? 1 : -1;
          const step = e.shiftKey ? 0.025 : 0.25;
          const audioT = elapsedFromMedia(el.currentTime, el);
          const dur = peaks?.duration ?? audioT + step;
          handleScrub(Math.min(dur, Math.max(0, audioT + dir * step)));
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    togglePlay,
    undo,
    performSave,
    stepShot,
    stepAnyMarker,
    peaks,
    handleScrub,
    handleMarkerClick,
    handleMarkerTimeChange,
    focusedMarkerId,
    markers,
    keptShots,
    currentShotIndex,
    elapsedFromMedia,
    waveformViewport,
  ]);

  // Peek key handler -- separate from the main onKey effect so it can be
  // a lightweight listener that only cares about p/P down/up.
  // Guards: ignore when typing in a text field, when a modifier key is held,
  // and on auto-repeat. window blur resets peek when the user alt-tabs or
  // otherwise loses focus mid-hold.
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

  // ---- Render ------------------------------------------------------------

  if (!fixturePath) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Review</h1>
        <Card>
          <CardHeader>
            <CardTitle>Missing ?fixture parameter</CardTitle>
            <CardDescription>
              Open this page with a fixture path, e.g.{" "}
              <code className="text-xs">/review?fixture=/path/to/x.json</code>.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (auditError) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Review</h1>
        <Card>
          <CardHeader>
            <CardTitle>Failed to load fixture</CardTitle>
            <CardDescription>{auditError}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!auditLoaded || !audit) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Review</h1>
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-muted">
            <Loader2 className="size-4 animate-spin" /> Loading fixture...
          </CardContent>
        </Card>
      </div>
    );
  }

  const detectedCount = markers.filter((m) => m.kind === "detected").length;
  const rejectedCount = markers.filter((m) => m.kind === "rejected").length;
  const manualCount = markers.filter((m) => m.kind === "manual").length;

  // In the walk the video sits small beside the stop (it shows whether the
  // shooter fired); a click or the corner button enlarges it, Esc shrinks it.
  const videoBox = videoPath ? (
    <div
      className={
        videoLarge
          ? "fixed inset-6 z-50 flex items-center justify-center rounded-md bg-black/95"
          : "relative overflow-hidden rounded-md bg-black"
      }
    >
      <video
        ref={videoRef}
        src={api.fixtureVideoUrl(videoPath)}
        preload="metadata"
        playsInline
        controls={false}
        onClick={walkMode ? () => setVideoLarge((v) => !v) : undefined}
        className={
          videoLarge
            ? "max-h-full max-w-full cursor-zoom-out"
            : walkMode
              ? "block h-auto max-h-[30vh] w-full cursor-zoom-in object-contain"
              : "block h-auto w-full max-h-[60vh]"
        }
      />
      {walkMode ? (
        <button
          type="button"
          className="absolute right-2 top-2 rounded bg-black/70 px-2 py-0.5 text-xs text-ink"
          onClick={() => setVideoLarge((v) => !v)}
        >
          {videoLarge ? "Smaller (Esc)" : "Enlarge"}
        </button>
      ) : null}
    </div>
  ) : null;

  return (
    <div className="space-y-6">
      {walkMode ? null : (
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Review</h1>
            <p className="text-sm text-muted">
              Standalone fixture review. Drag the waveform to scrub. Double-click
              to add a manual marker. Press <kbd>?</kbd> for the full keyboard
              shortcuts.
            </p>
          </div>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3">
            <Crosshair className="size-5" />
            {audit.stage_name ?? "Fixture"}{" "}
            {audit.stage_number != null ? (
              <Badge variant="outline">stage {audit.stage_number}</Badge>
            ) : null}
            {audit.beep_time != null ? (
              <Badge variant="outline">beep at {audit.beep_time.toFixed(3)}s</Badge>
            ) : null}
          </CardTitle>
          {walkMode ? null : (
            <CardDescription>
              <code className="text-xs">{fixturePath}</code>
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {walkMode && peaks && edgePeaks ? (
            <Walk
              key={`${fixturePath}:${walkScope}`}
              markers={markers}
              peaks={edgePeaks}
              savedEvents={audit.audit_events ?? []}
              from={(audit.beep_time ?? 0) + BEEP_GUARD_S}
              expectedRounds={
                (audit as unknown as { stage_rounds?: { expected?: number } }).stage_rounds?.expected ?? null
              }
              snapDisplacementMs={snapDisplacementMs}
              onSetTime={handleMarkerTimeChange}
              onSetKind={setMarkerKind}
              onAddShot={addShotExact}
              onRemove={removeManual}
              onRecord={recordEvent}
              onFocus={focusFromWalk}
              onListen={listenAround}
              onDone={() => void finishWalk()}
              onExit={() => setWalkMode(false)}
              busy={marking}
              guideOpen={guideOpen}
              onToggleGuide={toggleGuide}
              audio={decodedAudio}
              aside={videoBox}
              scope={walkScope}
              onScopeChange={setScopeOverride}
            />
          ) : walkMode && peaks ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" /> Loading the 1 ms waveform for the walk...
            </div>
          ) : null}
          {videoPath ? (
            walkMode ? null : videoBox
          ) : (
            <audio
              ref={audioRef}
              src={api.fixtureAudioUrl(fixturePath)}
              preload="metadata"
              className="sr-only"
            />
          )}

          {peaksLoading ? (
            <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" /> Computing waveform...
            </div>
          ) : peaksError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              Couldn't load peaks: {peaksError}
            </div>
          ) : peaks && !walkMode ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <FilterBar
                  filters={walkMode ? { ...filters, rejected: true } : filters}
                  counts={{
                    detected: detectedCount,
                    rejected: rejectedCount,
                    manual: manualCount,
                    beep: peaks.beep_time != null ? 1 : 0,
                  }}
                  onChange={setFilters}
                  peeking={peeking}
                  onPeekStart={() => setPeeking(true)}
                  onPeekEnd={() => setPeeking(false)}
                />
                <ZoomControls
                  zoom={zoom}
                  onZoomChange={setZoom}
                  maxZoom={reviewMaxZoom(peaks.duration, waveformViewport)}
                />
              </div>
              <div ref={waveformWrapperRef}>
                <Waveform
                  peaks={drawnPeaks ?? peaks.peaks}
                  duration={peaks.duration}
                  currentTime={currentTime}
                  beepTime={filters.beep ? peaks.beep_time : null}
                  loopRegion={loopRegion}
                  pixelsPerSecond={pixelsPerSecond}
                  onScrub={handleScrub}
                  onDoubleClick={handleAddManual}
                  height={160}
                >
                  <MarkerLayer
                    markers={markers}
                    duration={peaks.duration}
                    focusedId={focusedMarkerId}
                    onFocusChange={setFocusedMarkerId}
                    onClick={handleMarkerClick}
                    onDelete={handleMarkerDelete}
                    onTimeChange={handleMarkerTimeChange}
                    visibleKinds={visibleKinds}
                    snapPeaks={edgePeaks ?? undefined}
                  />
                </Waveform>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={togglePlay}
                  aria-label={isPlaying ? "Pause" : "Play"}
                >
                  {isPlaying ? (
                    <Pause className="size-4" />
                  ) : (
                    <Play className="size-4" />
                  )}
                </Button>
                <Button
                  variant={loopMode ? "default" : "outline"}
                  size="sm"
                  onClick={() => setLoopMode((v) => !v)}
                  aria-pressed={loopMode}
                  title="Loop (R) - repeats around the focused shot when one is selected"
                  aria-label={loopMode ? "Loop on (R)" : "Loop off (R)"}
                >
                  <Repeat className="size-4" />
                </Button>
                <span className="font-mono text-muted">
                  {formatTime(currentTime)} / {formatTime(peaks.duration)}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={undo}
                  disabled={undoStackRef.current.length === 0}
                  aria-label="Undo (Cmd+Z)"
                >
                  <Undo2 className="size-4" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void performSave()}
                  disabled={saveStatus.kind === "saving"}
                  aria-label="Save (Cmd+S)"
                >
                  {saveStatus.kind === "saving" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : saveStatus.kind === "saved" ? (
                    <CheckCircle2 className="size-4" />
                  ) : (
                    <Save className="size-4" />
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void markReviewed()}
                  disabled={marking || reviewStatus === "reviewed"}
                  title="Every shot checked on this fixture's own audio: sign it off"
                >
                  {marking ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
                  {reviewStatus === "reviewed" ? "Reviewed" : "Mark reviewed"}
                </Button>
                {!walkMode ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setWalkMode(true)}
                    disabled={!edgePeaks}
                    title="Visit every candidate, shot and unmarked sound in time order, one key each"
                  >
                    Walk
                  </Button>
                ) : null}
                <span className="ml-auto flex items-center gap-3 text-xs text-muted">
                  <span>{detectedCount} detected</span>
                  <span>{rejectedCount} rejected</span>
                  <span>{manualCount} manual</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowDrawer((v) => !v)}
                    aria-label="Toggle marker drawer (L)"
                    aria-pressed={showDrawer}
                  >
                    <ListChecks className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowHelp(true)}
                    aria-label="Keyboard shortcuts (?)"
                    title="Keyboard shortcuts (?)"
                  >
                    <HelpCircle className="size-4" />
                  </Button>
                </span>
              </div>
              <ShotStepper
                shots={keptShots}
                currentIndex={currentShotIndex}
                onStep={stepShot}
                onNoteChange={handleNoteChange}
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      <ListDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        markers={markers}
        currentMarkerId={focusedMarkerId}
        onJumpTo={jumpToMarker}
      />
      <HelpOverlay
        open={showHelp}
        onClose={() => setShowHelp(false)}
        mode="review"
      />
      <SaveToast status={saveStatus} />
    </div>
  );
}

function SaveToast({ status }: { status: SaveStatus }) {
  let label = "";
  let tone = "";
  if (status.kind === "saving") {
    label = "Saving fixture...";
    tone = "bg-surface text-ink";
  } else if (status.kind === "saved") {
    label = "Fixture saved";
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

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.000";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds - Math.floor(seconds)) * 1000);
  return `${m}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`;
}
