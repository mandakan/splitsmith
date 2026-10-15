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
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { api, READ_ONLY_MIRROR_MESSAGE } from "@/lib/api";
import type { BeepQueueItem, BeepSnippetPeaks } from "@/lib/api";
import { recheckOrder } from "@/lib/beepQueue";
import { stageTone, toneEndNear, toneLength } from "@/lib/beepTone";
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

/** What applying a moved secondary beep does: its trim is cut again on the
 *  new beep. A secondary has no shot timeline, so nothing is re-detected. */
export const RECHECK_APPLY_WARNING =
  "Applying re-cuts this camera's clip on the new beep. The primary's shots stay as they are.";

const RECHECK_DONE = "done";

export interface MobileBeepReviewProps {
  /** The recheck pass over secondary beeps (``?recheck=secondaries``):
   *  pages over every secondary camera, confirmed or not
   *  (lib/beepQueue.recheckOrder) instead of the pending queue. */
  recheck?: boolean;
  /** The camera the pass starts at, as ``slug::stage::video``. */
  start?: string | null;
  /** Where the pass opens; shown when every beep is confirmed. */
  recheckHref?: string;
}

export function MobileBeepReview({ recheck = false, start = null, recheckHref }: MobileBeepReviewProps = {}) {
  const q = useBeepQueue();
  const [draft, setDraft] = useState<number | null>(null);
  const [sheet, setSheet] = useState<null | "confirm" | "redetect">(null);
  // The recheck pass keeps its own place: the queue's active item follows
  // the pending backlog, which a recheck of confirmed beeps is not.
  const recheckItems = useMemo(() => recheckOrder(q.flatItems), [q.flatItems]);
  const [recheckKey, setRecheckKey] = useState<string | null>(start);
  useEffect(() => setDraft(null), [q.activeKey, recheckKey]);
  // "Beep starts before the clip" (2026-10-15): set per card, on already
  // for a secondary whose beep is before 0.
  const [beforeClip, setBeforeClip] = useState(false);
  // A measured tone re-renders the card, which hands it to the picker.
  useToneUpdates();

  if (!q.data) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading beep queue...
      </div>
    );
  }
  const recheckAt = recheck
    ? recheckKey === RECHECK_DONE
      ? -1
      : Math.max(0, recheckItems.findIndex((it) => keyOf(it) === recheckKey))
    : -1;
  const item = recheck ? (recheckItems[recheckAt] ?? null) : q.active;
  if (!item) {
    return (
      <div className="flex flex-col items-center gap-4 px-5 py-10 text-center text-sm text-muted" role="status">
        {recheck
          ? recheckItems.length
            ? "Every secondary beep checked."
            : "No secondary cameras in this match."
          : "All quiet - every beep is confirmed."}
        {!recheck && recheckHref ? (
          <a href={recheckHref} className="min-h-11 rounded-md border border-rule px-4 py-2.5 text-ink">
            Recheck secondary beeps
          </a>
        ) : null}
      </div>
    );
  }
  const canBeforeClip = item.role === "secondary" && !item.proxy_ready && item.snippet_ready;
  const recheckNextItem = () => {
    const next = recheckItems[recheckAt + 1];
    setRecheckKey(next ? keyOf(next) : RECHECK_DONE);
  };
  const position = q.pendingItems.findIndex((it) => keyOf(it) === keyOf(item));
  const effective = draft ?? item.beep_time;
  const mediaAvailable = item.proxy_ready || item.snippet_ready;

  const changed = draft != null && (item.beep_time == null || Math.abs(draft - item.beep_time) >= SAME_BEEP_S);
  const doConfirm = () => {
    if (changed) setSheet("confirm"); // picking a new time is destructive
    else if (recheck) recheckNextItem(); // the beep stands: nothing written
    else void q.confirm(item);
  };

  return (
    <div className="mx-auto max-w-md px-4 pb-24 pt-4">
      <header className="mb-3 flex items-center justify-between">
        <Kicker>{recheck ? "Recheck" : "Beep review"}</Kicker>
        <span className="text-sm text-muted" aria-live="polite">
          {recheck
            ? `Secondary beep ${recheckAt + 1} of ${recheckItems.length}`
            : position >= 0
              ? `${position + 1} of ${q.pendingItems.length}`
              : "confirmed"}
        </span>
      </header>
      <div className="rounded-lg border border-rule bg-surface p-4">
        <div className="mb-1 text-sm font-bold text-ink">
          {item.shooter_name} - stage {item.stage_number}
          {item.role === "secondary" ? " (secondary)" : ""}
        </div>
        <StatusLine item={item} />
        {canBeforeClip ? (
          <BeforeClipSwitch
            key={keyOf(item)}
            item={item}
            items={q.flatItems}
            on={beforeClip}
            onChange={setBeforeClip}
            draft={draft}
          />
        ) : null}
        <MediaArea
          item={item}
          draft={draft}
          onPick={setDraft}
          setError={q.setError}
          tone={canBeforeClip && beforeClip ? toneFor(q.flatItems, item) : null}
        />
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
            // Looks right writes nothing, so it never waits on media.
            disabled={
              q.busy || (!(recheck && !changed) && (!mediaAvailable || (item.beep_time == null && draft == null)))
            }
            onClick={doConfirm}
            className="btn-led-fill inline-flex min-h-11 items-center justify-center rounded-md px-5 disabled:opacity-40"
          >
            {changed ? "Apply new time and confirm" : recheck ? "Looks right, next" : "Confirm beep"}
          </button>
          <div className="flex gap-2">
            {recheck ? (
              <button
                type="button"
                disabled={recheckAt <= 0}
                onClick={() => setRecheckKey(keyOf(recheckItems[recheckAt - 1]))}
                className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink disabled:opacity-40"
              >
                Previous camera
              </button>
            ) : (
              <button
                type="button"
                onClick={q.skip}
                className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink"
              >
                Skip
              </button>
            )}
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
          <div className={recheck ? "hidden" : "flex gap-2"}>
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
        body={recheck && item.role === "secondary" ? RECHECK_APPLY_WARNING : DESTRUCTIVE_RERUN_WARNING}
        confirmLabel="Apply and confirm"
        onConfirm={() => {
          setSheet(null);
          const applied = q.confirm(item, changed ? draft! : undefined);
          // The pass moves on once the new beep is written.
          if (recheck) void Promise.resolve(applied).then(recheckNextItem);
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

/** The stage's tone length, measured on its head cams' beep snippets
 *  (every shooter's; lib/beepTone.stageTone), cached per stage. */
const toneCache = new Map<string, ReturnType<typeof stageTone> | "loading">();
const toneListeners = new Set<() => void>();

function stageKey(item: BeepQueueItem): string {
  return String(item.stage_number);
}

function headCams(items: ReadonlyArray<BeepQueueItem>, item: BeepQueueItem): BeepQueueItem[] {
  return items.filter(
    (it) => it.role === "primary" && it.stage_number === item.stage_number && it.snippet_ready && it.beep_time != null,
  );
}

function measureTone(items: ReadonlyArray<BeepQueueItem>, item: BeepQueueItem): void {
  const key = stageKey(item);
  if (toneCache.has(key)) return;
  toneCache.set(key, "loading");
  void Promise.all(
    headCams(items, item).map((h) =>
      api
        .getBeepSnippetPeaks(h.slug, h.stage_number, h.video_id)
        .then((p) =>
          toneLength(p.peaks, { start: p.snippet_start, end: p.snippet_start + p.duration }, h.beep_time!),
        )
        .catch(() => null),
    ),
  ).then((lengths) => {
    toneCache.set(key, stageTone(lengths));
    toneListeners.forEach((f) => f());
  });
}

/** The measured tone in seconds for this card's stage, or null. */
function toneFor(items: ReadonlyArray<BeepQueueItem>, item: BeepQueueItem): number | null {
  const t = toneCache.get(stageKey(item));
  return t == null || t === "loading" ? (measureTone(items, item), null) : t.tone;
}

function useToneUpdates(): void {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    toneListeners.add(f);
    return () => {
      toneListeners.delete(f);
    };
  }, []);
}

/** "Beep starts before the clip": a camera that started recording during
 *  the beep has only the tone's end in its clip (2026-10-15). The line goes
 *  on that end and the beep is set the stage's tone length earlier. */
function BeforeClipSwitch({
  item,
  items,
  on,
  onChange,
  draft,
}: {
  item: BeepQueueItem;
  items: ReadonlyArray<BeepQueueItem>;
  on: boolean;
  onChange: (on: boolean) => void;
  draft: number | null;
}) {
  useEffect(() => {
    onChange(item.beep_time != null && item.beep_time < 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per card
  }, []);
  if (on) measureTone(items, item);
  const measured = toneCache.get(stageKey(item));
  const beep = draft ?? item.beep_time;
  const line = !on
    ? null
    : measured === "loading" || measured === undefined
      ? "Measuring the tone on the head cams..."
      : measured == null
        ? "No head cam's beep audio here to measure the tone from."
        : `Beep at ${beep != null ? beep.toFixed(3) : "?"} s · tone ${measured.tone.toFixed(3)} s from ${measured.count} head cam${
            measured.count === 1 ? "" : "s"
          }${measured.count > 1 ? ` (within ${Math.round(measured.spread * 1000)} ms)` : ""}`;
  return (
    <div className="mb-3 space-y-1">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Beep starts before the clip"
        onClick={() => onChange(!on)}
        className="min-h-11 w-full rounded border border-rule px-3 text-left text-sm text-ink"
      >
        Beep starts before the clip
        <span className="float-right text-muted">{on ? "on" : "off"}</span>
      </button>
      {line ? (
        <p data-testid="before-clip-line" className="text-sm text-ink">
          {line}
        </p>
      ) : null}
      {on ? <p className="text-xs text-muted">Put the line on the end of the tone.</p> : null}
    </div>
  );
}

function MediaArea({
  item,
  draft,
  onPick,
  setError,
  tone = null,
}: {
  item: BeepQueueItem;
  draft: number | null;
  onPick: (t: number | null) => void;
  setError: (msg: string | null) => void;
  /** Set when the beep starts before the clip: the line is the tone's end. */
  tone?: number | null;
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
        <SnippetPlayer item={item} draft={draft} onPick={onPick} tone={tone} />
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
  tone = null,
}: {
  item: BeepQueueItem;
  draft: number | null;
  onPick: (t: number | null) => void;
  tone?: number | null;
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

  // Before the clip: find where the tone stops near the clip's start and put
  // the line there, once per card, unless a time was already picked.
  const placedRef = useRef<string | null>(null);
  useEffect(() => {
    if (tone == null || !peaks || draft != null) return;
    const key = `${item.video_id}:${tone}`;
    if (placedRef.current === key) return;
    placedRef.current = key;
    const reach = (tone + 0.1) / 2;
    const end = toneEndNear(
      peaks.peaks,
      { start: peaks.snippet_start, end: peaks.snippet_start + peaks.duration },
      peaks.snippet_start + reach,
      reach + 0.05,
    );
    if (end != null) onPick(Math.round((end - tone) * 1000) / 1000);
  }, [tone, peaks, draft, item.video_id, onPick]);

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
  const beepValue = draft ?? item.beep_time ?? item.alt_candidates[0]?.time ?? range.start + peaks.duration / 2;
  // Before the clip the line is the tone's end; the beep is ``tone`` before it.
  const value = tone != null ? beepValue + tone : beepValue;
  const pickLine = (t: number | null) =>
    onPick(t == null || tone == null ? t : Math.round((t - tone) * 1000) / 1000);
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
        onChange={pickLine}
        snapTo={tone != null ? "end" : "start"}
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
