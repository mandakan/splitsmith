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
import { showFirstFrame } from "@/lib/firstFrame";

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
function fakeMedia(over: Partial<{ readyState: number; seeking: boolean; currentTime: number }> = {}) {
  return { readyState: 1, seeking: false, currentTime: 0, ...over } as unknown as HTMLMediaElement;
}

describe("showFirstFrame", () => {
  it("seeks 1 ms in when metadata came without a frame", () => {
    const el = fakeMedia();
    showFirstFrame(el);
    expect(el.currentTime).toBe(0.001);
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
    expect(set).toBe(0.001);
  });
});
