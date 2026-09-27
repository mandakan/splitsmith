/**
 * MobileBeepReview - the mobile beep review card pager (slice 3, #326
 * follow-up). Desktop's BeepReview is a list + detail layout that
 * doesn't fit a phone viewport; this renders one card for the active
 * item from {@link useBeepQueue} instead, with Prev/Next replacing the
 * sidebar list.
 *
 * Media source picks in this priority order, matching what the backend
 * actually made available for this item:
 *   1. `proxy_ready` - hosted-native or local: stream the low-res proxy
 *      and drive the same BeepWaveformPicker desktop uses.
 *   2. `snippet_ready` - hosted mirror only: no proxy exists on a
 *      mirror, so play the desktop-pushed audio snippet instead, on the
 *      touch picker (BeepReticle: pan under a fixed line, pinch to zoom,
 *      overview strip). The snippet covers the whole beep search window,
 *      so a wrong detector pick never hides the real beep.
 *   3. neither - nothing was pushed for this video yet; point the
 *      operator at desktop and keep Confirm disabled (confirming a beep
 *      with no evidence in front of the operator is not a real review).
 */
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { api, READ_ONLY_MIRROR_MESSAGE } from "@/lib/api";
import type { BeepQueueItem, BeepSnippetPeaks } from "@/lib/api";
import { useBeepQueue, DESTRUCTIVE_RERUN_WARNING, keyOf } from "@/lib/useBeepQueue";
import { BeepWaveformPicker } from "@/components/BeepSection";
import { BeepReticle, type ReticleMarker } from "@/components/audit/BeepReticle";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Kicker } from "@/components/ui";

const NUDGE_S = 0.01; // +-10 ms fine steppers
const PLAY_AROUND_S = 1.5;
const PLAY_LEAD_S = 0.4; // "Play from line" starts this far before the pick
/** A pick within 5 ms of the detected beep is the detected beep: panning
 *  away and back must not turn a plain confirm into a destructive re-run. */
const SAME_BEEP_S = 0.005;

export function MobileBeepReview() {
  const q = useBeepQueue();
  const [draft, setDraft] = useState<number | null>(null);
  const [sheet, setSheet] = useState<null | "confirm" | "redetect">(null);
  useEffect(() => setDraft(null), [q.activeKey]);

  if (!q.data) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading beep queue...
      </div>
    );
  }
  const item = q.active;
  if (!item) {
    return (
      <div className="px-5 py-10 text-center text-sm text-muted" role="status">
        All quiet - every beep is confirmed.
      </div>
    );
  }
  const position = q.pendingItems.findIndex((it) => keyOf(it) === keyOf(item));
  const effective = draft ?? item.beep_time;
  const mediaAvailable = item.proxy_ready || item.snippet_ready;

  const changed = draft != null && (item.beep_time == null || Math.abs(draft - item.beep_time) >= SAME_BEEP_S);
  const doConfirm = () => {
    if (changed) setSheet("confirm"); // picking a new time is destructive
    else void q.confirm(item);
  };

  return (
    <div className="mx-auto max-w-md px-4 pb-24 pt-4">
      <header className="mb-3 flex items-center justify-between">
        <Kicker>Beep review</Kicker>
        <span className="text-sm text-muted" aria-live="polite">
          {position >= 0 ? `${position + 1} of ${q.pendingItems.length}` : "confirmed"}
        </span>
      </header>
      <div className="rounded-lg border border-rule bg-surface p-4">
        <div className="mb-1 text-sm font-bold text-ink">
          {item.shooter_name} - stage {item.stage_number}
          {item.role === "secondary" ? " (secondary)" : ""}
        </div>
        <StatusLine item={item} />
        <MediaArea item={item} draft={draft} onPick={setDraft} setError={q.setError} />
        {effective != null ? (
          <NudgeRow value={effective} onNudge={(d) => setDraft((effective ?? 0) + d)} />
        ) : null}
        {item.trim_stale ? (
          <p className="mt-2 text-xs text-muted" role="status">
            Awaiting desktop re-process - results refresh after the next desktop sync.
          </p>
        ) : null}
        <div className="mt-4 flex flex-col gap-2">
          <button
            type="button"
            disabled={q.busy || !mediaAvailable || (item.beep_time == null && draft == null)}
            onClick={doConfirm}
            className="btn-led-fill inline-flex min-h-11 items-center justify-center rounded-md px-5 disabled:opacity-40"
          >
            {changed ? "Apply new time and confirm" : "Confirm beep"}
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={q.skip}
              className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink"
            >
              Skip
            </button>
            {/* #756: disabled (not hidden) when edit is denied - a
             *  missing Re-detect next to a live Confirm would read as a
             *  bug. Confirm/skip stay live regardless (review-class). */}
            <button
              type="button"
              disabled={q.busy || q.editDenied}
              onClick={() => setSheet("redetect")}
              title={q.editDenied ? READ_ONLY_MIRROR_MESSAGE : undefined}
              className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink disabled:opacity-40"
            >
              Re-detect
            </button>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={q.prevItem}
              aria-label="Previous item"
              className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink"
            >
              Prev
            </button>
            <button
              type="button"
              onClick={q.nextItem}
              aria-label="Next item"
              className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink"
            >
              Next
            </button>
          </div>
        </div>
        {q.error ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {q.error}
          </p>
        ) : null}
      </div>
      <MobileConfirmSheet
        open={sheet === "confirm"}
        title="Apply new beep time?"
        body={DESTRUCTIVE_RERUN_WARNING}
        confirmLabel="Apply and confirm"
        onConfirm={() => {
          setSheet(null);
          void q.confirm(item, changed ? draft! : undefined);
        }}
        onCancel={() => setSheet(null)}
      />
      <MobileConfirmSheet
        open={sheet === "redetect"}
        title="Re-detect this beep?"
        body={DESTRUCTIVE_RERUN_WARNING}
        confirmLabel="Re-detect"
        onConfirm={() => {
          setSheet(null);
          setDraft(null); // redetect keeps activeKey, so the effect below won't fire
          void q.redetect(item);
        }}
        onCancel={() => setSheet(null)}
      />
    </div>
  );
}

/** Text-only status line - never color-only, matches the status the
 *  queue reports (never a locally recomputed heuristic). */
function StatusLine({ item }: { item: BeepQueueItem }) {
  const text = (() => {
    switch (item.status) {
      case "missing":
        return "Missing beep";
      case "low_confidence":
        return `Low confidence${item.beep_confidence != null ? ` (${item.beep_confidence.toFixed(2)})` : ""}`;
      case "confirmed":
        return "Confirmed";
      case "unreviewed":
      default:
        return "Unreviewed";
    }
  })();
  return <p className="mb-3 text-xs text-muted">{text}</p>;
}

function MediaArea({
  item,
  draft,
  onPick,
  setError,
}: {
  item: BeepQueueItem;
  draft: number | null;
  onPick: (t: number | null) => void;
  setError: (msg: string | null) => void;
}) {
  if (item.proxy_ready) {
    return (
      <div className="mb-3 space-y-2">
        <video
          controls
          playsInline
          src={api.videoStreamUrl(item.slug, item.video_path, "proxy")}
          className="aspect-video w-full rounded-md border border-rule bg-black"
        />
        <BeepWaveformPicker
          slug={item.slug}
          stageNumber={item.stage_number}
          videoId={item.video_id}
          videoBeepTime={item.beep_time}
          draftSourceTime={draft}
          onPick={onPick}
          setError={setError}
        />
      </div>
    );
  }
  if (item.snippet_ready) {
    return (
      <div className="mb-3 space-y-2">
        <p className="text-xs text-muted">Video available on desktop - reviewing from the audio snippet.</p>
        <SnippetPlayer item={item} draft={draft} onPick={onPick} />
      </div>
    );
  }
  return (
    <p className="mb-3 text-sm text-muted">
      Review this beep on desktop - no media was pushed for this video.
    </p>
  );
}

function SnippetPlayer({
  item,
  draft,
  onPick,
}: {
  item: BeepQueueItem;
  draft: number | null;
  onPick: (t: number | null) => void;
}) {
  const [peaks, setPeaks] = useState<BeepSnippetPeaks | null>(null);
  const [peaksFailed, setPeaksFailed] = useState(false);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [focusSpan, setFocusSpan] = useState<{ span: number; nonce: number } | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pauseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPeaks(null);
    setPeaksFailed(false);
    api
      .getBeepSnippetPeaks(item.slug, item.stage_number, item.video_id)
      .then((p) => {
        if (!cancelled) setPeaks(p);
      })
      .catch(() => {
        if (!cancelled) setPeaksFailed(true);
      });
    return () => {
      cancelled = true;
      // A pending play-around-beep timeout from the outgoing item must not
      // pause the incoming item's audio once Prev/Next swaps `item` while
      // this component stays mounted.
      if (pauseTimeoutRef.current != null) clearTimeout(pauseTimeoutRef.current);
    };
  }, [item.slug, item.stage_number, item.video_id]);

  useEffect(
    () => () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  if (peaksFailed) {
    return (
      <p className="text-sm text-muted" role="status">
        The beep snippet did not load. Push from desktop again, or review this beep on desktop.
      </p>
    );
  }
  if (!peaks) {
    return (
      <div className="flex h-24 items-center justify-center text-xs text-muted">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading waveform...
      </div>
    );
  }

  const range = { start: peaks.snippet_start, end: peaks.snippet_start + peaks.duration };
  const value = draft ?? item.beep_time ?? item.alt_candidates[0]?.time ?? range.start + peaks.duration / 2;
  const alts = item.alt_candidates.filter((c) => item.beep_time == null || Math.abs(c.time - item.beep_time) >= 0.005);
  const markers: ReticleMarker[] = [
    ...(item.beep_time != null ? [{ time: item.beep_time, kind: "detected" as const }] : []),
    ...alts.map((c) => ({ time: c.time, kind: "candidate" as const })),
  ];
  const jumpTo = (t: number | null) => {
    onPick(t);
    setFocusSpan((f) => ({ span: 1, nonce: (f?.nonce ?? 0) + 1 }));
  };

  const trackPlayhead = () => {
    const el = audioRef.current;
    if (!el || el.paused) {
      setPlayhead(null);
      rafRef.current = null;
      return;
    }
    setPlayhead(range.start + el.currentTime);
    rafRef.current = requestAnimationFrame(trackPlayhead);
  };
  const playFromLine = () => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, value - range.start - PLAY_LEAD_S);
    void el.play()?.then(() => {
      if (rafRef.current == null) rafRef.current = requestAnimationFrame(trackPlayhead);
    });
    if (pauseTimeoutRef.current != null) clearTimeout(pauseTimeoutRef.current);
    pauseTimeoutRef.current = setTimeout(() => {
      audioRef.current?.pause();
    }, PLAY_AROUND_S * 1000);
  };

  return (
    <div className="space-y-2">
      <audio
        ref={audioRef}
        src={api.beepSnippetAudioUrl(item.slug, item.stage_number, item.video_id)}
        preload="auto"
      />
      <p className="text-xs text-muted">Drag to move the beep under the red line. Pinch to zoom.</p>
      <BeepReticle
        peaks={peaks.peaks}
        range={range}
        value={value}
        onChange={onPick}
        markers={markers}
        playhead={playhead}
        focusSpan={focusSpan}
        ariaLabel="Beep time - drag the waveform to put the beep under the line"
      />
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={playFromLine}
          className="min-h-11 rounded border border-rule px-3 text-sm text-ink"
        >
          Play from line
        </button>
        {item.beep_time != null ? (
          <button
            type="button"
            onClick={() => jumpTo(null)}
            aria-pressed={draft == null}
            className="min-h-11 rounded border border-rule px-3 text-sm text-ink aria-pressed:border-ink-2"
          >
            Detected <span className="numeral">{item.beep_time.toFixed(2)}</span>
          </button>
        ) : null}
        {alts.map((c) => (
          <button
            key={c.time}
            type="button"
            onClick={() => jumpTo(c.time)}
            aria-pressed={draft != null && Math.abs(draft - c.time) < 0.005}
            className="min-h-11 rounded border border-dashed border-rule px-3 text-sm text-ink aria-pressed:border-ink-2"
          >
            <span className="numeral">{c.time.toFixed(2)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function NudgeRow({ value, onNudge }: { value: number; onNudge: (delta: number) => void }) {
  return (
    <div className="mb-3 flex items-center justify-center gap-3">
      <button
        type="button"
        onClick={() => onNudge(-NUDGE_S)}
        className="min-h-11 min-w-11 rounded border border-rule text-sm text-ink"
      >
        -10 ms
      </button>
      <span className="min-w-[6ch] text-center font-mono text-sm text-ink">{value.toFixed(3)}s</span>
      <button
        type="button"
        onClick={() => onNudge(NUDGE_S)}
        className="min-h-11 min-w-11 rounded border border-rule text-sm text-ink"
      >
        +10 ms
      </button>
    </div>
  );
}
