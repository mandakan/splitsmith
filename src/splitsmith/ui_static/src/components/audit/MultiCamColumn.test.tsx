import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { StageVideo } from "@/lib/api";

import { MultiCamColumn } from "./MultiCamColumn";

const video = {
  video_id: "v1",
  role: "primary",
  path: "raw/stage3.mp4",
  added_at: "2026-10-01T00:00:00Z",
  beep_time: 6,
  beep_source: "auto",
  beep_confidence: 0.99,
  beep_reviewed: true,
} as unknown as StageVideo;

const cam = (n: number): StageVideo =>
  ({ ...video, video_id: `v${n}`, role: "secondary", path: `raw/cam${n}.mp4`, beep_time: 6.2 }) as StageVideo;

function renderColumn(videos: StageVideo[] = [video]) {
  render(
    <MultiCamColumn
      videos={videos}
      activeIndex={0}
      onActiveIndexChange={vi.fn()}
      camSyncStates={videos.map(() => "synced")}
      primaryBeepTime={6}
      onStartSync={vi.fn()}
      onPromote={vi.fn()}
      layout="focus"
      onLayoutChange={vi.fn()}
      isPlaying={false}
      loopMode={false}
      currentTime={0}
      duration={20}
      onTogglePlay={vi.fn()}
      onToggleLoop={vi.fn()}
      onStepFrame={vi.fn()}
    >
      <video />
    </MultiCamColumn>,
  );
  return { aside: screen.getByRole("complementary"), tile: screen.getByTestId("cam-primary-tile") };
}

describe("MultiCamColumn", () => {
  it("below lg, fills the cell with a 16:9 primary tile capped by the viewport height", () => {
    const { aside, tile } = renderColumn();
    expect(aside.style.width).toBe("");
    expect(aside).toHaveClass("w-full");
    expect(tile.style.height).toBe("");
    expect(tile).toHaveClass("aspect-video", "w-full", "max-h-[max(240px,calc(100dvh-620px))]");
  });

  it("on lg, fills the row's height and gives the primary tile what is left", () => {
    const { aside, tile } = renderColumn();
    // The column follows the bounded top row; the tile drops its aspect
    // ratio and cap and flexes into the remaining height, so the video
    // letterboxes instead of setting the row's height.
    expect(aside).toHaveClass("flex-col", "lg:h-full", "lg:min-h-0");
    expect(tile).toHaveClass("lg:aspect-auto", "lg:max-h-none", "lg:flex-1", "lg:min-h-[200px]");
  });

  it.each([
    [2, "cam-strip"],
    [3, "cam-thumb-row"],
  ])("with %i cameras the tile flexes and keeps its 200 px backstop", (n, secondaryId) => {
    const videos = [video, ...Array.from({ length: n - 1 }, (_, i) => cam(i + 2))];
    const { aside, tile } = renderColumn(videos);
    expect(tile).toHaveClass("lg:flex-1", "lg:min-h-[200px]");
    expect(tile).not.toHaveClass("lg:min-h-0");
    // Every fixed-height sibling holds its size, so the tile is what gives.
    const siblings = Array.from(aside.children).filter((c) => c !== tile);
    expect(siblings).toHaveLength(4);
    for (const s of siblings) expect(s).toHaveClass("shrink-0");
    expect(screen.getByTestId(secondaryId)).toHaveClass("shrink-0");
    expect(screen.getByTestId("cam-sync-row")).toHaveClass("shrink-0");
    expect(screen.getByTestId("cam-transport")).toHaveClass("shrink-0");
  });
});
