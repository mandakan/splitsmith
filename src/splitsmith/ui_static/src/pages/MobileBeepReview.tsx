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
 *      mirror, so play the desktop-pushed audio snippet instead.
 *   3. neither - nothing was pushed for this video yet; point the
 *      operator at desktop and keep Confirm disabled (confirming a beep
 *      with no evidence in front of the operator is not a real review).
 */
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Loader2 } from "lucide-react";

import { api, READ_ONLY_MIRROR_MESSAGE } from "@/lib/api";
import type { BeepQueueItem, BeepSnippetPeaks } from "@/lib/api";
import { useBeepQueue, DESTRUCTIVE_RERUN_WARNING, keyOf } from "@/lib/useBeepQueue";
import { BeepWaveformPicker } from "@/components/BeepSection";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Kicker } from "@/components/ui";

const NUDGE_S = 0.01; // +-10 ms fine steppers
const PLAY_AROUND_S = 1.5;

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

  const doConfirm = () => {
    if (draft != null) setSheet("confirm"); // picking a new time is destructive
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
            {draft != null ? "Apply new time and confirm" : "Confirm beep"}
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
          void q.confirm(item, draft ?? undefined);
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
  onPick: (t: number) => void;
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
  onPick: (t: number) => void;
}) {
  const [peaks, setPeaks] = useState<BeepSnippetPeaks | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pauseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPeaks(null);
    void api
      .getBeepSnippetPeaks(item.slug, item.stage_number, item.video_id)
      .then((p) => {
        if (!cancelled) setPeaks(p);
      });
    return () => {
      cancelled = true;
      // A pending play-around-beep timeout from the outgoing item must not
      // pause the incoming item's audio once Prev/Next swaps `item` while
      // this component stays mounted.
      if (pauseTimeoutRef.current != null) clearTimeout(pauseTimeoutRef.current);
    };
  }, [item.slug, item.stage_number, item.video_id]);

  const playAroundBeep = () => {
    if (!peaks || !audioRef.current) return;
    const t = draft ?? item.beep_time ?? peaks.candidates[0]?.time;
    if (t == null) return;
    audioRef.current.currentTime = Math.max(0, t - peaks.snippet_start - PLAY_AROUND_S / 2);
    void audioRef.current.play();
    if (pauseTimeoutRef.current != null) clearTimeout(pauseTimeoutRef.current);
    pauseTimeoutRef.current = setTimeout(() => {
      audioRef.current?.pause();
    }, PLAY_AROUND_S * 1000);
  };

  const handleTap = (e: MouseEvent<HTMLDivElement>) => {
    if (!peaks) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    onPick(peaks.snippet_start + fraction * peaks.duration);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!peaks) return;
    const current = draft ?? item.beep_time ?? peaks.snippet_start;
    const clamp = (t: number) => Math.min(peaks.snippet_start + peaks.duration, Math.max(peaks.snippet_start, t));
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      onPick(clamp(current - NUDGE_S));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onPick(clamp(current + NUDGE_S));
    } else if (e.key === "Home") {
      e.preventDefault();
      onPick(peaks.snippet_start);
    } else if (e.key === "End") {
      e.preventDefault();
      onPick(peaks.snippet_start + peaks.duration);
    }
  };

  return (
    <div>
      <audio
        ref={audioRef}
        src={api.beepSnippetAudioUrl(item.slug, item.stage_number, item.video_id)}
        preload="metadata"
      />
      <div className="mb-2 flex items-center gap-2">
        <button
          type="button"
          onClick={playAroundBeep}
          disabled={!peaks}
          className="min-h-11 rounded border border-rule px-3 text-sm text-ink disabled:opacity-40"
        >
          Play around beep
        </button>
      </div>
      {peaks ? (
        <div
          role="slider"
          tabIndex={0}
          aria-label="Beep snippet waveform - tap to set the beep time"
          aria-valuemin={peaks.snippet_start}
          aria-valuemax={peaks.snippet_start + peaks.duration}
          aria-valuenow={draft ?? item.beep_time ?? peaks.snippet_start}
          onClick={handleTap}
          onKeyDown={handleKeyDown}
          className="relative h-24 w-full cursor-pointer overflow-hidden rounded border border-rule bg-bg"
        >
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
            {peaks.peaks.map((v, i) => {
              const barW = 100 / peaks.peaks.length;
              const h = Math.max(2, v * 100);
              return (
                <rect
                  key={i}
                  x={i * barW}
                  y={100 - h}
                  width={Math.max(0.5, barW - 0.5)}
                  height={h}
                  fill="var(--color-waveform-bar)"
                />
              );
            })}
          </svg>
          {item.beep_time != null ? (
            <Marker
              time={item.beep_time}
              snippetStart={peaks.snippet_start}
              duration={peaks.duration}
              label={`Detected ${item.beep_time.toFixed(3)}s`}
              color="var(--color-waveform-beep)"
              dashed
            />
          ) : null}
          {draft != null ? (
            <Marker
              time={draft}
              snippetStart={peaks.snippet_start}
              duration={peaks.duration}
              label={`Draft ${draft.toFixed(3)}s`}
              color="var(--color-waveform-playhead)"
              dashed={false}
            />
          ) : null}
        </div>
      ) : (
        <div className="flex h-24 items-center justify-center text-xs text-muted">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading waveform...
        </div>
      )}
      {item.alt_candidates.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {item.alt_candidates.map((c) => (
            <button
              key={c.time}
              type="button"
              onClick={() => onPick(c.time)}
              className="min-h-11 rounded border border-rule px-3 text-sm text-ink"
            >
              Use {c.time.toFixed(2)}s
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Marker({
  time,
  snippetStart,
  duration,
  label,
  color,
  dashed,
}: {
  time: number;
  snippetStart: number;
  duration: number;
  label: string;
  color: string;
  dashed: boolean;
}) {
  const pct = Math.min(100, Math.max(0, ((time - snippetStart) / duration) * 100));
  return (
    <div
      className="pointer-events-none absolute inset-y-0 border-l-2"
      style={{ left: `${pct}%`, borderColor: color, borderStyle: dashed ? "dashed" : "solid" }}
    >
      <span className="absolute -top-0.5 left-1 whitespace-nowrap text-[0.625rem] text-ink">
        {label}
      </span>
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
