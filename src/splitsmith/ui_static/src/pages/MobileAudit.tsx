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
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft, X } from "lucide-react";

import { ActionArea } from "@/components/audit/mobile/ActionArea";
import { AuditTransport } from "@/components/audit/mobile/AuditTransport";
import { DEFAULT_ROWS, WrappedWaveform } from "@/components/audit/mobile/WrappedWaveform";
import { ZoomLane, type ZoomFactor } from "@/components/audit/mobile/ZoomLane";
import type { AuditMarker } from "@/components/MarkerLayer";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Snackbar, type SnackState } from "@/components/Snackbar";
import { Portal } from "@/components/ui/Portal";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import {
  ApiError,
  api,
  capabilityDenied,
  type AuditEvent,
  type PeaksResult,
  type StageAudit,
} from "@/lib/api";
import { buildAuditJson, deriveMarkers } from "@/lib/audit-doc";
import { resolveTarget } from "@/lib/audit-target";
import { useDialogFocus } from "@/lib/dialogFocus";
import { useMatchHref } from "@/lib/matchHref";
import { snapToPeak, type SnapPeaks } from "@/lib/peak-snap";
import { createScrubber, type Scrubber } from "@/lib/scrub-audio";
import { useAuditPlayback } from "@/lib/useAuditPlayback";

const PEAKS_BINS = 8192;

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
  const { matchId, slug = "", stage } = useParams();
  const navigate = useNavigate();
  const href = useMatchHref();
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();

  const stageNumber = stage != null ? Number(stage) : null;
  const readOnly = capabilityDenied(outletCtx?.capabilities, "review");

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

  const [heldId, setHeldId] = useState<string | null>(null);
  const [nudgeMs, setNudgeMs] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [snack, setSnack] = useState<SnackState | null>(null);
  const [zoom, setZoom] = useState<ZoomFactor>(3);
  const [videoOpen, setVideoOpen] = useState(false);
  const [confirmDeleteMarker, setConfirmDeleteMarker] = useState<AuditMarker | null>(null);

  const sessionEvents = useRef<AuditEvent[]>([]);
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

  const snapPeaks: SnapPeaks | null = useMemo(
    () => (peaksResult ? { peaks: peaksResult.peaks, duration: peaksResult.duration } : null),
    [peaksResult],
  );

  // ---- Gestures -----------------------------------------------------------

  const handleTap = useCallback((t: number) => playback.playFrom(t), [playback]);
  const handleGrabStart = useCallback(() => playback.stop(), [playback]);
  const handleScrub = useCallback(
    (t: number) => {
      playback.seek(t);
      scrubberRef.current?.grainAt(t);
    },
    [playback],
  );
  const handleGrabEnd = useCallback(() => {
    // Two-verb rule: grab-start stops, grab-end does nothing. Playback
    // stays stopped until the next explicit tap/play.
  }, []);

  // ---- Actions --------------------------------------------------------------

  const handleNudge = useCallback(
    (deltaMs: -10 | 10) => {
      if (target.kind !== "shot" || readOnly || saving) return;
      const m = target.marker;
      const fromTime = m.time;
      const toTime = fromTime + deltaMs / 1000;
      recordEvent("marker_time_changed", { id: m.id, from_time: fromTime, to_time: toTime });
      setMarkers((prev) => prev.map((x) => (x.id === m.id ? { ...x, time: toTime } : x)));
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
    const snapped = snapPeaks ? snapToPeak(playback.playhead, snapPeaks) : null;
    const t = snapped ?? playback.playhead;
    const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    recordEvent("marker_added_manual", { id, time: t });
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
  }, [target, readOnly, saving, snapPeaks, playback.playhead, recordEvent]);

  const handleLoopToggle = useCallback(() => {
    const anchor = target.kind === "shot" ? target.marker.time : playback.playhead;
    playback.toggleLoop(anchor);
  }, [target, playback]);

  // ---- Video overlay --------------------------------------------------------

  const primaryVideo = useMemo(() => {
    const stageEntry = outletCtx?.project?.stages.find((s) => s.stage_number === stageNumber);
    if (!stageEntry) return null;
    return stageEntry.videos.find((v) => v.role === "primary") ?? null;
  }, [outletCtx?.project, stageNumber]);

  const videoUrl = useMemo(() => {
    if (!primaryVideo) return null;
    return api.videoStreamUrl(slug, primaryVideo.path, primaryVideo.processed.trim ? "trim" : "auto");
  }, [primaryVideo, slug]);

  const handleShowVideo = useCallback(() => {
    if (videoUrl) setVideoOpen(true);
  }, [videoUrl]);

  const closeVideo = useCallback(() => setVideoOpen(false), []);

  // Same overlay-architecture contract as MobileConfirmSheet: Escape
  // closes, Tab is trapped inside, focus moves in on open and restores
  // to the trigger on close.
  useDialogFocus(videoOpen, videoPanelRef, closeVideo);

  const handleVideoLoadedMetadata = useCallback(() => {
    const el = videoElRef.current;
    if (!el) return;
    const seekTo = target.kind === "shot" ? Math.max(0, target.marker.time - 1.5) : 0;
    el.currentTime = seekTo;
  }, [target]);

  // ---- Save -----------------------------------------------------------------

  const handleSave = useCallback(async () => {
    if (!audit || stageNumber == null || readOnly || saving) return;
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
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const fresh = await api.getStageAudit(slug, stageNumber);
        setAudit(fresh);
        setMarkers(deriveMarkers(fresh));
        sessionEvents.current = [];
        setDirty(false);
        setSnack({
          message: "This stage changed elsewhere - reloaded, local edits were discarded",
          tone: "error",
        });
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
  }, [audit, stageNumber, readOnly, saving, markers, peaksResult, slug, outletCtx]);

  // ---- No-stage-param: plain stage list, no takeover ------------------------

  if (stageNumber == null) {
    const stages = (outletCtx?.project?.stages ?? []).filter((s) =>
      s.videos.some((v) => v.role === "primary"),
    );
    return (
      <div className="mx-auto max-w-md p-4">
        <h1 className="mb-4 font-display text-lg uppercase tracking-wide">Audit</h1>
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
              onClick={() => navigate(`/match/${matchId}/results/${slug}/${stageNumber}`)}
              className="flex min-h-11 min-w-11 items-center justify-center"
            >
              <ArrowLeft className="size-5" aria-hidden />
            </button>
            <span className="font-display text-sm uppercase tracking-wide">
              Audit . stage {stageNumber}
            </span>
            {audit != null && (
              <button
                type="button"
                disabled={readOnly || !dirty || saving}
                onClick={handleSave}
                className="btn-led-fill ml-auto min-h-11 rounded-md px-4 disabled:opacity-50"
              >
                {saving ? "Saving..." : dirty ? "Save *" : "Save"}
              </button>
            )}
          </header>

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
              <Link to={href("jobs")} className="min-h-11 rounded-md border border-rule px-4 py-2 text-sm text-ink">
                Go to jobs
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
            <video
              ref={videoElRef}
              src={videoUrl}
              controls
              playsInline
              className="min-h-0 flex-1"
              onLoadedMetadata={handleVideoLoadedMetadata}
            />
          </div>
        )}

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
      </Portal>
      <Snackbar snack={snack} onDismiss={() => setSnack(null)} />
    </>
  );
}
