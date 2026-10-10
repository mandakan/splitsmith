/* eslint-disable no-restricted-syntax -- visual budget: the header row is still the old style (spec 2026-09-13 s5) */
/**
 * MultiCamColumn -- the Audit page's camera column. Audit (the top row's
 * left cell, the shared timeline, #1352) is its only renderer: the column
 * is `w-full` and the big player's tile is a 16:9 box as wide as the
 * column, height-capped (the video letterboxes inside it).
 *
 * Focus (#1407, epic #1405): the stage's other cameras are a PiP inset
 * over the big player (``components/video/PipView``), not stacked tiles,
 * so the big player fills the row. Each camera's sync pill travels with
 * its picture: the big camera's at the tile's top right, the inset's as
 * the inset's note (a click on it opens step 1 on that camera, never a
 * swap). A camera that cannot be lined up (no beep) never enters the
 * inset; its pill sits in the header so its sync stays one click away.
 * Click the inset, its swap button or press C to make it big (Shift+C
 * back; with three or more cameras C cycles the inset). Audio, the
 * waveform and every time stay the primary's whichever camera is big
 * (the page's business, see Audit.tsx).
 *
 * On lg the column fills Audit's bounded top row: the header is
 * `shrink-0`, so the tile is what flexes, and it never drops below
 * 200 px.
 *
 * The "Focus / Grid" segmented control opens the equal grid (the host
 * owns CamGridModal). The shared transport is the timeline band's header
 * (#1359).
 */

import type { ReactNode } from "react";

import { CamSyncPill, type CamSyncState } from "@/components/audit/CamSyncPill";
import { PipView } from "@/components/video/PipView";
import type { StageVideo } from "@/lib/api";
import { camOffset } from "@/lib/auditPip";
import type { InsetStreamKind, PipCamera } from "@/lib/pip";
import type { PipController } from "@/lib/usePip";
import { cn } from "@/lib/utils";

export type CamLayout = "focus" | "grid";

/** Drops the inset's top corners below the big camera's sync pill (8 px
 *  from the tile's top, about 18 px tall), measured at 1440 x 900. */
export const PIP_TOP_INSET_PX = 22;

export interface MultiCamColumnProps {
  /** Primary first. */
  videos: StageVideo[];
  camSyncStates: CamSyncState[];
  primaryBeepTime: number | null;
  /** A camera's sync pill: open step 1 on that camera. */
  onStartSync: (video: StageVideo) => void;
  layout: CamLayout;
  onLayoutChange: (layout: CamLayout) => void;
  /** The page's PiP state over these videos (camera id = ``video_id``). */
  pip: PipController;
  /** The big ``<video>`` element (callback ref + state), for PipView. */
  bigVideo: HTMLVideoElement | null;
  /** The stream kind of the inset camera's ``src``. */
  insetKind?: InsetStreamKind | null;
  onInsetError?: (camera: PipCamera, kind: InsetStreamKind | null) => void;
  /** The big player renders here (the page owns the <video>). */
  children: ReactNode;
  className?: string;
}

export function MultiCamColumn({
  videos,
  camSyncStates,
  primaryBeepTime,
  onStartSync,
  layout,
  onLayoutChange,
  pip,
  bigVideo,
  insetKind,
  onInsetError,
  children,
  className,
}: MultiCamColumnProps) {
  const count = videos.length;
  if (count === 0) return null;

  const pill = (video: StageVideo, overVideo = true) => {
    const index = videos.indexOf(video);
    return (
      <CamPill
        video={video}
        index={index}
        state={camSyncStates[index] ?? "no_beep"}
        primaryBeepTime={primaryBeepTime}
        onStartSync={onStartSync}
        overVideo={overVideo}
      />
    );
  };
  const big = videos.find((v) => v.video_id === pip.big?.id) ?? videos[0];
  // Cameras that cannot enter the inset (no beep to line them up by).
  const parked = videos.filter((v) => {
    const cam = pip.cameras.find((c) => c.id === v.video_id);
    return v !== videos[0] && (cam == null || cam.beepInClip == null);
  });

  return (
    <aside
      aria-label={`Cameras (${count})`}
      className={cn("flex w-full min-w-0 shrink-0 flex-col gap-2 lg:h-full lg:min-h-0", className)}
    >
      {/* Column header: kicker + any camera the inset cannot show + Focus/Grid. */}
      <div className="flex shrink-0 items-center gap-2 px-0.5">
        <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] tabular-nums text-subtle">
          Cameras · {pad2(count)}
        </span>
        <span aria-hidden className="h-px flex-1 bg-rule" />
        {parked.map((v) => (
          <span key={v.video_id} data-testid="cam-parked" className="inline-flex items-center gap-1">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-ink-2">
              Cam {videos.indexOf(v) + 1}
            </span>
            {pill(v, false)}
          </span>
        ))}
        {count >= 2 ? (
          <div className="inline-flex rounded-full border border-rule bg-surface-2 p-0.5">
            {(["focus", "grid"] as const).map((opt) => {
              const active = layout === opt;
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() => onLayoutChange(opt)}
                  className={cn(
                    "rounded-full px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em] transition-colors",
                    active
                      ? "bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_8px_var(--color-led-glow)]"
                      : "text-muted hover:text-ink",
                  )}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* The big player's tile. The <video> renders via {children} so the
          page keeps owning it; PipView lays the big camera's label and the
          inset over its rendered frame. */}
      <div
        data-testid="cam-primary-tile"
        className="relative aspect-video max-h-[max(240px,calc(100dvh-620px))] w-full overflow-hidden lg:aspect-auto lg:max-h-none lg:min-h-[200px] lg:flex-1 rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.02),0_18px_36px_-24px_rgba(0,0,0,0.7)] [&_video]:object-contain"
      >
        <div className="absolute inset-0">{children}</div>
        <PipView pip={pip} bigVideo={bigVideo} topInset={PIP_TOP_INSET_PX} insetKind={insetKind} onInsetError={onInsetError} />
        <span data-testid="cam-big-sync" className="absolute right-2 top-2 z-[6]">
          {pill(big)}
        </span>
      </div>
    </aside>
  );
}

/** A camera's sync pill. Over a picture it sits on an opaque backing: the
 *  pill's own tint is a 10 % wash that a bright frame (or the inset's
 *  bottom gradient) swallows. Audit builds the inset's note with it too. */
export function CamPill({
  video,
  index,
  state,
  primaryBeepTime,
  onStartSync,
  overVideo = true,
}: {
  video: StageVideo;
  index: number;
  state: CamSyncState;
  primaryBeepTime: number | null;
  onStartSync: (video: StageVideo) => void;
  overVideo?: boolean;
}) {
  const pill = (
    <CamSyncPill
      state={state}
      beepTime={video.beep_time}
      beepConfidence={video.beep_confidence}
      offsetSeconds={camOffset(video, index, primaryBeepTime)}
      size="xs"
      onClick={() => onStartSync(video)}
    />
  );
  return overVideo ? <span className="inline-flex rounded-full bg-bg/85">{pill}</span> : pill;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
