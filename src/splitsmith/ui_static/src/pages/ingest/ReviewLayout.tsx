import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Package, Plus, Video, X } from "lucide-react";
import { Link } from "react-router-dom";

import { CameraCard } from "@/components/ingest/CameraCard";
import { IngestMoveBanner } from "@/components/ingest/IngestMoveBanner";
import {
  StageReferenceDrawer,
  useStageDrawerCollapsed,
} from "@/components/ingest/StageReferenceDrawer";
import { Button } from "@/components/ui/button";
import type {
  MatchProject,
  MoveShooterBlocked,
  ShooterListEntry,
  VideoRole,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";
import { ClipDetail } from "@/pages/ingest/ClipDetail";
import { ClipList } from "@/pages/ingest/ClipList";
import {
  buildClipModel,
  firstUnassignedPath,
  nextUnassignedAfter,
  selectDelta,
} from "@/pages/ingest/model";

export function ReviewLayout({
  slug,
  project,
  shooters,
  lastImportedPaths,
  moveBlocked,
  onDismissBanner,
  onMoveShooter,
  onAddMore,
  onMoveAssignment,
  onRemoveVideo,
  onConfirm,
  onSaved,
  busy,
  lastScannedDir,
  onError,
  beepPending,
}: {
  slug: string;
  project: MatchProject;
  shooters: ShooterListEntry[];
  lastImportedPaths: string[] | null;
  moveBlocked: MoveShooterBlocked[];
  onDismissBanner: () => void;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onAddMore: () => void;
  // Applies the move optimistically and resolves true immediately so selection
  // can auto-advance without waiting on the network. A rare server failure
  // resyncs the project in the background (Ingest.moveAssignment), after which
  // the selection effect re-resolves against the corrected model.
  onMoveAssignment: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<boolean>;
  onRemoveVideo: (videoPath: string) => Promise<void>;
  onConfirm: () => void;
  onSaved: (project?: MatchProject) => Promise<void>;
  busy: boolean;
  lastScannedDir: string | null;
  onError: (msg: string | null) => void;
  beepPending: number;
}) {
  const href = useMatchHref();
  const model = useMemo(() => buildClipModel(project), [project]);

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  // Default selection follows the work queue: first unassigned clip, else the
  // first clip overall. Re-resolves if the current selection disappears (moved
  // to another shooter, removed) after a reload.
  useEffect(() => {
    const stillExists =
      selectedPath != null &&
      model.order.some((c) => c.video.path === selectedPath);
    if (!stillExists) {
      setSelectedPath(firstUnassignedPath(model) ?? model.order[0]?.video.path ?? null);
    }
  }, [model, selectedPath]);

  const selectedClip =
    model.order.find((c) => c.video.path === selectedPath) ?? null;

  // Up/Down (and j/k) move the selection through the flat clip order. Skip
  // when focus is in a form control so typing / native <select> keys still
  // work. preventDefault stops the page from scrolling under the list.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t instanceof HTMLElement) {
        if (t.isContentEditable) return;
        if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") {
          return;
        }
      }
      let delta = 0;
      if (e.key === "ArrowDown" || e.key === "j") delta = 1;
      else if (e.key === "ArrowUp" || e.key === "k") delta = -1;
      else return;
      e.preventDefault();
      setSelectedPath((cur) => selectDelta(model.order, cur, delta));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [model.order]);

  // Match the prior StageReference default: collapse once every stage has
  // footage (not merely when the unassigned queue is empty). Only the first
  // visit is affected -- a persisted toggle wins after that.
  const allStagesHaveFootage =
    project.stages.length > 0 &&
    project.stages.every((s) => (s.videos?.length ?? 0) > 0);
  const [drawerCollapsed, toggleDrawer] = useStageDrawerCollapsed(
    allStagesHaveFootage,
  );

  const activeShooterName = shooters.find((s) => s.slug === slug)?.name ?? slug;
  const showBanner =
    lastImportedPaths != null &&
    lastImportedPaths.length > 0 &&
    shooters.length > 1;

  // After assigning a queued clip to a stage, jump to the next unassigned one
  // so the operator keeps clearing the pile without the mouse. Compute the
  // target from the CURRENT model (before the move); its path is unchanged by
  // the reassignment, so selecting it by path survives the project reload.
  // Only advance when the move actually succeeded -- otherwise the clip stays
  // in the queue and selection should stay on it.
  const handleMove = useCallback(
    async (videoPath: string, toStage: number | null, role: VideoRole) => {
      const wasUnassigned =
        model.order.find((c) => c.video.path === videoPath)?.stageNumber == null;
      const nextPath =
        wasUnassigned && toStage != null
          ? nextUnassignedAfter(model, videoPath)
          : null;
      const ok = await onMoveAssignment(videoPath, toStage, role);
      if (ok && nextPath != null) setSelectedPath(nextPath);
    },
    [model, onMoveAssignment],
  );

  const assignStage = useCallback(
    (stageNumber: number) => {
      if (selectedClip)
        void handleMove(selectedClip.video.path, stageNumber, selectedClip.video.role);
    },
    [selectedClip, handleMove],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: drop summary + add more */}
      <div className="relative flex items-center gap-4 overflow-hidden rounded-xl border border-rule-strong bg-gradient-to-r from-led/10 to-transparent px-5 py-3.5">
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_12px_var(--color-led-glow)]"
        />
        <span className="inline-flex size-10 items-center justify-center rounded-[10px] bg-led-fill text-ink shadow-[0_0_16px_var(--color-led-glow)]">
          <Package className="size-5" strokeWidth={2.2} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-display text-[0.9375rem] font-bold uppercase tracking-[0.04em] text-ink tabular-nums">
            <b className="text-led">{model.totalVideos}</b>{" "}
            {model.totalVideos === 1 ? "video" : "videos"} detected &middot;{" "}
            <b className="text-led">{model.cameras.length}</b>{" "}
            {model.cameras.length === 1 ? "camera" : "cameras"} inferred
          </div>
          {lastScannedDir && (
            <div className="mt-0.5 truncate font-mono text-[0.6875rem] tracking-[0.04em] text-muted">
              {lastScannedDir}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onAddMore}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-3.5 py-2 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-ink-2 hover:bg-surface-3"
        >
          <Plus className="size-3" /> Add more
        </button>
      </div>

      {/* Beep review CTA */}
      {beepPending > 0 && (
        <Link
          to={href("beep-review")}
          className="flex items-center gap-3.5 rounded-xl border border-rule bg-gradient-to-r from-beep/10 to-transparent px-5 py-3 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-ink-2 transition-colors hover:bg-beep/15"
        >
          <span className="inline-flex size-7 items-center justify-center rounded-full border border-beep/40 bg-beep-tint text-beep shadow-[0_0_10px_var(--color-beep-glow)]">
            <Video className="size-3.5" />
          </span>
          <span className="flex-1">
            <b className="font-bold text-beep">{beepPending}</b> beep
            {beepPending === 1 ? "" : "s"} need{beepPending === 1 ? "s" : ""}{" "}
            confirmation &middot;{" "}
            <span className="text-muted">detect found candidates but isn't sure</span>
          </span>
          <span className="inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-beep">
            Review beeps <ArrowRight className="size-3" />
          </span>
        </Link>
      )}

      {/* Post-import batch move banner */}
      {showBanner && (
        <IngestMoveBanner
          shooterName={activeShooterName}
          videoPaths={lastImportedPaths}
          shooters={shooters}
          excludeSlug={slug}
          blocked={moveBlocked}
          busy={busy}
          onMove={onMoveShooter}
          onDismiss={onDismissBanner}
        />
      )}
      {moveBlocked.length > 0 && !showBanner && (
        <div className="flex items-start gap-3 rounded-xl border border-live/40 bg-live/10 px-4 py-3 text-[0.8125rem]">
          <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-live font-mono text-xs font-bold text-bg">
            !
          </span>
          <div className="flex-1 font-mono text-[0.6875rem] leading-relaxed text-ink-2">
            <b className="font-display font-bold uppercase tracking-[0.06em] text-live">
              {moveBlocked.length} stage{moveBlocked.length === 1 ? "" : "s"} not moved
            </b>{" "}
            -- the destination already had reviewed footage. Resolve manually.
          </div>
          <button
            type="button"
            onClick={() => onDismissBanner()}
            aria-label="Dismiss"
            className="rounded p-0.5 text-subtle hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      {/* Cameras */}
      {model.cameras.length > 0 && (
        <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2">
          {model.cameras.map((cam) => (
            <CameraCard key={cam.id} camera={cam} slug={slug} onSaved={onSaved} />
          ))}
        </div>
      )}

      {/* Three-region workspace */}
      <div
        className={cn(
          "grid min-h-[70vh] grid-cols-1 gap-4",
          drawerCollapsed
            ? "lg:grid-cols-[300px_minmax(0,1fr)_28px]"
            : "lg:grid-cols-[300px_minmax(0,1fr)_300px]",
        )}
      >
        <ClipList
          model={model}
          selectedPath={selectedPath}
          onSelect={setSelectedPath}
          slug={slug}
          rawVideos={project.raw_videos ?? []}
        />
        <ClipDetail
          slug={slug}
          clip={selectedClip}
          allStages={project.stages}
          shooters={shooters}
          rawVideos={project.raw_videos ?? []}
          busy={busy}
          onMove={handleMove}
          onRemove={onRemoveVideo}
          onMoveShooter={onMoveShooter}
          onError={onError}
          onReload={onSaved}
        />
        <StageReferenceDrawer
          stages={project.stages}
          collapsed={drawerCollapsed}
          onToggle={toggleDrawer}
          canAssign={selectedClip != null}
          onAssignStage={assignStage}
        />
      </div>

      {/* Footer */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rule-strong bg-surface px-6 py-4">
        <div className="font-mono text-[0.75rem] uppercase tracking-[0.06em] text-muted tabular-nums">
          <b className="font-bold text-ink">{model.totalVideos}</b> videos &middot;{" "}
          <b className="font-bold text-ink">{model.willProcess}</b> will process{" "}
          {model.ignoredCount > 0 && (
            <>
              &middot; <b className="font-bold text-ink">{model.ignoredCount}</b> ignored
            </>
          )}
        </div>
        <Button
          type="button"
          onClick={onConfirm}
          disabled={busy || model.willProcess === 0}
          className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
        >
          {/* Processing already auto-queued at scan/assign time; this
           *  button only leaves the review. Don't claim it starts work. */}
          <span className="font-display uppercase tracking-[0.08em]">
            Done - match overview
          </span>
          <ArrowRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
