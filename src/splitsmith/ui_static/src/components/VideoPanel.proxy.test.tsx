/**
 * #821 (c): the "proxy not ready" placeholder must not promise a proxy is
 * coming when the video will never leave the desktop install (a mirror
 * match). VideoPanel's `mediaOnDesktop` prop switches the copy; this file
 * pins both branches against a regression that reintroduces one unconditional
 * "Preview generating" message.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoPanel } from "@/components/VideoPanel";
import type { StageVideo } from "@/lib/api";

function makeVideo(overrides: Partial<StageVideo> = {}): StageVideo {
  return {
    path: "raw/stage1.mp4",
    video_id: "v1",
    role: "primary",
    added_at: "2026-01-01T00:00:00Z",
    match_timestamp: null,
    processed: { beep: true, shot_detect: false, trim: false },
    beep_time: 2.0,
    beep_source: "auto",
    beep_reviewed: false,
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
    ...overrides,
  };
}

const minimalProps = {
  slug: "alice",
  videos: [makeVideo()],
  primaryBeepTime: 2.0,
  activeIndex: 0,
  onActiveIndexChange: vi.fn(),
  videoSrc: "http://localhost/video.mp4",
  gridMode: false,
  onGridModeToggle: vi.fn(),
  onSecondaryRef: vi.fn(),
  onSecondaryBuffering: vi.fn(),
};

describe("VideoPanel proxy-not-ready placeholder (#821)", () => {
  it("says the video stays on desktop when the match is a mirror", () => {
    render(<VideoPanel {...minimalProps} proxyReady={false} mediaOnDesktop />);
    expect(screen.getByRole("status")).toHaveTextContent(/stays on the desktop install/i);
    expect(screen.queryByText(/check back shortly/i)).not.toBeInTheDocument();
  });

  it("keeps the generating copy when a proxy is actually coming", () => {
    render(<VideoPanel {...minimalProps} proxyReady={false} />);
    expect(screen.getByText(/preview generating/i)).toBeInTheDocument();
    expect(screen.queryByText(/stays on the desktop install/i)).not.toBeInTheDocument();
  });
});
