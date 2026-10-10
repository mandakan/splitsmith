import { fireEvent, render, screen, within } from "@testing-library/react";
import { useMemo, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StageVideo } from "@/lib/api";
import type { PipCamera } from "@/lib/pip";
import { resetPipPrefsForTests } from "@/lib/pipPrefs";
import { usePip } from "@/lib/usePip";

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

const cam = (n: number, extra: Partial<StageVideo> = {}): StageVideo =>
  ({ ...video, video_id: `v${n}`, role: "secondary", path: `raw/cam${n}.mp4`, beep_time: 6.2, ...extra }) as StageVideo;

function Harness({ videos, onStartSync }: { videos: StageVideo[]; onStartSync: (v: StageVideo) => void }) {
  const cameras = useMemo<PipCamera[]>(
    () =>
      videos.map((v, i) => ({
        id: v.video_id,
        label: `Cam ${i + 1}`,
        primary: i === 0,
        beepInClip: v.beep_time == null ? null : 5,
        src: `/${v.video_id}.mp4`,
        note: <button type="button" onClick={() => onStartSync(v)}>{`sync ${v.video_id}`}</button>,
      })),
    [videos, onStartSync],
  );
  const pip = usePip({ cameras, stageKey: "s3" });
  const [el, setEl] = useState<HTMLVideoElement | null>(null);
  return (
    <MultiCamColumn
      videos={videos}
      camSyncStates={videos.map(() => "synced")}
      primaryBeepTime={6}
      onStartSync={onStartSync}
      layout="focus"
      onLayoutChange={vi.fn()}
      pip={pip}
      bigVideo={el}
    >
      <video ref={setEl} data-big={pip.state.big} />
    </MultiCamColumn>
  );
}

function renderColumn(videos: StageVideo[] = [video], onStartSync = vi.fn()) {
  render(<Harness videos={videos} onStartSync={onStartSync} />);
  return { aside: screen.getByRole("complementary"), tile: screen.getByTestId("cam-primary-tile"), onStartSync };
}

beforeEach(() => {
  resetPipPrefsForTests();
  // PipView lays the inset over the measured frame; jsdom measures 0.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ left: 0, top: 0, right: 800, bottom: 450, width: 800, height: 450, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect,
  );
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("MultiCamColumn", () => {
  it("below lg, fills the cell with a 16:9 tile capped by the viewport height", () => {
    const { aside, tile } = renderColumn();
    expect(aside.style.width).toBe("");
    expect(aside).toHaveClass("w-full");
    expect(tile.style.height).toBe("");
    expect(tile).toHaveClass("aspect-video", "w-full", "max-h-[max(240px,calc(100dvh-620px))]");
  });

  it("on lg, fills the row's height and gives the tile what is left", () => {
    const { aside, tile } = renderColumn();
    // The column follows the bounded top row; the tile drops its aspect
    // ratio and cap and flexes into the remaining height, so the video
    // letterboxes instead of setting the row's height.
    expect(aside).toHaveClass("flex-col", "lg:h-full", "lg:min-h-0");
    expect(tile).toHaveClass("lg:aspect-auto", "lg:max-h-none", "lg:flex-1", "lg:min-h-[200px]");
  });

  it.each([2, 3])("with %i cameras the tile is the whole column under the header: no strip, thumbs or sync row", (n) => {
    const videos = [video, ...Array.from({ length: n - 1 }, (_, i) => cam(i + 2))];
    const { aside, tile } = renderColumn(videos);
    expect(tile).toHaveClass("lg:flex-1", "lg:min-h-[200px]");
    const siblings = Array.from(aside.children).filter((c) => c !== tile);
    expect(siblings).toHaveLength(1);
    expect(siblings[0]).toHaveClass("shrink-0");
    for (const id of ["cam-strip", "cam-thumb-row", "cam-sync-row", "cam-transport"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    expect(screen.queryByText(/Synced to primary beep/i)).toBeNull();
    // The other camera is the PiP inset over the big player.
    expect(within(tile).getByTestId("pip-inset")).toHaveAttribute("aria-label", "Inset camera: Cam 2");
  });

  it("has no inset with one camera, and labels the big camera with neutral chips", () => {
    const { tile } = renderColumn();
    expect(within(tile).queryByTestId("pip-inset")).toBeNull();
    const label = within(tile).getByTestId("pip-big-label");
    expect(label).toHaveTextContent(/Cam 1/);
    expect(label).toHaveTextContent(/Primary/);
    // The old red led PRIMARY label is gone.
    expect(tile.querySelector(".text-led-text")).toBeNull();
  });

  it("keeps the big camera's sync pill at the tile's top right, and it follows a swap", () => {
    const { tile, onStartSync } = renderColumn([video, cam(2)]);
    const pill = () => within(within(tile).getByTestId("cam-big-sync")).getByRole("button");
    // The primary shows its own beep time.
    expect(pill()).toHaveTextContent("6.000s");
    fireEvent.click(within(tile).getByRole("button", { name: "Swap with the big camera" }));
    expect(tile.querySelector("video[data-big]")).toHaveAttribute("data-big", "v2");
    // Now the secondary is big: its pill shows its offset against the primary.
    expect(pill()).toHaveTextContent("+0.200s");
    fireEvent.click(pill());
    expect(onStartSync).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v2" }));
  });

  it("a click on the inset's note opens that camera's sync and never swaps", () => {
    const { tile, onStartSync } = renderColumn([video, cam(2)]);
    fireEvent.click(within(tile).getByRole("button", { name: "sync v2" }));
    expect(onStartSync).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v2" }));
    expect(tile.querySelector("video[data-big]")).toHaveAttribute("data-big", "v1");
  });

  it("parks a camera the inset cannot show (no beep) in the header with its pill", () => {
    const { aside, tile, onStartSync } = renderColumn([video, cam(2), cam(3, { beep_time: null })]);
    const parked = within(aside).getByTestId("cam-parked");
    expect(parked).toHaveTextContent(/Cam 3/);
    fireEvent.click(within(parked).getByRole("button"));
    expect(onStartSync).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v3" }));
    // Cam 2 is the inset; Cam 3 never enters it.
    expect(within(tile).getByTestId("pip-inset")).toHaveAttribute("aria-label", "Inset camera: Cam 2");
  });
});
