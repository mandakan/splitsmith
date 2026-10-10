/**
 * BeepStep -- step 1 of the Audit page (UX PR 5, spec s4.4): confirm the
 * beep before the shot editor. Mounted when the primary has no beep or
 * is unreviewed, when a secondary is unreviewed, or on Re-pick from
 * step 2. One item per video on this stage, primary first; Confirm runs
 * the queue's confirm (override + confirm-in-queue), which marks the
 * beep reviewed, submits the trim if uncached, then shot detection --
 * the same chain the Beep review page used, so the shot editor never
 * opens on an untrimmed clip. The parent shows the chain's progress.
 *
 * It is also the match's beep review queue (``lib/beepQueue``): a line
 * says which beep of how many this is, and Confirm & next or Later move
 * to the next one in queue order (every primary first, then the
 * secondaries), across stages and shooters. After the last, "done".
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { useConfirm } from "@/components/useConfirm";
import type { BeepQueueItem } from "@/lib/api";
import {
  nextInQueue,
  queueLine,
  queueOrder,
  queuePlace,
} from "@/lib/beepQueue";
import { isTypingTextTarget } from "@/lib/audit-input";
import {
  DESTRUCTIVE_RERUN_WARNING,
  keyOf,
  useBeepQueue,
} from "@/lib/useBeepQueue";
import { cn } from "@/lib/utils";

import { BeepPreview } from "./BeepPreview";
import { BeepTimeline } from "./BeepTimeline";

export interface BeepStepProps {
  slug: string;
  stageNumber: number;
  /** Which video to open on; defaults to the first item needing review. */
  focusVideoId?: string | null;
  /** Re-pick from step 2: the stage is already trimmed; Cancel is offered. */
  repick?: boolean;
  onCancel?: () => void;
  /** Where to go after Confirm & next or Later: the next beep in queue
   *  order (with its camera), "done" when the queue is empty, or null to
   *  stay on this stage (Skip stage, a re-pick). */
  onConfirmed: (
    next:
      { slug: string; stageNumber: number; videoId?: string } | "done" | null,
  ) => void;
  /** The page header, rendered here with this step's actions. */
  header: { ordinal: string; title: string; sub: ReactNode };
  mediaOnDesktop: boolean;
}

const MOD =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
    ? "⌘"
    : "⌃";

function cameraLabel(item: BeepQueueItem): string {
  return item.role === "secondary" ? "Secondary cam" : "Head cam";
}

export function BeepStep({
  slug,
  stageNumber,
  focusVideoId,
  repick,
  onCancel,
  onConfirmed,
  header,
  mediaOnDesktop,
}: BeepStepProps) {
  const queue = useBeepQueue();
  const confirmDialog = useConfirm();
  const items = useMemo(
    () =>
      queue.flatItems
        .filter((it) => it.slug === slug && it.stage_number === stageNumber)
        .sort((a, b) =>
          a.role === b.role ? 0 : a.role === "primary" ? -1 : 1,
        ),
    [queue.flatItems, slug, stageNumber],
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  useEffect(() => {
    if (activeId && items.some((it) => it.video_id === activeId)) return;
    const focus = focusVideoId
      ? items.find((it) => it.video_id === focusVideoId)
      : null;
    const pick =
      focus ??
      items.find((it) => it.status !== "confirmed") ??
      items[0] ??
      null;
    setActiveId(pick?.video_id ?? null);
  }, [items, activeId, focusVideoId]);
  const item = items.find((it) => it.video_id === activeId) ?? null;

  // The operator's pick: a candidate row or a click on the waveform.
  // Null means "confirm the detector's time as-is".
  const [draft, setDraft] = useState<number | null>(null);
  useEffect(() => {
    setDraft(null);
  }, [item?.video_id]);
  // The preview's <video>, held in state rather than a ref: BeepPreview
  // can swap it on its own (error / Retry, a camera switch), and the band
  // has to re-attach to the new element, which only a re-render tells it.
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const selectedTime = draft ?? item?.beep_time ?? null;

  const candidates = useMemo(() => {
    if (!item) return [];
    const out: {
      time: number;
      confidence: number | null;
      detected: boolean;
    }[] = [];
    if (item.beep_time != null)
      out.push({
        time: item.beep_time,
        confidence: item.beep_confidence,
        detected: true,
      });
    for (const c of item.alt_candidates) {
      if (item.beep_time != null && Math.abs(c.time - item.beep_time) < 0.005)
        continue;
      out.push({ time: c.time, confidence: c.confidence, detected: false });
    }
    return out;
  }, [item]);

  // The band reports every pick in source seconds, including a release
  // that lands on the detected candidate's own time -- treat that one
  // the same as a click on the detected row: "no override", not a draft
  // that happens to equal the detector's own pick.
  const handleTimelinePick = useCallback(
    (t: number) => {
      setDraft(
        item?.beep_time != null && Math.abs(t - item.beep_time) < 0.005
          ? null
          : t,
      );
    },
    [item],
  );

  const order = useMemo(() => queueOrder(queue.flatItems), [queue.flatItems]);
  const place = item
    ? queuePlace(order, {
        slug: item.slug,
        stageNumber: item.stage_number,
        videoId: item.video_id,
      })
    : null;

  // On to the beep after ``from`` in ``snapshot`` (the order as it stood
  // before a confirm): this stage's next camera in place, elsewhere through
  // the parent, "done" when nothing else waits.
  const goNext = useCallback(
    (from: BeepQueueItem, snapshot: BeepQueueItem[]) => {
      const next = nextInQueue(snapshot, {
        slug: from.slug,
        stageNumber: from.stage_number,
        videoId: from.video_id,
      });
      if (!next) {
        onConfirmed("done");
        return;
      }
      if (next.slug === slug && next.stage_number === stageNumber) {
        setActiveId(next.video_id);
        return;
      }
      onConfirmed({
        slug: next.slug,
        stageNumber: next.stage_number,
        videoId: next.video_id,
      });
    },
    [onConfirmed, slug, stageNumber],
  );

  const canConfirm = item != null && selectedTime != null && !queue.busy;
  // The picker's draft is a source-time in this camera's own clip.
  const handleConfirm = useCallback(async () => {
    if (!item || selectedTime == null) return;
    // A confirmed item reopened on repick with no fresh pick: nothing to
    // write, the operator either picked a new time or cancels.
    const changed =
      draft != null &&
      (item.beep_time == null || Math.abs(draft - item.beep_time) >= 0.005);
    if (item.beep_reviewed && !changed) {
      onCancel?.();
      return;
    }
    const snapshot = order;
    await queue.confirm(item, changed ? draft! : undefined);
    // A re-picked beep returns to its stage; a queued one moves on.
    if (repick) {
      onConfirmed(null);
      return;
    }
    goNext(item, snapshot);
  }, [
    item,
    selectedTime,
    draft,
    queue,
    onCancel,
    onConfirmed,
    order,
    repick,
    goNext,
  ]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTextTarget(e.target as HTMLElement | null)) return;
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canConfirm) {
        e.preventDefault();
        void handleConfirm();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canConfirm, handleConfirm]);

  const handleRedetect = async () => {
    if (!item) return;
    const res = await confirmDialog({
      title: "Re-detect the beep?",
      body: DESTRUCTIVE_RERUN_WARNING,
      confirmLabel: "Re-detect",
      destructive: true,
    });
    if (!res.confirmed) return;
    await queue.redetect(item);
  };

  const actions = (
    <>
      <Button
        type="button"
        onClick={() => void handleRedetect()}
        disabled={!item || queue.editDenied || queue.busy}
      >
        {queue.redetecting
          ? `Re-detecting${queue.redetectPct != null ? ` ${queue.redetectPct}%` : "..."}`
          : "Re-detect"}
      </Button>
      {repick ? (
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      ) : (
        <>
          <Button type="button" onClick={() => onConfirmed(null)}>
            Skip stage
          </Button>
          <Button
            type="button"
            onClick={() => item && goNext(item, order)}
            disabled={!item || place == null || queue.busy}
            title="Leave this beep for later and go to the next"
          >
            Later
          </Button>
        </>
      )}
      <Button
        type="button"
        variant="primary"
        onClick={() => void handleConfirm()}
        disabled={!canConfirm}
      >
        Confirm &amp; next <Kbd size="sm">{MOD}&#9166;</Kbd>
      </Button>
    </>
  );

  return (
    <>
      <PageHeader {...header} actions={actions} />
      {queue.error ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {queue.error}
        </p>
      ) : null}
      {item && place && !repick ? (
        <p className="numeral mb-2 text-md text-ink" aria-live="polite">
          {queueLine(item, place)}
        </p>
      ) : null}
      <div className="mb-3 flex flex-wrap items-center gap-3 text-md text-ink-2">
        <Label>
          <span className="text-led">Step 1</span> of 2
        </Label>
        <span>
          Is this the start beep? Pick the candidate or the timeline below,
          then Confirm. Trim and shot detection run on the confirmed beep.
        </span>
      </div>
      {items.length > 1 ? (
        <div
          className="mb-3 flex flex-wrap gap-1.5"
          role="tablist"
          aria-label="Cameras"
        >
          {items.map((it) => (
            <button
              key={it.video_id}
              type="button"
              role="tab"
              aria-selected={it.video_id === activeId}
              onClick={() => setActiveId(it.video_id)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-sm",
                it.video_id === activeId
                  ? "border-ink-2 text-ink"
                  : "border-rule-strong text-ink-2",
              )}
            >
              {cameraLabel(it)}
              {it.status === "confirmed" ? " · confirmed" : ""}
            </button>
          ))}
        </div>
      ) : null}
      {item ? (
        <>
          <div className="mb-3 flex items-center gap-3">
            <Label tone="ink">{cameraLabel(item)}</Label>
            <Label>full source</Label>
          </div>
          <div
            data-testid="beep-top-row"
            className="grid gap-4 lg:h-[max(300px,calc(100dvh-560px))] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-[minmax(0,1fr)]"
          >
            <BeepPreview
              key={keyOf(item)}
              slug={item.slug}
              videoPath={item.video_path}
              proxyReady={item.proxy_ready}
              mediaOnDesktop={mediaOnDesktop}
              initialTime={selectedTime}
              onVideoElement={setVideoEl}
              caption="Frame at the selected candidate"
            />
            <div
              className="overflow-hidden rounded-[10px] border border-rule bg-surface lg:flex lg:h-full lg:min-h-0 lg:flex-col"
              role="radiogroup"
              aria-label="Beep candidates"
            >
              <div className="lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
              {candidates.map((c) => {
                const selected =
                  selectedTime != null &&
                  Math.abs(c.time - selectedTime) < 0.005;
                return (
                  <button
                    key={c.time}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setDraft(c.detected ? null : c.time)}
                    className={cn(
                      "numeral flex w-full items-center gap-3 border-b border-rule px-3 py-2 text-left text-md text-ink-2 last:border-b-0 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                      selected &&
                        "bg-surface-2 shadow-[inset_2px_0_0_var(--color-beep)]",
                    )}
                  >
                    <i
                      aria-hidden
                      className={cn(
                        "size-3 rounded-full border-[1.5px]",
                        selected ? "border-beep bg-beep" : "border-rule-strong",
                      )}
                    />
                    <span className="w-14 text-ink">{c.time.toFixed(2)}</span>
                    <span className="w-12 text-muted">
                      {c.confidence != null ? c.confidence.toFixed(2) : "—"}
                    </span>
                    <span className="font-sans text-sm text-muted">
                      {c.detected ? "detected" : "candidate"}
                    </span>
                  </button>
                );
              })}
              {draft != null &&
              !candidates.some((c) => Math.abs(c.time - draft) < 0.005) ? (
                <div className="numeral flex items-center gap-3 border-b border-rule bg-surface-2 px-3 py-2 text-md text-ink-2 shadow-[inset_2px_0_0_var(--color-beep)] last:border-b-0">
                  <i
                    aria-hidden
                    className="size-3 rounded-full border-[1.5px] border-beep bg-beep"
                  />
                  <span className="w-14 text-ink">{draft.toFixed(2)}</span>
                  <span className="w-12 text-muted">&mdash;</span>
                  <span className="font-sans text-sm text-muted">
                    picked on the timeline
                  </span>
                </div>
              ) : null}
              <div className="px-3 py-2 text-sm text-muted">
                Or pick the beep on the timeline below
              </div>
              </div>
            </div>
          </div>
          <div className="mt-4">
            <BeepTimeline
              slug={item.slug}
              stageNumber={item.stage_number}
              videoId={item.video_id}
              videoBeepTime={item.beep_time}
              draftSourceTime={draft}
              candidates={candidates}
              media={videoEl}
              mediaOnDesktop={mediaOnDesktop}
              onPick={handleTimelinePick}
            />
          </div>
        </>
      ) : queue.data ? (
        <p className="text-md text-muted">
          Nothing on this stage needs a beep confirmed.
        </p>
      ) : (
        <p className="text-md text-muted">Loading beep queue...</p>
      )}
    </>
  );
}
