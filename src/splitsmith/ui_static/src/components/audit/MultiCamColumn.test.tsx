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

function renderColumn(fill?: boolean) {
  render(
    <MultiCamColumn
      videos={[video]}
      activeIndex={0}
      onActiveIndexChange={vi.fn()}
      camSyncStates={["synced"]}
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
      fill={fill}
    >
      <video />
    </MultiCamColumn>,
  );
  return { aside: screen.getByRole("complementary"), tile: screen.getByTestId("cam-primary-tile") };
}

describe("MultiCamColumn fill", () => {
  it("keeps the fixed 380 px column and tile height without fill", () => {
    const { aside, tile } = renderColumn();
    expect(aside.style.width).toBe("380px");
    expect(tile.style.height).toBe("220px");
    expect(tile).not.toHaveClass("aspect-video");
  });

  it("fills the cell with a 16:9 primary tile capped by the viewport height", () => {
    const { aside, tile } = renderColumn(true);
    expect(aside.style.width).toBe("");
    expect(aside).toHaveClass("w-full");
    expect(tile.style.height).toBe("");
    expect(tile).toHaveClass("aspect-video", "w-full", "max-h-[55vh]");
  });
});
