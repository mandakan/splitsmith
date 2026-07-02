import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, MoreVertical, XCircle } from "lucide-react";

import { RoleToggles } from "@/components/ingest/RoleToggles";
import { ShooterPickerPopover } from "@/components/ingest/ShooterPickerPopover";
import { Portal } from "@/components/ui/Portal";
import {
  ApiError,
  api,
  type ShooterListEntry,
  type StageEntry,
  type VideoRole,
} from "@/lib/api";
import { useSpacePlayPause } from "@/lib/keyboard";
import type { ClipItem } from "@/pages/ingest/model";
import { pad2 } from "@/pages/ingest/model";

/**
 * ClipDetail -- the center master-detail pane. Renders the ONLY <video> on the
 * page (keyed on the clip path so switching clips loads fresh) plus the ONLY
 * stage picker, docked directly beneath the player so watch-and-assign is one
 * motion with no scrolling. Space toggles playback via the shared hook.
 */
export function ClipDetail({
  slug,
  clip,
  allStages,
  shooters,
  busy,
  onMove,
  onRemove,
  onMoveShooter,
  onError,
}: {
  slug: string;
  clip: ClipItem | null;
  allStages: StageEntry[];
  shooters: ShooterListEntry[];
  busy: boolean;
  onMove: (videoPath: string, toStage: number | null, role: VideoRole) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onError: (msg: string | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [rowBusy, setRowBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [kebabOpen, setKebabOpen] = useState(false);
  const kebabRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // Anchor for the portaled kebab menu: fixed coordinates captured when
  // the menu opens. The menu can't render inline -- the clip card is
  // overflow-hidden, which clipped the dropdown on short cards.
  const [menuPos, setMenuPos] = useState<{ top: number; right: number } | null>(
    null,
  );
  const hasOtherShooters = shooters.length > 1;

  const toggleKebab = useCallback(() => {
    setKebabOpen((o) => {
      if (!o) {
        const rect = kebabRef.current?.getBoundingClientRect();
        setMenuPos(
          rect
            ? { top: rect.bottom + 4, right: window.innerWidth - rect.right }
            : null,
        );
      }
      return !o;
    });
  }, []);

  const togglePlay = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }, []);
  useSpacePlayPause(togglePlay, clip != null);

  useEffect(() => {
    if (!kebabOpen) return;
    function onOutside(e: MouseEvent) {
      const t = e.target as Node;
      // The menu is portaled to <body>, so check both the trigger
      // wrapper and the menu itself before treating a press as outside.
      if (kebabRef.current?.contains(t) || menuRef.current?.contains(t)) {
        return;
      }
      setKebabOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setKebabOpen(false);
    }
    document.addEventListener("mousedown", onOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [kebabOpen]);

  if (!clip) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-rule-strong bg-surface/50 px-6 text-center">
        <div className="font-display text-sm font-bold uppercase tracking-[0.08em] text-muted">
          Select a clip to preview and assign
        </div>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
          Space plays / pauses &middot; Up / Down moves between clips
        </div>
      </div>
    );
  }

  const video = clip.video;
  const currentStage = clip.stageNumber;
  const filename = video.path.split("/").pop() ?? video.path;
  const cameraDetail = [clip.camera?.model ?? null, clip.camera?.mount ?? null]
    .filter(Boolean)
    .join(" \u00B7 ");
  const needsBeep =
    video.role !== "ignored" && video.beep_time == null && currentStage != null;

  async function changeStage(next: string) {
    setRowBusy(true);
    try {
      if (next === "unassigned") await onMove(video.path, null, video.role);
      else {
        const n = Number(next);
        if (!Number.isNaN(n)) await onMove(video.path, n, video.role);
      }
    } finally {
      setRowBusy(false);
    }
  }

  async function setRole(next: VideoRole) {
    setRowBusy(true);
    try {
      await onMove(video.path, currentStage, next);
    } finally {
      setRowBusy(false);
    }
  }

  async function detectBeep() {
    if (currentStage == null) return;
    setDetecting(true);
    onError(null);
    try {
      await api.detectBeepForVideo(slug, currentStage, video.video_id);
    } catch (e) {
      onError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setDetecting(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface">
      {/* Header: filename + camera */}
      <div className="flex items-center gap-3 border-b border-rule px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.8125rem] font-semibold text-ink">
            {filename}
          </div>
          {clip.camera && (
            <div className="mt-0.5 truncate font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
              {clip.camera?.label}
              {cameraDetail && <> &middot; {cameraDetail}</>}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={() => void onRemove(video.path)}
          disabled={busy}
          title="Remove video"
          aria-label="Remove video"
          className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led disabled:opacity-50"
        >
          <XCircle className="size-4" />
        </button>
        {hasOtherShooters && (
          <div ref={kebabRef} className="relative">
            <button
              type="button"
              onClick={toggleKebab}
              disabled={busy || rowBusy}
              title="More actions"
              aria-label="More actions"
              aria-expanded={kebabOpen}
              className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-surface-2 hover:text-ink-2 disabled:opacity-50"
            >
              <MoreVertical className="size-4" />
            </button>
            {kebabOpen && menuPos && (
              <Portal>
              <div
                ref={menuRef}
                role="menu"
                aria-label="Clip actions"
                style={{ top: menuPos.top, right: menuPos.right }}
                className="fixed z-drawer w-48 overflow-hidden rounded-lg border border-rule-strong bg-surface shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)]"
              >
                <div className="border-b border-rule px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
                  Move to shooter
                </div>
                <div className="p-2">
                  <ShooterPickerPopover
                    shooters={shooters}
                    excludeSlug={slug}
                    busy={busy || rowBusy}
                    onPick={async (targetSlug) => {
                      setKebabOpen(false);
                      setRowBusy(true);
                      onError(null);
                      try {
                        await onMoveShooter(targetSlug, [video.path]);
                      } finally {
                        setRowBusy(false);
                      }
                    }}
                  />
                </div>
              </div>
              </Portal>
            )}
          </div>
        )}
      </div>

      {/* Player */}
      <div className="min-h-0 flex-1 overflow-hidden bg-black">
        <video
          key={video.path}
          ref={videoRef}
          controls
          preload="metadata"
          src={api.shooterVideoStreamUrl(slug, video.path)}
          className="h-full w-full object-contain"
        />
      </div>

      {/* Assignment bar -- docked directly under the player, no scroll gap */}
      <div className="border-t border-rule bg-surface-2 px-4 py-3">
        <div className="mb-2 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-subtle">
          Streaming source &middot; scrub to identify the stage
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <select
              value={currentStage === null ? "unassigned" : String(currentStage)}
              onChange={(e) => void changeStage(e.target.value)}
              disabled={busy}
              className="min-h-9 rounded-md border border-rule bg-surface-3 px-3 py-1.5 pr-8 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)]"
            >
              <option value="unassigned">-- Unassigned --</option>
              {allStages.map((s) => (
                <option key={s.stage_number} value={s.stage_number}>
                  Stage {pad2(s.stage_number)} -- {s.stage_name}
                </option>
              ))}
            </select>
            {rowBusy && (
              <Loader2
                aria-label="Saving assignment"
                className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-led"
              />
            )}
          </div>

          {currentStage === null ? (
            <span
              className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted"
              title="A video needs a stage before it can have a role. The first video assigned to a stage becomes its primary automatically."
            >
              pick a stage &rarr; auto-primary
            </span>
          ) : (
            <RoleToggles
              value={video.role}
              onChange={(r) => void setRole(r)}
              disabled={busy || rowBusy}
            />
          )}

          <div className="ml-auto flex items-center gap-2">
            {video.beep_time != null ? (
              <span className="inline-flex items-center gap-1.5 rounded border border-beep/40 bg-beep-tint px-2 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums text-beep">
                beep {video.beep_time.toFixed(2)}s
              </span>
            ) : video.role === "ignored" ? (
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                ignored
              </span>
            ) : null}
            {needsBeep && (
              <button
                type="button"
                onClick={() => void detectBeep()}
                disabled={busy || detecting}
                title="Detect beep on this video"
                className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 transition-colors hover:border-led-deep hover:bg-led-tint hover:text-led disabled:opacity-50"
              >
                {detecting ? "Queuing..." : "Detect beep"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
