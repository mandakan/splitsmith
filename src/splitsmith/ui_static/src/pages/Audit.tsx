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
import { useNavigate, useParams } from "react-router-dom";
import { CheckCircle2, Crosshair, ListChecks, Loader2, Pause, Play, Save, Undo2 } from "lucide-react";

import { ListDrawer } from "@/components/ListDrawer";
import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
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
  type StageAudit,
  type StageVideo,
} from "@/lib/api";

const PEAK_BINS = 1500;
const MAX_UNDO = 50;

export function Audit() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

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
  const rafRef = useRef<number | null>(null);

  const stageNumber = useMemo(() => {
    if (stageParam == null) return null;
    const n = Number.parseInt(stageParam, 10);
    return Number.isFinite(n) ? n : null;
  }, [stageParam]);

  // Load project once.
  useEffect(() => {
    let alive = true;
    api
      .getProject()
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

  const stagesWithPrimary = useMemo(() => {
    if (!project) return [];
    return project.stages.filter((s) => s.videos.some((v) => v.role === "primary"));
  }, [project]);

  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    navigate(`/audit/${stagesWithPrimary[0].stage_number}`, { replace: true });
  }, [stageNumber, stagesWithPrimary, navigate]);

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
    undoStackRef.current = [];
    sessionEventsRef.current = [];
    isDirtyRef.current = false;
    setSaveStatus({ kind: "idle" });
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
      .getStagePeaks(stageNumber, PEAK_BINS)
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
  }, [stageNumber, primary]);

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
      .getStageAudit(stageNumber)
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
  }, [stageNumber]);

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
      if (v) setCurrentTime(v.currentTime - beepOffset);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, beepOffset]);

  const handleScrub = useCallback(
    (primaryTime: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = primaryTime + beepOffset;
      setCurrentTime(primaryTime);
    },
    [beepOffset],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      void v.play();
      setIsPlaying(true);
    } else {
      v.pause();
      setIsPlaying(false);
    }
  }, []);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind,
      payload,
    });
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

  const handleMarkerTimeChange = useCallback(
    (id: string, time: number) => {
      // Drag-during-pointer-move calls this many times. Coalesce by replacing
      // the head of the undo stack on the same id while a drag is active --
      // simplest: only push to undo when the time actually differs from the
      // current snapshot. Here we use an inline check.
      setMarkers((prev) => {
        const target = prev.find((x) => x.id === id);
        if (target && target.time !== time) {
          // One event per drag commit (rAF-coalesced). Aggregating
          // pointer-move ticks into a single event would need extra
          // glue; the log stays useful even with a few entries per drag.
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
    },
    [],
  );

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
      const next = Math.min(Math.max(currentShotIndex + delta, 0), keptShots.length - 1);
      setCurrentShotIndex(next);
      setFocusedMarkerId(keptShots[next].id);
      handleScrub(keptShots[next].time);
    },
    [keptShots, currentShotIndex, handleScrub],
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
    async (opts: { silent?: boolean } = {}): Promise<boolean> => {
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
        const saved = await api.saveStageAudit(stageNumber, payload);
        setAudit(saved);
        sessionEventsRef.current = [];
        isDirtyRef.current = false;
        setSaveStatus({ kind: "saved", at: Date.now() });
        return true;
      } catch (err) {
        const message = err instanceof ApiError ? err.detail : String(err);
        setSaveStatus({ kind: "error", message });
        return false;
      }
    },
    [stageNumber, stage, peaks, primary, audit, markers],
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
      navigate(`/audit/${n}`);
    },
    [navigate, performSave, stageNumber],
  );

  // ---- Global keyboard shortcuts -----------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");

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
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void performSave();
        return;
      }
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "m" || e.key === "M") {
          e.preventDefault();
          stepShot(e.shiftKey ? -1 : 1);
          return;
        }
        if (e.key === "l" || e.key === "L") {
          e.preventDefault();
          setShowDrawer((v) => !v);
          return;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, undo, stepShot, performSave]);

  const videoSrc = activeVideo ? api.videoStreamUrl(activeVideo.path) : "";

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
    <div className="space-y-6">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
          <p className="text-sm text-muted-foreground">
            Drag the waveform to scrub. Double-click to add a manual marker.
            Click a marker to toggle keep/reject. M / Shift+M step shots,
            L toggles the marker list, Cmd+Z undoes.
          </p>
        </div>
        <StageSelector
          stages={stagesWithPrimary.map((s) => ({
            stageNumber: s.stage_number,
            stageName: s.stage_name,
          }))}
          selected={stageNumber ?? null}
          onSelect={(n) => void navigateToStage(n)}
        />
      </div>

      {stage && primary ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Stage {stage.stage_number} -- {stage.stage_name}
              {primary.beep_time != null ? (
                <Badge variant="outline">beep at {primary.beep_time.toFixed(3)}s</Badge>
              ) : (
                <Badge variant="destructive">no beep yet</Badge>
              )}
              {videos.length > 1 ? (
                <Badge variant="secondary">{videos.length} videos</Badge>
              ) : null}
              {auditLoaded && audit ? (
                <Badge variant="outline">audit loaded</Badge>
              ) : auditLoaded ? (
                <Badge variant="outline">no audit yet</Badge>
              ) : null}
              {peaks && !peaks.trimmed ? (
                <TrimNowBadge
                  stageNumber={stage.stage_number}
                  hasBeep={primary.beep_time != null}
                  hasStageTime={stage.time_seconds > 0}
                  onProjectUpdate={(p) => {
                    setProject(p);
                    // Re-fetch peaks now that the trimmed clip exists.
                    if (stageNumber != null) {
                      api
                        .getStagePeaks(stageNumber, PEAK_BINS)
                        .then((np) => setPeaks(np))
                        .catch(() => {});
                    }
                  }}
                />
              ) : null}
            </CardTitle>
            <CardDescription>
              Primary: <code className="text-xs">{primary.path}</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <VideoPanel
              ref={videoRef}
              videos={videos}
              primaryBeepTime={primaryBeep}
              activeIndex={activeVideoIndex}
              onActiveIndexChange={setActiveVideoIndex}
              videoSrc={videoSrc}
            />
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
                <Waveform
                  peaks={peaks.peaks}
                  duration={peaks.duration}
                  currentTime={currentTime}
                  beepTime={auditBeep}
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
                  />
                </Waveform>
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
                  <span className="font-mono text-muted-foreground">
                    {formatTime(currentTime)} / {formatTime(peaks.duration)}
                  </span>
                  {beepOffset !== 0 ? (
                    <span className="text-xs text-muted-foreground">
                      (cam offset {beepOffset >= 0 ? "+" : ""}
                      {beepOffset.toFixed(3)}s)
                    </span>
                  ) : null}
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
                    title="Save (Cmd+S)"
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
                      title="Marker list (L)"
                      aria-pressed={showDrawer}
                    >
                      <ListChecks className="size-4" />
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
      ) : null}
      <ListDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        markers={markers}
        currentMarkerId={focusedMarkerId}
        onJumpTo={jumpToMarker}
      />
      <SaveToast status={saveStatus} />
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
  onSelect: (n: number) => void;
}

function StageSelector({ stages, selected, onSelect }: StageSelectorProps) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Stage</span>
      <select
        className="rounded-md border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        value={selected ?? ""}
        onChange={(e) => onSelect(Number.parseInt(e.target.value, 10))}
      >
        {stages.map((s) => (
          <option key={s.stageNumber} value={s.stageNumber}>
            {s.stageNumber} -- {s.stageName}
          </option>
        ))}
      </select>
    </label>
  );
}

interface TrimNowBadgeProps {
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  onProjectUpdate: (p: MatchProject) => void;
}

function TrimNowBadge({
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
  useEffect(() => {
    let cancelled = false;
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
          if (final.status === "succeeded") onProjectUpdate(await api.getProject());
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
      const initial = await api.trimStage(stageNumber);
      setJob(initial);
      const final = await api.pollJob(initial.id, setJob);
      if (final.status === "failed") {
        setError(final.error ?? "Trim failed");
        return;
      }
      const fresh = await api.getProject();
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
        title={reason ?? "Re-encode with short GOP for scrub-friendly playback"}
      >
        {running ? <Loader2 className="size-3 animate-spin" /> : null}
        {running ? job?.message ?? "Trimming..." : "Trim now"}
        {running && pct != null ? ` (${pct}%)` : null}
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
