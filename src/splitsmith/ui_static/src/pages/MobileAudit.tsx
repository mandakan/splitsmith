/**
 * Mobile audit screen (#700 follow-up, mobile audit UI program).
 *
 * A full-viewport takeover: a wrapped waveform (the whole stage on one
 * screen, playhead sweeping row to row) above a zoom lane that scrubs
 * the target band around the playhead. There is no selection state -
 * whichever marker falls in the band around the playhead is the target,
 * and the action area names exactly what it will do to it (promote a
 * rejected candidate, nudge/delete a kept shot, or add a new one).
 *
 * Mirrors the desktop Audit.tsx event vocabulary (marker_time_changed,
 * marker_rejected, marker_kept, marker_deleted, marker_added_manual) so
 * the two screens' audit_events logs read as one history.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, MoreHorizontal, X } from "lucide-react";

import { ActionArea } from "@/components/audit/mobile/ActionArea";
import { AuditTransport } from "@/components/audit/mobile/AuditTransport";
import { CameraStack } from "@/components/audit/mobile/CameraStack";
import { JUMP_TITLE, JumpSheet } from "@/components/audit/mobile/JumpSheet";
import { DEFAULT_ROWS, WrappedWaveform } from "@/components/audit/mobile/WrappedWaveform";
import { ZoomLane, type ZoomFactor } from "@/components/audit/mobile/ZoomLane";
import type { AuditMarker } from "@/components/MarkerLayer";
import { DesktopCommandLine } from "@/components/desktop/DesktopCommandLine";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Snackbar, type SnackState } from "@/components/Snackbar";
import { Button } from "@/components/ui/button";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { Portal } from "@/components/ui/Portal";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import {
  ApiError,
  api,
  capabilityDenied,
  type AuditEvent,
  type PeaksResult,
  type StageAudit,
  type StageVideo,
} from "@/lib/api";
import { addFailedKind, auditPipCameras, type AuditPipCamera, type FailedKinds } from "@/lib/auditPip";
import { buildAuditJson, deriveMarkers } from "@/lib/audit-doc";
import { resolveTarget } from "@/lib/audit-target";
import { useDialogFocus } from "@/lib/dialogFocus";
import { isActiveCommand, latestForStage, REDETECT_CONFIRM } from "@/lib/desktopCommands";
import { useDesktopCommands } from "@/lib/useDesktopCommands";
import { useMatchHref } from "@/lib/matchHref";
import { computeAuditNextStep } from "@/lib/audit-next-step";
import { jumpShooters, nextTarget, type AuditTarget } from "@/lib/mobileAuditNav";
import { PEAK_SNAP_TOLERANCE_S, type SnapPeaks } from "@/lib/peak-snap";
import { footFor, footReadout, readSnapOn, seatAllMoves, writeSnapOn } from "@/lib/riseFoot";
import { stackCameras } from "@/lib/phoneCameraStack";
import { createScrubber, type Scrubber } from "@/lib/scrub-audio";
import { useAuditPlayback } from "@/lib/useAuditPlayback";
import { useScrubSource } from "@/lib/useScrubSource";

const PEAKS_BINS = 8192;
// Shots are placed against 1 ms peaks, as on desktop Audit, so a seat lands
// on the same time on both (lib/riseFoot). Capped like desktop's.
const SNAP_PEAKS_MAX_BINS = 131_072;

interface PeaksError {
  status: number | null;
  text: string;
}

function peaksErrorFrom(err: unknown): PeaksError {
  if (err instanceof ApiError && err.status === 404) {
    return { status: 404, text: "Waiting for the desktop to sync this stage's audio" };
  }
  const detail = err instanceof ApiError ? err.detail : err instanceof Error ? err.message : String(err);
  return { status: err instanceof ApiError ? err.status : null, text: `Audio failed to load: ${detail}` };
}

function saveErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function MobileAudit() {
  const { slug = "", stage } = useParams();
  const navigate = useNavigate();
  const href = useMatchHref();
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();

  const stageNumber = stage != null ? Number(stage) : null;
  const readOnly = capabilityDenied(outletCtx?.capabilities, "review");
  // A desktop-synced match: detection runs on the desktop, so the phone
  // asks it to (#1100) instead of offering a local re-detect.
  const desktopMirror = outletCtx?.origin === "desktop" && !readOnly;
  const desktop = useDesktopCommands(desktopMirror);
  const stageCommand =
    desktopMirror && stageNumber != null ? latestForStage(desktop.commands, slug, stageNumber) : null;
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmRedetect, setConfirmRedetect] = useState(false);

  // undefined = not loaded yet; null = confirmed no audit doc for this stage.
  const [audit, setAudit] = useState<StageAudit | null | undefined>(undefined);
  // Set only when the getStageAudit request itself failed (network error,
  // 5xx, ...) - distinct from the server's honest "no audit yet" 200-null
  // response. Conflating the two told the operator to re-run detection on
  // a stage that was actually already audited (#757 follow-up).
  const [auditError, setAuditError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const [peaksResult, setPeaksResult] = useState<PeaksResult | null>(null);
  const [peaksError, setPeaksError] = useState<PeaksError | null>(null);
  // 1 ms peaks for the rise foot; null until they arrive (or when they fail,
  // which leaves adds exactly at the playhead and Seat disabled).
  const [footPeaks, setFootPeaks] = useState<SnapPeaks | null>(null);
  // Snap added shots to their rise foot: on until turned off (the same
  // setting as desktop Audit's, per browser).
  const [snapOn, setSnapOn] = useState(readSnapOn);
  // Where each shot was last placed (an add or a nudge): where its foot is
  // looked for. A seat never moves it (lib/riseFoot).
  const [placedAt, setPlacedAt] = useState<Record<string, number>>({});
  const [seatAllPending, setSeatAllPending] = useState<Array<{ id: string; from: number; to: number }> | null>(
    null,
  );

  const [heldId, setHeldId] = useState<string | null>(null);
  const [nudgeMs, setNudgeMs] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [snack, setSnack] = useState<SnackState | null>(null);
  const [zoom, setZoom] = useState<ZoomFactor>(3);
  const [videoOpen, setVideoOpen] = useState(false);
  const [confirmDeleteMarker, setConfirmDeleteMarker] = useState<AuditMarker | null>(null);
  // Where to go once unsaved edits are discarded (Back, or a stage picked
  // in the title's picker); null while no discard question is open.
  const [pendingNav, setPendingNav] = useState<(() => void) | null>(null);
  const confirmDiscard = pendingNav != null;
  const [jumpOpen, setJumpOpen] = useState(false);

  const sessionEvents = useRef<AuditEvent[]>([]);

  // The desktop's result arrives through sync: reload the stage when its
  // request turns succeeded, so the new shots show without a manual refresh.
  const lastCommandStatus = useRef<string | null>(null);
  useEffect(() => {
    const status = stageCommand?.status ?? null;
    const wasActive = lastCommandStatus.current === "pending" || lastCommandStatus.current === "claimed";
    if (wasActive && status === "succeeded") setReloadToken((n) => n + 1);
    lastCommandStatus.current = status;
  }, [stageCommand?.status]);
  const scrubberRef = useRef<Scrubber | null>(null);
  const videoElRef = useRef<HTMLVideoElement | null>(null);
  const videoPanelRef = useRef<HTMLDivElement | null>(null);

  const audioSrc = stageNumber != null ? api.stageAudioUrl(slug, stageNumber) : null;
  const playback = useAuditPlayback(audioSrc);

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEvents.current.push({ ts: new Date().toISOString(), kind, payload });
    setDirty(true);
  }, []);

  // Load the audit doc + peaks on mount / stage change. Independent
  // requests - a peaks failure must not block the audit doc from
  // rendering, and vice versa (#757 distinction between "no audit yet"
  // and "audio not synced yet").
  useEffect(() => {
    if (!slug || stageNumber == null) return undefined;
    let alive = true;
    setAudit(undefined);
    setAuditError(null);
    setMarkers([]);
    setPeaksResult(null);
    setPeaksError(null);
    setFootPeaks(null);
    setPlacedAt({});

    api
      .getStageAudit(slug, stageNumber)
      .then((doc) => {
        if (!alive) return;
        setAudit(doc);
        setMarkers(deriveMarkers(doc));
      })
      .catch((err) => {
        if (!alive) return;
        setAudit(undefined);
        setAuditError(saveErrorMessage(err));
      });

    api
      .getStagePeaks(slug, stageNumber, PEAKS_BINS)
      .then((res) => {
        if (!alive) return;
        setPeaksResult(res);
      })
      .catch((err) => {
        if (!alive) return;
        setPeaksError(peaksErrorFrom(err));
      });

    return () => {
      alive = false;
    };
  }, [slug, stageNumber, reloadToken]);

  // Scrubber lifecycle: create once the audio src is known, dispose on
  // unmount / src change. Every failure path degrades to silent
  // seeking (createScrubber never throws), so no error handling here.
  useEffect(() => {
    let disposed = false;
    scrubberRef.current = null;
    if (audioSrc == null) return undefined;
    createScrubber(audioSrc).then((s) => {
      if (disposed) {
        s?.dispose();
        return;
      }
      scrubberRef.current = s;
    });
    return () => {
      disposed = true;
      scrubberRef.current?.dispose();
      scrubberRef.current = null;
    };
  }, [audioSrc]);

  // Held target clears on any real playhead movement. Nudges never call
  // playback.seek/playFrom, so a change here can only mean the operator
  // scrubbed, tapped, or played elsewhere.
  useEffect(() => {
    setHeldId(null);
    setNudgeMs(0);
  }, [playback.playhead]);

  const target = useMemo(
    () => resolveTarget(markers, playback.playhead, heldId),
    [markers, playback.playhead, heldId],
  );
  const targetId = target.kind === "none" ? null : target.marker.id;

  const keptMarkers = useMemo(() => markers.filter((m) => m.kind !== "rejected"), [markers]);
  const keptSorted = useMemo(
    () => keptMarkers.slice().sort((a, b) => a.time - b.time),
    [keptMarkers],
  );
  const beep = peaksResult?.beep_time ?? (audit ? audit.beep_time ?? null : null);

  const shotOrdinal = useMemo(() => {
    if (target.kind !== "shot") return null;
    const idx = keptSorted.findIndex((m) => m.id === target.marker.id);
    if (idx === -1) return null;
    return { index: idx + 1, total: keptSorted.length };
  }, [target, keptSorted]);

  const splitS = useMemo(() => {
    if (target.kind !== "shot") return null;
    const idx = keptSorted.findIndex((m) => m.id === target.marker.id);
    if (idx <= 0) return beep != null ? target.marker.time - beep : null;
    return target.marker.time - keptSorted[idx - 1].time;
  }, [target, keptSorted, beep]);

  // Fetch the 1 ms peaks once the stage's duration is known.
  useEffect(() => {
    if (!peaksResult || !slug || stageNumber == null) return undefined;
    const bins = Math.min(SNAP_PEAKS_MAX_BINS, Math.max(PEAKS_BINS, Math.ceil(peaksResult.duration / 0.001)));
    if (bins <= PEAKS_BINS) {
      setFootPeaks({ peaks: peaksResult.peaks, duration: peaksResult.duration });
      return undefined;
    }
    let alive = true;
    api
      .getStagePeaks(slug, stageNumber, bins)
      .then((res) => {
        if (alive) setFootPeaks({ peaks: res.peaks, duration: res.duration });
      })
      .catch(() => {
        if (alive) setFootPeaks(null);
      });
    return () => {
      alive = false;
    };
  }, [peaksResult, slug, stageNumber]);

  const placedFrom = useCallback((m: AuditMarker) => placedAt[m.id] ?? m.time, [placedAt]);
  const targetFoot =
    target.kind === "shot" ? footFor(placedFrom(target.marker), footPeaks) : null;
  // The rule looks PEAK_SNAP_TOLERANCE_S either side of where the shot was
  // placed; a shot further from its sound has no foot to seat on, and the
  // readout says so rather than leaving Seat disabled without a reason.
  const foot =
    target.kind !== "shot" || footPeaks == null
      ? null
      : (footReadout(target.marker.time, targetFoot) ?? {
          text: `No rise foot within ${Math.round(PEAK_SNAP_TOLERANCE_S * 1000)} ms`,
          onRule: false,
        });
  const canSeat = targetFoot != null && foot != null && !foot.onRule;

  // ---- Gestures -----------------------------------------------------------

  // The loop follows the shot the operator selects: while it is on, a tap,
  // a drag or an add that lands on a different shot re-centres it there.
  // Only gestures move it, never playback sweeping across the shots
  // inside the loop.
  const loopShotRef = useRef<string | null>(null);
  const followLoop = useCallback(
    (t: number) => {
      if (playback.loop == null) return;
      const sel = resolveTarget(markers, t, null);
      if (sel.kind !== "shot" || sel.marker.id === loopShotRef.current) return;
      loopShotRef.current = sel.marker.id;
      playback.moveLoop(sel.marker.time);
    },
    [playback, markers],
  );
  const lastScrubRef = useRef<number | null>(null);

  // A tap moves the playhead; only Play plays.
  const handleTap = useCallback(
    (t: number) => {
      playback.seek(t);
      followLoop(t);
    },
    [playback, followLoop],
  );
  const handleGrabStart = useCallback(() => playback.stop(), [playback]);
  const handleScrub = useCallback(
    (t: number) => {
      playback.seek(t);
      scrubberRef.current?.grainAt(t);
      lastScrubRef.current = t;
    },
    [playback],
  );
  const handleGrabEnd = useCallback(() => {
    // Two-verb rule: grab-start stops, grab-end does nothing to playback.
    // Playback stays stopped until the next explicit Play.
    if (lastScrubRef.current != null) followLoop(lastScrubRef.current);
    lastScrubRef.current = null;
  }, [followLoop]);

  // ---- Actions --------------------------------------------------------------

  const handleNudge = useCallback(
    (deltaMs: -10 | 10) => {
      if (target.kind !== "shot" || readOnly || saving) return;
      const m = target.marker;
      const fromTime = m.time;
      const toTime = fromTime + deltaMs / 1000;
      recordEvent("marker_time_changed", { id: m.id, from_time: fromTime, to_time: toTime });
      setMarkers((prev) => prev.map((x) => (x.id === m.id ? { ...x, time: toTime } : x)));
      setPlacedAt((prev) => ({ ...prev, [m.id]: toTime }));
      setHeldId(m.id);
      setNudgeMs((n) => n + deltaMs);
    },
    [target, readOnly, saving, recordEvent],
  );

  const handleDeleteShot = useCallback(() => {
    if (target.kind !== "shot" || readOnly || saving) return;
    const m = target.marker;
    if (m.kind === "manual") {
      setConfirmDeleteMarker(m);
      return;
    }
    recordEvent("marker_rejected", { id: m.id, time: m.time, candidate_number: m.candidateNumber });
    setMarkers((prev) => prev.map((x) => (x.id === m.id ? { ...x, kind: "rejected" } : x)));
  }, [target, readOnly, saving, recordEvent]);

  const confirmDeleteManual = useCallback(() => {
    if (saving) return;
    const m = confirmDeleteMarker;
    if (!m) return;
    recordEvent("marker_deleted", { id: m.id, time: m.time, kind: "manual" });
    setMarkers((prev) => prev.filter((x) => x.id !== m.id));
    setConfirmDeleteMarker(null);
  }, [confirmDeleteMarker, saving, recordEvent]);

  const handlePromote = useCallback(() => {
    if (target.kind !== "candidate" || readOnly || saving) return;
    const m = target.marker;
    recordEvent("marker_kept", { id: m.id, time: m.time, candidate_number: m.candidateNumber });
    setMarkers((prev) => prev.map((x) => (x.id === m.id ? { ...x, kind: "detected" } : x)));
  }, [target, readOnly, saving, recordEvent]);

  const handleAddShot = useCallback(() => {
    if (target.kind !== "none" || readOnly || saving) return;
    const snapped = snapOn ? footFor(playback.playhead, footPeaks) : null;
    const t = snapped ?? playback.playhead;
    const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    recordEvent("marker_added_manual", { id, time: t });
    setPlacedAt((prev) => ({ ...prev, [id]: playback.playhead }));
    if (playback.loop != null) {
      loopShotRef.current = id;
      playback.moveLoop(t);
    }
    setMarkers((prev) => [
      ...prev,
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
  }, [target, readOnly, saving, snapOn, footPeaks, playback, recordEvent]);

  // Seat: the selected shot onto its rise foot. Held, like a nudge, so the
  // target stays on it while the playhead does not move.
  const handleSeat = useCallback(() => {
    if (target.kind !== "shot" || readOnly || saving || targetFoot == null) return;
    const m = target.marker;
    if (Math.abs(targetFoot - m.time) < 0.0005) return;
    const from = placedFrom(m);
    recordEvent("marker_time_changed", { id: m.id, from_time: m.time, to_time: targetFoot, reason: "rise_foot" });
    setMarkers((prev) => prev.map((x) => (x.id === m.id ? { ...x, time: targetFoot } : x)));
    setPlacedAt((prev) => ({ ...prev, [m.id]: from }));
    setHeldId(m.id);
    setNudgeMs(0);
  }, [target, readOnly, saving, targetFoot, placedFrom, recordEvent]);

  const askSeatAll = useCallback(() => {
    setSeatAllPending(seatAllMoves(markers, footPeaks, placedFrom));
  }, [markers, footPeaks, placedFrom]);
  const seatAll = useCallback(() => {
    const moves = seatAllPending ?? [];
    setSeatAllPending(null);
    if (moves.length === 0 || readOnly || saving) return;
    const to = new Map(moves.map((x) => [x.id, x.to]));
    setPlacedAt((prev) => {
      const next = { ...prev };
      for (const x of moves) next[x.id] = prev[x.id] ?? x.from;
      return next;
    });
    recordEvent("rise_foot_seat_all", { moved: moves.length });
    setMarkers((prev) => prev.map((x) => (to.has(x.id) ? { ...x, time: to.get(x.id)! } : x)));
  }, [seatAllPending, readOnly, saving, recordEvent]);
  const toggleSnap = useCallback(() => {
    setSnapOn((on) => {
      writeSnapOn(!on);
      return !on;
    });
  }, []);

  const handleLoopToggle = useCallback(() => {
    const anchor = target.kind === "shot" ? target.marker.time : playback.playhead;
    loopShotRef.current = target.kind === "shot" ? target.marker.id : null;
    playback.toggleLoop(anchor);
  }, [target, playback]);

  // ---- Video overlay --------------------------------------------------------

  // The stage's cameras, primary first and the others by when they were
  // added (desktop Audit's order, so "Cam 2" names the same camera).
  const stageVideos = useMemo<StageVideo[]>(() => {
    const stageEntry = outletCtx?.project?.stages.find((s) => s.stage_number === stageNumber);
    if (!stageEntry) return [];
    const primary = stageEntry.videos.find((v) => v.role === "primary");
    if (!primary) return [];
    const secondaries = stageEntry.videos
      .filter((v) => v.role === "secondary")
      .slice()
      .sort((a, b) => (a.added_at ?? "").localeCompare(b.added_at ?? ""));
    return [primary, ...secondaries];
  }, [outletCtx?.project, stageNumber]);
  const primaryVideo = stageVideos[0] ?? null;

  // The trim pin may play the 720p rendition (lib/scrubSource.ts); a
  // playback error falls back to the trim for that video.
  const { choose: chooseScrub, markFailed: markScrubFailed } = useScrubSource();
  const videoUrl = useMemo(() => {
    // Keyed off peaksResult.trimmed, not primaryVideo.processed.trim: the
    // audit timeline's marker times live in whatever file the server
    // actually served for peaks, and that flag is the only source of
    // truth for it (mirrors lib/camPlayback.ts's planServedClip, which
    // keys the primary's served-clip kind off the same peaks flag - "peaks
    // already reflect whichever file the server picked"). If the two
    // diverge, keying off the project payload's `processed.trim` instead
    // would silently serve the wrong file while `marker.time - 1.5` is
    // still trimmed-clip-local. Also gates the Video button on peaks
    // being loaded at all, since there is no defensible kind to seek into
    // before that - the seek-into-nothing case.
    if (!primaryVideo || !peaksResult) return null;
    if (!peaksResult.trimmed) return api.videoStreamUrl(slug, primaryVideo.path, "auto", null, stageNumber);
    const choice = chooseScrub(primaryVideo);
    return api.videoStreamUrl(slug, primaryVideo.path, choice.kind, choice.version, stageNumber);
  }, [primaryVideo, peaksResult, slug, chooseScrub, stageNumber]);

  // Where the dialog's video starts, captured when Video is pressed. The tap
  // that selected the shot also started its audio, so the playhead-derived
  // target moves on before the video's metadata arrives; reading it then
  // opened the video at 0 or at a later shot (#1419).
  const videoSeekRef = useRef(0);

  const handleShowVideo = useCallback(() => {
    if (!videoUrl) return;
    videoSeekRef.current = target.kind === "shot" ? Math.max(0, target.marker.time - 1.5) : 0;
    playback.stop();
    setVideoOpen(true);
  }, [videoUrl, target, playback]);

  const closeVideo = useCallback(() => setVideoOpen(false), []);

  // Two or more cameras with a beep: the dialog stacks them (#1410), each
  // streaming what desktop Audit's PiP streams for it (lib/auditPip: the
  // 720p rendition first), synced on the beep offsets. Fewer: the one
  // primary player below, unchanged.
  const [stackFailed, setStackFailed] = useState<FailedKinds>({});
  const preBufferSeconds = outletCtx?.project?.trim_pre_buffer_seconds ?? 5;
  const stacked = useMemo(() => {
    if (!peaksResult || stageVideos.length < 2) return null;
    return stackCameras(
      auditPipCameras({
        slug,
        stageNumber,
        videos: stageVideos,
        peaks: { beep_time: peaksResult.beep_time, trimmed: peaksResult.trimmed },
        preBufferSeconds,
        failed: stackFailed,
      }),
    );
  }, [peaksResult, stageVideos, slug, stageNumber, preBufferSeconds, stackFailed]);
  const onStackCameraError = useCallback(
    (cam: AuditPipCamera) => {
      setStackFailed((prev) => addFailedKind(prev, cam.id, cam.kind));
      // A broken rendition is broken for the one-camera player too.
      const video = stageVideos.find((v) => v.video_id === cam.id);
      if (cam.kind === "scrub" && video) markScrubFailed(video);
    },
    [stageVideos, markScrubFailed],
  );

  // Same overlay-architecture contract as MobileConfirmSheet: Escape
  // closes, Tab is trapped inside, focus moves in on open and restores
  // to the trigger on close.
  useDialogFocus(videoOpen, videoPanelRef, closeVideo);

  const handleVideoLoadedMetadata = useCallback(() => {
    const el = videoElRef.current;
    if (!el) return;
    el.currentTime = videoSeekRef.current;
  }, []);

  // ---- Save -----------------------------------------------------------------

  const handleSave = useCallback(async (): Promise<boolean> => {
    if (!audit || stageNumber == null || readOnly || saving) return false;
    setSaving(true);
    const keptCount = markers.filter((m) => m.kind === "detected" || m.kind === "manual").length;
    const saveEvent: AuditEvent = {
      ts: new Date().toISOString(),
      kind: "save",
      payload: { shots_count: keptCount },
    };
    const payload = buildAuditJson({
      base: audit,
      stage: {
        stage_number: stageNumber,
        stage_name: audit.stage_name,
        time_seconds: audit.stage_time_seconds ?? 0,
      },
      primaryBeepInClip: peaksResult?.beep_time ?? audit.beep_time ?? null,
      markers,
      appendEvents: [...sessionEvents.current, saveEvent],
    });
    try {
      const saved = await api.saveStageAudit(slug, stageNumber, payload);
      setAudit(saved);
      setMarkers(deriveMarkers(saved));
      sessionEvents.current = [];
      setDirty(false);
      outletCtx?.refresh?.();
      setSnack({ message: "Saved", tone: "status" });
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        try {
          const fresh = await api.getStageAudit(slug, stageNumber);
          setAudit(fresh);
          setMarkers(deriveMarkers(fresh));
          sessionEvents.current = [];
          setDirty(false);
          setSnack({
            message: "This stage changed elsewhere - reloaded, local edits were discarded",
            tone: "error",
          });
        } catch (reloadErr) {
          // The reload itself failed - do not clear sessionEvents/dirty,
          // there is nothing fresh to have replaced them with, and the
          // operator's edits are the only copy of this work.
          setSnack({
            message: `Save conflicted and the reload failed - check your connection and retry (${saveErrorMessage(reloadErr)})`,
            tone: "error",
          });
        }
      } else if (err instanceof ApiError && err.status === 403) {
        setSnack({
          message: "Save refused - this mirror's audit gate should be open. This is a bug.",
          tone: "error",
        });
      } else {
        setSnack({ message: `Save failed: ${saveErrorMessage(err)}`, tone: "error" });
      }
    } finally {
      setSaving(false);
    }
    return false;
  }, [audit, stageNumber, readOnly, saving, markers, peaksResult, slug, outletCtx]);

  // ---- Back navigation -------------------------------------------------------

  // Back returns to the page this audit was opened from. Stepping between
  // stages (Save & next, the picker) replaces the history entry and carries
  // whether there is a page behind it, so Back skips the stages stepped
  // through; opened directly, with nothing behind it, Back goes to the
  // match Overview.
  const location = useLocation();
  const canGoBack =
    (location.state as { canGoBack?: boolean } | null)?.canGoBack ?? location.key !== "default";
  const goBack = useCallback(() => {
    if (canGoBack) navigate(-1);
    else navigate(href(""));
  }, [canGoBack, navigate, href]);
  const openStage = useCallback(
    (t: AuditTarget) => navigate(href("audit", t.slug, String(t.stage)), { replace: true, state: { canGoBack } }),
    [navigate, href, canGoBack],
  );

  // Desktop auto-saves on navigation; mobile has no such safety net, so a
  // dirty stage must not lose edits to one mis-tap of the back button.
  const handleBack = useCallback(() => {
    if (dirty) {
      setPendingNav(() => goBack);
      return;
    }
    goBack();
  }, [dirty, goBack]);

  const confirmDiscardAndBack = useCallback(() => {
    const go = pendingNav;
    setPendingNav(null);
    go?.();
  }, [pendingNav]);

  // The conveyor, in desktop Audit's order: the shooter's next stage with
  // footage, then the next shooter with stages left, then back.
  const shooters = useMemo(() => outletCtx?.shooters ?? [], [outletCtx?.shooters]);
  const stageOptions = useMemo(
    () =>
      (outletCtx?.project?.stages ?? [])
        .filter((s) => s.videos.some((v) => v.role === "primary"))
        .map((s) => ({ stageNumber: s.stage_number, stageName: s.stage_name }))
        .sort((a, b) => a.stageNumber - b.stageNumber),
    [outletCtx?.project?.stages],
  );
  const nextStep = useMemo(
    () => computeAuditNextStep({ shooters, activeSlug: slug, stages: stageOptions, activeStage: stageNumber }),
    [shooters, slug, stageOptions, stageNumber],
  );
  const next = nextTarget(nextStep, shooters);
  const handleSaveNext = useCallback(async () => {
    if (!(await handleSave())) return;
    if (next) openStage(next);
    else goBack();
  }, [handleSave, next, openStage, goBack]);

  const handleJump = useCallback(
    (target: AuditTarget) => {
      setJumpOpen(false);
      if (dirty) setPendingNav(() => () => openStage(target));
      else openStage(target);
    },
    [dirty, openStage],
  );

  // ---- No-stage-param: plain stage list, no takeover ------------------------

  if (stageNumber == null) {
    const stages = (outletCtx?.project?.stages ?? []).filter((s) =>
      s.videos.some((v) => v.role === "primary"),
    );
    return (
      <div className="mx-auto max-w-md p-4">
        <PageHeader title="Audit" />
        {stages.length === 0 ? (
          <p className="text-sm text-muted">No stages with footage yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {stages.map((s) => (
              <li key={s.stage_number}>
                <Link
                  to={href("audit", slug, String(s.stage_number))}
                  className="flex min-h-11 items-center rounded-md border border-rule px-3 font-mono text-sm text-ink"
                >
                  Stage {s.stage_number} - {s.stage_name}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <>
      <Portal>
        <div className="fixed inset-0 z-takeover flex flex-col bg-[var(--color-bg)]">
          <header className="flex min-h-11 items-center gap-2 border-b border-rule px-2">
            <button
              type="button"
              aria-label="Back"
              onClick={handleBack}
              className="flex min-h-11 min-w-11 items-center justify-center"
            >
              <ArrowLeft className="size-5" aria-hidden />
            </button>
            <button
              type="button"
              aria-label={JUMP_TITLE}
              onClick={() => setJumpOpen(true)}
              className="flex min-h-11 min-w-0 items-center gap-1 text-md font-medium text-ink"
            >
              <span className="font-mono text-sm text-led">{String(stageNumber).padStart(2, "0")}</span>
              <span className="truncate">
                {shooters.find((s) => s.slug === slug)?.name ?? "Audit"}
              </span>
              <ChevronDown className="size-4 shrink-0 text-ink-2" aria-hidden />
            </button>
            <div className="ml-auto flex items-center gap-1">
              {audit != null && (
                <>
                  <Button
                    type="button"
                    disabled={readOnly || !dirty || saving}
                    onClick={() => void handleSave()}
                    className="min-h-11 px-2.5"
                  >
                    {dirty ? "Save *" : "Save"}
                  </Button>
                  <Button
                    type="button"
                    variant="primary"
                    disabled={readOnly || saving}
                    onClick={() => void handleSaveNext()}
                    className="min-h-11 px-2.5"
                  >
                    {saving ? "Saving..." : next ? "Save & next" : "Save & finish"}
                  </Button>
                </>
              )}
              {!readOnly ? (
                <span className="relative">
                  <button
                    type="button"
                    aria-label="Stage actions"
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    onClick={() => setMenuOpen((o) => !o)}
                    className="flex min-h-11 min-w-11 items-center justify-center text-ink-2"
                  >
                    <MoreHorizontal className="size-5" aria-hidden />
                  </button>
                  <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
                    <button
                      type="button"
                      role="menuitem"
                      className={menuItemClass}
                      disabled={footPeaks == null || saving}
                      onClick={() => {
                        setMenuOpen(false);
                        askSeatAll();
                      }}
                    >
                      Seat all on rise foot&hellip;
                    </button>
                    <button
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={snapOn}
                      className={menuItemClass}
                      onClick={toggleSnap}
                    >
                      Snap to rise foot
                      <span className="ml-auto text-sm text-muted">{snapOn ? "on" : "off"}</span>
                    </button>
                    {desktopMirror ? (
                      <button
                        type="button"
                        role="menuitem"
                        className={menuItemClass}
                        disabled={stageCommand != null && isActiveCommand(stageCommand)}
                        onClick={() => {
                          setMenuOpen(false);
                          setConfirmRedetect(true);
                        }}
                      >
                        Re-detect on desktop&hellip;
                      </button>
                    ) : null}
                  </Menu>
                </span>
              ) : null}
            </div>
          </header>
          {stageCommand ? (
            <DesktopCommandLine
              command={stageCommand}
              presence={desktop.presence}
              onCancel={(id) => void desktop.cancel(id)}
              className="border-b border-rule px-3 py-1.5"
            />
          ) : null}
          {desktop.error ? (
            <p className="border-b border-rule px-3 py-1.5 text-sm text-led-text">{desktop.error}</p>
          ) : null}

          {auditError != null ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm text-muted">Could not load the audit - retry ({auditError})</p>
              <button
                type="button"
                onClick={() => setReloadToken((n) => n + 1)}
                className="min-h-11 rounded-md border border-rule px-4 py-2 text-sm text-ink"
              >
                Retry
              </button>
            </div>
          ) : audit === undefined ? (
            <div className="flex flex-1 items-center justify-center text-sm text-muted">
              Loading...
            </div>
          ) : audit === null ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm text-muted">Nothing to audit yet - run shot detection first</p>
              {desktopMirror && !(stageCommand && isActiveCommand(stageCommand)) ? (
                <Button type="button" className="min-h-11" onClick={() => setConfirmRedetect(true)}>
                  Detect on desktop
                </Button>
              ) : null}
              <Link to={href("")} className="min-h-11 rounded-md border border-rule px-4 py-2 text-sm text-ink">
                Overview
              </Link>
            </div>
          ) : (
            <>
              {peaksResult ? (
                <WrappedWaveform
                  peaks={peaksResult.peaks}
                  duration={peaksResult.duration}
                  rows={DEFAULT_ROWS}
                  playhead={playback.playhead}
                  markers={keptMarkers}
                  targetId={targetId}
                  loop={playback.loop}
                  onTap={handleTap}
                  onGrabStart={handleGrabStart}
                  onScrub={handleScrub}
                  onGrabEnd={handleGrabEnd}
                />
              ) : peaksError ? (
                <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted">
                  {peaksError.text}
                </div>
              ) : (
                <div className="flex flex-1 items-center justify-center text-sm text-muted">
                  Loading audio...
                </div>
              )}

              {peaksResult && (
                <ZoomLane
                  peaks={peaksResult.peaks}
                  duration={peaksResult.duration}
                  rows={DEFAULT_ROWS}
                  playhead={playback.playhead}
                  zoom={zoom}
                  onZoomChange={setZoom}
                  markers={markers}
                  targetId={targetId}
                  onTap={handleTap}
                  onGrabStart={handleGrabStart}
                  onJog={handleScrub}
                  onGrabEnd={handleGrabEnd}
                />
              )}

              <AuditTransport
                playing={playback.playing}
                onPlayPause={() =>
                  playback.playing ? playback.stop() : playback.playFrom(playback.playhead)
                }
                loopActive={playback.loop != null}
                onLoopToggle={handleLoopToggle}
                speed={playback.speed}
                onSpeedChange={playback.setSpeed}
              />
              <ActionArea
                target={target}
                shotOrdinal={shotOrdinal}
                splitS={splitS}
                nudgeMs={nudgeMs}
                readOnly={readOnly || saving}
                hasVideo={videoUrl != null}
                onNudge={handleNudge}
                foot={foot}
                canSeat={canSeat}
                onSeat={footPeaks ? handleSeat : undefined}
                onDeleteShot={handleDeleteShot}
                onShowVideo={handleShowVideo}
                onPromote={handlePromote}
                onAddShot={handleAddShot}
              />
            </>
          )}
        </div>

        {videoOpen && videoUrl && (
          <div
            ref={videoPanelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Shot video"
            tabIndex={-1}
            className="fixed inset-0 z-modal flex flex-col bg-black outline-none"
          >
            <div className="flex justify-end p-2">
              <button
                type="button"
                aria-label="Close"
                onClick={closeVideo}
                className="flex min-h-11 min-w-11 items-center justify-center text-ink"
              >
                <X className="size-5" aria-hidden />
              </button>
            </div>
            {stacked ? (
              <CameraStack
                cameras={stacked}
                openAt={videoSeekRef.current - (stacked[0].beepInClip ?? 0)}
                onCameraError={onStackCameraError}
              />
            ) : (
              <video
                ref={videoElRef}
                src={videoUrl}
                controls
                playsInline
                className="min-h-0 flex-1"
                onLoadedMetadata={handleVideoLoadedMetadata}
                onError={() => {
                  if (primaryVideo && videoUrl.includes("kind=scrub")) markScrubFailed(primaryVideo);
                }}
              />
            )}
          </div>
        )}

        <JumpSheet
          open={jumpOpen}
          shooters={jumpShooters(shooters, slug)}
          currentStage={stageNumber}
          stageNames={Object.fromEntries(stageOptions.map((s) => [s.stageNumber, s.stageName]))}
          onPick={(pickSlug, pickStage) => handleJump({ slug: pickSlug, stage: pickStage })}
          onClose={() => setJumpOpen(false)}
        />

        <MobileConfirmSheet
          open={confirmDeleteMarker != null}
          title="Delete manual shot"
          body={
            confirmDeleteMarker
              ? `This removes the manual shot at ${confirmDeleteMarker.time.toFixed(3)} s.`
              : ""
          }
          confirmLabel="Delete"
          confirmDisabled={saving}
          onConfirm={confirmDeleteManual}
          onCancel={() => setConfirmDeleteMarker(null)}
        />

        <MobileConfirmSheet
          open={seatAllPending != null}
          title={
            seatAllPending == null || seatAllPending.length === 0
              ? "Every shot is on its rise foot"
              : `Seat ${seatAllPending.length} of ${keptSorted.length} shots on their rise foot?`
          }
          body={
            seatAllPending == null || seatAllPending.length === 0
              ? "Nothing to move."
              : `They move by up to ${Math.round(
                  Math.max(...seatAllPending.map((x) => Math.abs(x.to - x.from))) * 1000,
                )} ms. Leaving without saving puts them back.`
          }
          confirmLabel={seatAllPending == null || seatAllPending.length === 0 ? "OK" : "Seat them"}
          confirmDisabled={saving}
          onConfirm={seatAll}
          onCancel={() => setSeatAllPending(null)}
        />

        <MobileConfirmSheet
          open={confirmRedetect}
          title={REDETECT_CONFIRM.title}
          body={REDETECT_CONFIRM.body}
          confirmLabel={REDETECT_CONFIRM.confirmLabel}
          onConfirm={() => {
            setConfirmRedetect(false);
            if (stageNumber != null) void desktop.requestRedetect(slug, stageNumber);
          }}
          onCancel={() => setConfirmRedetect(false)}
        />

        <MobileConfirmSheet
          open={confirmDiscard}
          title="Discard unsaved edits?"
          body={`Stage ${stageNumber} has ${sessionEvents.current.length} unsaved edit${
            sessionEvents.current.length === 1 ? "" : "s"
          }. Going back without saving discards them.`}
          confirmLabel="Discard"
          onConfirm={confirmDiscardAndBack}
          onCancel={() => setPendingNav(null)}
        />
      </Portal>
      <Snackbar snack={snack} onDismiss={() => setSnack(null)} />
    </>
  );
}
