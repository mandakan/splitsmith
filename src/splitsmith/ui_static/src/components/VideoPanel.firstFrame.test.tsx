/**
 * Safari, unlike Chrome, decodes no frame of a ``preload="metadata"`` video
 * until it plays or seeks: it fires ``loadedmetadata`` and stops, so the
 * panel stayed black under "Loading video..." until Play. On metadata the
 * panel seeks 1 ms in, which makes Safari decode the first frame and fire
 * ``seeked`` / ``loadeddata``.
 */
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoPanel } from "@/components/VideoPanel";
import type { StageVideo } from "@/lib/api";
import { FIRST_FRAME_SEEK_S, keepOnAFrame, showFirstFrame } from "@/lib/firstFrame";

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

/** A media element as Safari leaves it after ``loadedmetadata``. */
function fakeMedia(
  over: Partial<{ readyState: number; seeking: boolean; currentTime: number; paused: boolean }> = {},
) {
  return { readyState: 1, seeking: false, currentTime: 0, paused: true, ...over } as unknown as HTMLMediaElement;
}

describe("showFirstFrame", () => {
  it("seeks into the first frame when metadata came without one", () => {
    const el = fakeMedia();
    showFirstFrame(el);
    expect(el.currentTime).toBe(FIRST_FRAME_SEEK_S);
  });

  it("seeks past a video track that starts after 0", () => {
    // Höstfinalen XI stage 7's head-cam rendition: video from 0.020 s; Safari
    // draws black for any seek before that.
    expect(FIRST_FRAME_SEEK_S).toBeGreaterThan(0.02);
  });

  it("leaves an element alone that has a frame, is seeking, or was placed elsewhere", () => {
    for (const el of [fakeMedia({ readyState: 2 }), fakeMedia({ seeking: true }), fakeMedia({ currentTime: 4.2 })]) {
      const before = el.currentTime;
      showFirstFrame(el);
      expect(el.currentTime).toBe(before);
    }
  });
});

describe("VideoPanel first frame", () => {
  it("asks for a frame as soon as the metadata is in", () => {
    const { container } = render(<VideoPanel {...props} videoSrc="/stream?kind=scrub&v=a" />);
    const el = container.querySelector("video")!;
    let set: number | null = null;
    Object.defineProperty(el, "readyState", { configurable: true, get: () => 1 });
    Object.defineProperty(el, "currentTime", {
      configurable: true,
      get: () => set ?? 0,
      set: (v: number) => {
        set = v;
      },
    });
    fireEvent.loadedMetadata(el);
    expect(set).toBe(FIRST_FRAME_SEEK_S);
  });
});

describe("keepOnAFrame", () => {
  it("moves a paused player off the moments before a late first frame", () => {
    const el = fakeMedia({ readyState: 4, currentTime: 0 });
    keepOnAFrame(el);
    expect(el.currentTime).toBe(FIRST_FRAME_SEEK_S);
  });

  it("leaves a playing, seeking or later player where it is", () => {
    for (const el of [
      fakeMedia({ readyState: 4, paused: false }),
      fakeMedia({ readyState: 4, seeking: true }),
      fakeMedia({ readyState: 4, currentTime: 3.5 }),
    ]) {
      const before = el.currentTime;
      keepOnAFrame(el);
      expect(el.currentTime).toBe(before);
    }
  });
});

describe("VideoPanel after a seek", () => {
  it("does not leave a paused player before the first frame (a swap back puts it at 0)", () => {
    const { container } = render(<VideoPanel {...props} videoSrc="/stream?kind=scrub&v=a" />);
    const el = container.querySelector("video")!;
    let set: number | null = null;
    Object.defineProperty(el, "paused", { configurable: true, get: () => true });
    Object.defineProperty(el, "readyState", { configurable: true, get: () => 4 });
    Object.defineProperty(el, "currentTime", {
      configurable: true,
      get: () => set ?? 0,
      set: (v: number) => {
        set = v;
      },
    });
    fireEvent.seeked(el);
    expect(set).toBe(FIRST_FRAME_SEEK_S);
  });
});
