/**
 * A pinned trim that is re-cut while the page is open 404s the player's
 * next range request. The error overlay must survive the seeks that follow
 * (a seek on an errored element still fires ``seeking``), or the panel
 * shows an endless "Buffering..." spinner instead. A new ``src`` clears it.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoPanel } from "@/components/VideoPanel";
import type { StageVideo } from "@/lib/api";

const video: StageVideo = {
  path: "raw/stage1.mp4",
  video_id: "v1",
  role: "primary",
  added_at: "2026-01-01T00:00:00Z",
  match_timestamp: null,
  processed: { beep: true, shot_detect: true, trim: true },
  beep_time: 2.0,
  beep_source: "auto",
  beep_reviewed: true,
  beep_peak_amplitude: null,
  beep_duration_ms: null,
  beep_confidence: null,
  beep_candidates: [],
  beep_auto_detect_failed: false,
  beep_alignment_confidence: null,
  beep_alignment_delta_ms: null,
  notes: "",
  camera_mount: null,
  camera_make: null,
  camera_model: null,
};

const props = {
  slug: "alice",
  videos: [video],
  primaryBeepTime: 2.0,
  activeIndex: 0,
  onActiveIndexChange: vi.fn(),
  gridMode: false,
  onGridModeToggle: vi.fn(),
  onSecondaryRef: vi.fn(),
  onSecondaryBuffering: vi.fn(),
};

function primaryVideo(container: HTMLElement): HTMLVideoElement {
  const el = container.querySelector("video");
  if (!el) throw new Error("no <video>");
  return el;
}

describe("VideoPanel playback error", () => {
  it("keeps the error overlay through later seeks", () => {
    const { container } = render(<VideoPanel {...props} videoSrc="/stream?kind=trim&v=a" />);
    const el = primaryVideo(container);
    fireEvent.loadStart(el);
    fireEvent.error(el);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.seeking(el);
    fireEvent.waiting(el);
    fireEvent.stalled(el);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/buffering/i)).not.toBeInTheDocument();
  });

  it("clears the error when a new trim version remounts the player", () => {
    const { container, rerender } = render(<VideoPanel {...props} videoSrc="/stream?kind=trim&v=a" />);
    fireEvent.error(primaryVideo(container));
    expect(screen.getByRole("alert")).toBeInTheDocument();

    rerender(<VideoPanel {...props} videoSrc="/stream?kind=trim&v=b" />);
    const fresh = primaryVideo(container);
    expect(fresh.getAttribute("src")).toBe("/stream?kind=trim&v=b");
    fireEvent.loadStart(fresh);
    fireEvent.loadedData(fresh);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a playback error to the page", () => {
    const onPlaybackError = vi.fn();
    const { container } = render(
      <VideoPanel {...props} videoSrc="/stream?kind=web&v=w" onPlaybackError={onPlaybackError} />,
    );
    fireEvent.error(primaryVideo(container));
    expect(onPlaybackError).toHaveBeenCalledTimes(1);
  });
});
