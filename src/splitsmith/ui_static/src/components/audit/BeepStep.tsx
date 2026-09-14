/**
 * BeepStep -- step 1 of the Audit page (UX PR 5, spec s4.4): confirm the
 * beep before the shot editor. Mounted when the primary has no beep or
 * is unreviewed, when a secondary is unreviewed, or on Re-pick from
 * step 2. One item per video on this stage, primary first; Confirm runs
 * the queue's confirm (override + confirm-in-queue), which marks the
 * beep reviewed, submits the trim if uncached, then shot detection --
 * the same chain the Beep review page used, so the shot editor never
 * opens on an untrimmed clip. The parent shows the chain's progress.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { BeepWaveformPicker } from "@/components/BeepSection";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { useConfirm } from "@/components/useConfirm";
import { api, type BeepQueueItem } from "@/lib/api";
import { isTypingTextTarget } from "@/lib/audit-input";
import { DESTRUCTIVE_RERUN_WARNING, keyOf, useBeepQueue } from "@/lib/useBeepQueue";
import { cn } from "@/lib/utils";

import { BeepPreview } from "./BeepPreview";

export interface BeepStepProps {
  slug: string;
  stageNumber: number;
  /** Which video to open on; defaults to the first item needing review. */
  focusVideoId?: string | null;
  /** Re-pick from step 2: the stage is already trimmed; Cancel is offered. */
  repick?: boolean;
  onCancel?: () => void;
  /** After a successful confirm with nothing left on this stage: the next
   *  pending item anywhere in the match, or null when the queue is clean. */
  onConfirmed: (next: { slug: string; stageNumber: number } | null) => void;
  /** The page header, rendered here with this step's actions. */
  header: { ordinal: string; title: string; sub: ReactNode };
  mediaOnDesktop: boolean;
}

const MOD = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "⌃";

function cameraLabel(item: BeepQueueItem): string {
  return item.role === "secondary" ? "Secondary cam" : "Head cam";
}

export function BeepStep({ slug, stageNumber, focusVideoId, repick, onCancel, onConfirmed, header, mediaOnDesktop }: BeepStepProps) {
  const queue = useBeepQueue();
  const confirmDialog = useConfirm();
  const items = useMemo(
    () =>
      queue.flatItems
        .filter((it) => it.slug === slug && it.stage_number === stageNumber)
        .sort((a, b) => (a.role === b.role ? 0 : a.role === "primary" ? -1 : 1)),
    [queue.flatItems, slug, stageNumber],
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  useEffect(() => {
    if (activeId && items.some((it) => it.video_id === activeId)) return;
    const focus = focusVideoId ? items.find((it) => it.video_id === focusVideoId) : null;
    const pick = focus ?? items.find((it) => it.status !== "confirmed") ?? items[0] ?? null;
    setActiveId(pick?.video_id ?? null);
  }, [items, activeId, focusVideoId]);
  const item = items.find((it) => it.video_id === activeId) ?? null;

  // The operator's pick: a candidate row or a click on the waveform.
  // Null means "confirm the detector's time as-is".
  const [draft, setDraft] = useState<number | null>(null);
  useEffect(() => {
    setDraft(null);
  }, [item?.video_id]);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const selectedTime = draft ?? item?.beep_time ?? null;

  const candidates = useMemo(() => {
    if (!item) return [];
    const out: { time: number; confidence: number | null; detected: boolean }[] = [];
    if (item.beep_time != null) out.push({ time: item.beep_time, confidence: item.beep_confidence, detected: true });
    for (const c of item.alt_candidates) {
      if (item.beep_time != null && Math.abs(c.time - item.beep_time) < 0.005) continue;
      out.push({ time: c.time, confidence: c.confidence, detected: false });
    }
    return out;
  }, [item]);

  const canConfirm = item != null && selectedTime != null && !queue.busy;
  // The picker's draft is a source-time in this camera's own clip.
  const handleConfirm = useCallback(async () => {
    if (!item || selectedTime == null) return;
    const before = items.filter((it) => it.status !== "confirmed" && it.video_id !== item.video_id);
    // A confirmed item reopened on repick with no fresh pick: nothing to
    // write, the operator either picked a new time or cancels.
    const changed = draft != null && (item.beep_time == null || Math.abs(draft - item.beep_time) >= 0.005);
    if (item.beep_reviewed && !changed) {
      onCancel?.();
      return;
    }
    await queue.confirm(item, changed ? draft! : undefined);
    if (before.length > 0) {
      setActiveId(before[0].video_id);
      return;
    }
    // Where "& next" goes: the first item still pending anywhere in the
    // match, stage-major, read fresh so it cannot be this one.
    let next: { slug: string; stageNumber: number } | null = null;
    try {
      const pending = (await api.getBeepQueue(false)).stages.flatMap((g) => g.items);
      const candidate = pending.find((it) => it.status !== "confirmed" && !(it.slug === slug && it.stage_number === stageNumber));
      if (candidate) next = { slug: candidate.slug, stageNumber: candidate.stage_number };
    } catch {
      next = null;
    }
    onConfirmed(next);
  }, [item, items, selectedTime, draft, queue, onCancel, onConfirmed, slug, stageNumber]);

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
      <Button type="button" onClick={() => void handleRedetect()} disabled={!item || queue.editDenied || queue.busy}>
        {queue.redetecting ? `Re-detecting${queue.redetectPct != null ? ` ${queue.redetectPct}%` : "..."}` : "Re-detect"}
      </Button>
      {repick ? (
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      ) : (
        <Button type="button" onClick={() => onConfirmed(null)}>
          Skip stage
        </Button>
      )}
      <Button type="button" variant="primary" onClick={() => void handleConfirm()} disabled={!canConfirm}>
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
      <div className="mb-3 flex flex-wrap items-center gap-3 text-md text-ink-2">
        <Label>
          <span className="text-led">Step 1</span> of 2
        </Label>
        <span>
          Is this the start beep? Pick the candidate or click the waveform, then Confirm. Trim and shot detection run on
          the confirmed beep.
        </span>
      </div>
      {items.length > 1 ? (
        <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Cameras">
          {items.map((it) => (
            <button
              key={it.video_id}
              type="button"
              role="tab"
              aria-selected={it.video_id === activeId}
              onClick={() => setActiveId(it.video_id)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-sm",
                it.video_id === activeId ? "border-ink-2 text-ink" : "border-rule-strong text-ink-2",
              )}
            >
              {cameraLabel(it)}
              {it.status === "confirmed" ? " · confirmed" : ""}
            </button>
          ))}
        </div>
      ) : null}
      {item ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start">
          <div className="overflow-hidden rounded-[10px] border border-rule bg-surface">
            <div className="flex items-center gap-3 border-b border-rule px-3 py-2">
              <Label tone="ink">{cameraLabel(item)}</Label>
              <Label>full source</Label>
            </div>
            <div className="p-3">
              <BeepWaveformPicker
                key={keyOf(item)}
                slug={item.slug}
                stageNumber={item.stage_number}
                videoId={item.video_id}
                videoBeepTime={item.beep_time}
                draftSourceTime={draft}
                onPick={(t) => setDraft(t)}
                setError={queue.setError}
                snapEnabled={false}
                showFallbackBeepMarker={item.beep_time != null}
                instructions="Click the waveform to place the beep; the video parks on the pick."
                ariaLabel={`Beep picker for ${item.shooter_name}, stage ${item.stage_number}`}
                externalMediaRef={videoRef}
                fillHeight
              />
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <BeepPreview
              key={keyOf(item)}
              slug={item.slug}
              videoPath={item.video_path}
              proxyReady={item.proxy_ready}
              mediaOnDesktop={mediaOnDesktop}
              initialTime={selectedTime}
              videoRef={videoRef}
              caption="Frame at the selected candidate"
            />
            <div className="overflow-hidden rounded-[10px] border border-rule bg-surface" role="radiogroup" aria-label="Beep candidates">
              {candidates.map((c) => {
                const selected = selectedTime != null && Math.abs(c.time - selectedTime) < 0.005;
                return (
                  <button
                    key={c.time}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setDraft(c.detected ? null : c.time)}
                    className={cn(
                      "numeral flex w-full items-center gap-3 border-b border-rule px-3 py-2 text-left text-md text-ink-2 last:border-b-0 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                      selected && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
                    )}
                  >
                    <i aria-hidden className={cn("size-3 rounded-full border-[1.5px]", selected ? "border-led bg-led" : "border-rule-strong")} />
                    <span className="w-14 text-ink">{c.time.toFixed(2)}</span>
                    <span className="w-12 text-muted">{c.confidence != null ? c.confidence.toFixed(2) : "—"}</span>
                    <span className="font-sans text-sm text-muted">{c.detected ? "detected" : "candidate"}</span>
                  </button>
                );
              })}
              {draft != null && !candidates.some((c) => Math.abs(c.time - draft) < 0.005) ? (
                <div className="numeral flex items-center gap-3 border-b border-rule bg-surface-2 px-3 py-2 text-md text-ink-2 shadow-[inset_2px_0_0_var(--color-led)] last:border-b-0">
                  <i aria-hidden className="size-3 rounded-full border-[1.5px] border-led bg-led" />
                  <span className="w-14 text-ink">{draft.toFixed(2)}</span>
                  <span className="w-12 text-muted">&mdash;</span>
                  <span className="font-sans text-sm text-muted">picked on the waveform</span>
                </div>
              ) : null}
              <div className="px-3 py-2 text-sm text-muted">Or click the waveform to place the beep by hand</div>
            </div>
          </div>
        </div>
      ) : queue.data ? (
        <p className="text-md text-muted">Nothing on this stage needs a beep confirmed.</p>
      ) : (
        <p className="text-md text-muted">Loading beep queue...</p>
      )}
    </>
  );
}
