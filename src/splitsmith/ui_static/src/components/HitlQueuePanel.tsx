/**
 * HITL queue panel for the Ingest page (#219).
 *
 * Polls ``GET /api/hitl-queue`` and renders the items the auto-trust
 * gate didn't clear: beeps that the auto-detector either missed or
 * produced with confidence below the project's
 * ``automation.beep_low_confidence_threshold``. Each item links the
 * user to the relevant stage's beep section so they can listen to
 * the ranked candidates and pick.
 *
 * The panel is intentionally light. The candidate cards + audio
 * playback already live in :file:`BeepSection.tsx` -- this component
 * is just the index that tells the user what's left to do.
 *
 * Empty state: when the queue is empty (every primary either auto-
 * trusted or manually confirmed), we render a calm "All beeps
 * confirmed" line so the panel disappears visually without
 * unmounting -- avoids the layout jump when one stage flips state.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ArrowRight, Volume2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  type HitlItemKind,
  type HitlQueueItem,
  type HitlQueueResponse,
} from "@/lib/api";

const KIND_LABELS: Record<HitlItemKind, string> = {
  beep_low_confidence: "Low confidence",
  beep_missing: "No beep found",
};

const KIND_VARIANTS: Record<HitlItemKind, "outline" | "destructive"> = {
  beep_low_confidence: "outline",
  beep_missing: "destructive",
};

export interface HitlQueuePanelProps {
  /** Optional: external trigger to refetch. Bumping the value forces
   *  a queue reload; useful when the Ingest page just promoted a
   *  candidate and wants the list to refresh without waiting for the
   *  poll. */
  refreshKey?: number;
  /** Called when the user clicks "Open stage" on an item. The Ingest
   *  page scrolls / navigates to the stage row. Defaults to a noop. */
  onJumpToStage?: (stageNumber: number, videoId: string) => void;
  /** Override poll interval in ms. Default 5 s; tests pass 0 to
   *  disable polling. */
  pollIntervalMs?: number;
}

const DEFAULT_POLL_INTERVAL_MS = 5_000;

export function HitlQueuePanel({
  refreshKey = 0,
  onJumpToStage,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: HitlQueuePanelProps) {
  const [queue, setQueue] = useState<HitlQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const next = await api.getHitlQueue();
      setQueue(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);

  useEffect(() => {
    if (!pollIntervalMs) return;
    const id = window.setInterval(() => {
      void reload();
    }, pollIntervalMs);
    return () => window.clearInterval(id);
  }, [reload, pollIntervalMs]);

  const items = queue?.items ?? [];
  const threshold = queue?.threshold ?? 0.6;

  return (
    <Card data-testid="hitl-queue-panel">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-base">Needs review</CardTitle>
            <CardDescription className="text-xs">
              Beeps the detector flagged as uncertain. Pick the right
              candidate or set the time on the waveform. Auto-trust
              fires at confidence &ge; {threshold.toFixed(2)}.
            </CardDescription>
          </div>
          {!loading && items.length > 0 ? (
            <Badge variant="outline" className="shrink-0 tabular-nums">
              {items.length}
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">
            Failed to load HITL queue: {error}
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            All beeps confirmed. Detect-beep results above the
            confidence threshold land here when the auto-trust gate
            doesn't fire.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {items.map((item) => (
              <HitlQueueRow
                key={`${item.stage_number}-${item.video_id}-${item.kind}`}
                item={item}
                onJumpToStage={onJumpToStage}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function HitlQueueRow({
  item,
  onJumpToStage,
}: {
  item: HitlQueueItem;
  onJumpToStage?: (stageNumber: number, videoId: string) => void;
}) {
  const Icon = item.kind === "beep_missing" ? AlertCircle : Volume2;
  return (
    <li
      className="flex items-center gap-3 rounded-md border bg-muted/20 px-2.5 py-2 text-sm"
      data-testid={`hitl-row-${item.stage_number}-${item.kind}`}
    >
      <Icon
        className={`size-4 shrink-0 ${
          item.kind === "beep_missing"
            ? "text-destructive"
            : "text-muted-foreground"
        }`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium tabular-nums">
            Stage {item.stage_number}
          </span>
          <Badge
            variant={KIND_VARIANTS[item.kind]}
            className="shrink-0 text-[10px] uppercase tracking-wide"
          >
            {KIND_LABELS[item.kind]}
          </Badge>
          {item.confidence !== null ? (
            <span
              className="text-xs tabular-nums text-muted-foreground"
              title="Calibrated detector confidence in [0, 1]"
            >
              conf {item.confidence.toFixed(2)}
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {item.suggested_action}
        </p>
      </div>
      {onJumpToStage ? (
        <Button
          size="sm"
          variant="ghost"
          className="shrink-0 gap-1"
          onClick={() => onJumpToStage(item.stage_number, item.video_id)}
        >
          Open
          <ArrowRight className="size-3.5" />
        </Button>
      ) : null}
    </li>
  );
}
