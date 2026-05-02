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
import { useSearchParams } from "react-router-dom";
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
  type PeaksResult,
  type StageAudit,
} from "@/lib/api";
import { isTypingTextTarget, useBlurOnPointerClick } from "@/lib/audit-input";

const PEAK_BINS = 1500;
const MAX_UNDO = 50;

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

export function Review() {
  const [params] = useSearchParams();
  const fixturePath = params.get("fixture");
  const videoPath = params.get("video");

  // Drop button / chip focus after a mouse click so the next Space press
  // toggles playback instead of re-clicking the last-touched control.
  useBlurOnPointerClick();

  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [auditLoaded, setAuditLoaded] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const [focusedMarkerId, setFocusedMarkerId] = useState<string | null>(null);
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [showDrawer, setShowDrawer] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const undoStackRef = useRef<AuditMarker[][]>([]);

  const sessionEventsRef = useRef<AuditEvent[]>([]);
  const isDirtyRef = useRef(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });

  // Single-element playback (no multi-video). Audio drives time when
  // there's no video; the video drives when present.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [loopMode, setLoopMode] = useState(false);
  // Anchor for loop-to-start semantics: position where playback last
  // started (or where the user last scrubbed). On pause / end-of-clip
  // while loopMode is on, the playhead snaps back here.
  const loopAnchorRef = useRef<number | null>(null);
  const [filters, setFilters] = useState<MarkerFilters>(DEFAULT_FILTERS);
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

  const visibleKinds = useMemo(() => visibleKindsFromFilters(filters), [filters]);

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
    api
      .getFixtureAudit(fixturePath)
      .then((a) => {
        if (!alive) return;
        setAudit(a);
        setMarkers(deriveMarkers(a));
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

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({ ts: new Date().toISOString(), kind, payload });
    isDirtyRef.current = true;
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

  const handleScrub = useCallback((t: number) => {
    const el = playbackEl();
    if (el) el.currentTime = t;
    setCurrentTime(t);
    loopAnchorRef.current = t;
  }, []);

  const togglePlay = useCallback(() => {
    const el = playbackEl();
    if (!el) return;
    if (el.paused) {
      loopAnchorRef.current = el.currentTime;
      void el.play();
      setIsPlaying(true);
    } else {
      el.pause();
      setIsPlaying(false);
      if (loopMode && loopAnchorRef.current != null) {
        const target = loopAnchorRef.current;
        el.currentTime = target;
        setCurrentTime(target);
      }
    }
  }, [loopMode]);

  // rAF loop -- pulls currentTime out of whichever element is playing.
  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const el = playbackEl();
      if (el) {
        const t = el.currentTime;
        const dur = peaks?.duration ?? null;
        if (loopMode && dur != null && t >= dur - 0.05) {
          const target = loopAnchorRef.current ?? 0;
          el.currentTime = target;
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
  }, [isPlaying, loopMode, peaks]);

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

  const performSave = useCallback(async (): Promise<boolean> => {
    if (!fixturePath || !audit) return false;
    const appendEvents = sessionEventsRef.current;
    const payload = buildAuditJson({
      base: audit,
      markers,
      appendEvents: [
        ...appendEvents,
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
      setAudit(saved);
      sessionEventsRef.current = [];
      isDirtyRef.current = false;
      setSaveStatus({ kind: "saved", at: Date.now() });
      return true;
    } catch (err) {
      setSaveStatus({
        kind: "error",
        message: err instanceof ApiError ? err.detail : String(err),
      });
      return false;
    }
  }, [fixturePath, audit, markers]);

  useEffect(() => {
    if (saveStatus.kind !== "saved") return;
    const timer = window.setTimeout(() => setSaveStatus({ kind: "idle" }), 2500);
    return () => window.clearTimeout(timer);
  }, [saveStatus]);

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
      if ((e.metaKey || e.ctrlKey) && (e.key === "1" || e.key === "2" || e.key === "3")) {
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
          const dur = peaks?.duration ?? el.currentTime + step;
          handleScrub(Math.min(dur, Math.max(0, el.currentTime + dir * step)));
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
  ]);

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
          <CardContent className="flex items-center gap-2 py-6 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading fixture...
          </CardContent>
        </Card>
      </div>
    );
  }

  const detectedCount = markers.filter((m) => m.kind === "detected").length;
  const rejectedCount = markers.filter((m) => m.kind === "rejected").length;
  const manualCount = markers.filter((m) => m.kind === "manual").length;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review</h1>
          <p className="text-sm text-muted-foreground">
            Standalone fixture review. Drag the waveform to scrub. Double-click
            to add a manual marker. Press <kbd>?</kbd> for the full keyboard
            shortcuts.
          </p>
        </div>
      </div>

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
          <CardDescription>
            <code className="text-xs">{fixturePath}</code>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {videoPath ? (
            <div className="overflow-hidden rounded-md bg-black">
              <video
                ref={videoRef}
                src={api.fixtureVideoUrl(videoPath)}
                preload="metadata"
                playsInline
                controls={false}
                className="block h-auto w-full max-h-[60vh]"
              />
            </div>
          ) : (
            <audio
              ref={audioRef}
              src={api.fixtureAudioUrl(fixturePath)}
              preload="metadata"
              className="sr-only"
            />
          )}

          {peaksLoading ? (
            <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Computing waveform...
            </div>
          ) : peaksError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              Couldn't load peaks: {peaksError}
            </div>
          ) : peaks ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <FilterBar
                  filters={filters}
                  counts={{
                    detected: detectedCount,
                    rejected: rejectedCount,
                    manual: manualCount,
                  }}
                  onChange={setFilters}
                />
                <ZoomControls zoom={zoom} onZoomChange={setZoom} />
              </div>
              <div ref={waveformWrapperRef}>
                <Waveform
                  peaks={peaks.peaks}
                  duration={peaks.duration}
                  currentTime={currentTime}
                  beepTime={filters.beep ? peaks.beep_time : null}
                  pixelsPerSecond={zoomToPixelsPerSecond(
                    zoom,
                    waveformViewport,
                    peaks.duration,
                  )}
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
                  title="Loop the fixture (R)"
                  aria-label={loopMode ? "Loop on (R)" : "Loop off (R)"}
                >
                  <Repeat className="size-4" />
                </Button>
                <span className="font-mono text-muted-foreground">
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
                <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
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
    tone = "bg-card text-foreground";
  } else if (status.kind === "saved") {
    label = "Fixture saved";
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

function buildAuditJson(opts: {
  base: StageAudit;
  markers: AuditMarker[];
  appendEvents: AuditEvent[];
}): StageAudit {
  const { base, markers, appendEvents } = opts;
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const beep = base.beep_time ?? null;
  const shots: AuditShot[] = kept.map((m, i) => ({
    shot_number: i + 1,
    candidate_number: m.candidateNumber,
    time: round3(m.time),
    ms_after_beep: beep != null ? Math.round((m.time - beep) * 1000) : 0,
    source: m.kind === "manual" ? "manual" : "detected",
    ...(m.note ? { note: m.note } : {}),
  })) as (AuditShot & { note?: string })[];

  const previousEvents = base.audit_events ?? [];
  return {
    ...base,
    shots,
    audit_events: [...previousEvents, ...appendEvents],
  };
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
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
  for (const s of audit.shots ?? []) {
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
