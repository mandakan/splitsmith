/**
 * A camera swap (PiP, #1408) changes the player's source in place: the
 * same <video> element, the position carried over by the beep, the play
 * state kept, and neither the window-start seek nor the moment seek
 * firing again for the new source.
 */
import { fireEvent, render } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ResultsPlayer } from "@/components/results/ResultsPlayer";

type Props = React.ComponentProps<typeof ResultsPlayer>;

function setup(extra: Partial<Props> = {}) {
  const videoRef = createRef<HTMLVideoElement>();
  const onTimeChange = vi.fn();
  const onVideoElement = vi.fn();
  const base: Props = {
    src: "blob:cam1",
    beepTime: 5,
    shots: [],
    videoRef,
    onTimeChange,
    onVideoElement,
    baselines: null,
    ...extra,
  };
  const utils = render(<ResultsPlayer {...base} />);
  const swapTo = (src: string, beepTime: number, more: Partial<Props> = {}) =>
    utils.rerender(<ResultsPlayer {...base} src={src} beepTime={beepTime} {...more} />);
  return { videoRef, onTimeChange, onVideoElement, swapTo, ...utils };
}

function loadMetadata(video: HTMLVideoElement, duration = 60) {
  Object.defineProperty(video, "duration", { value: duration, configurable: true });
  fireEvent(video, new Event("loadedmetadata"));
}

describe("ResultsPlayer camera swap", () => {
  it("keeps the element and carries the position from the beep (paused)", () => {
    const { videoRef, onVideoElement, swapTo } = setup();
    const video = videoRef.current!;
    loadMetadata(video);
    video.currentTime = 8; // 3 s after cam 1's beep
    fireEvent.timeUpdate(video);
    const play = vi.spyOn(video, "play");

    swapTo("blob:cam2", 12);
    expect(videoRef.current).toBe(video);
    expect(onVideoElement).toHaveBeenCalledTimes(1);
    expect(video.getAttribute("src")).toBe("blob:cam2");

    loadMetadata(video);
    expect(video.currentTime).toBeCloseTo(15, 6); // 3 s after cam 2's beep
    expect(play).not.toHaveBeenCalled();
  });

  it("resumes playback on the new source when it was playing", () => {
    const { videoRef, swapTo } = setup();
    const video = videoRef.current!;
    loadMetadata(video);
    video.currentTime = 9;
    fireEvent.timeUpdate(video);
    fireEvent.play(video);
    const play = vi.spyOn(video, "play").mockResolvedValue(undefined);

    swapTo("blob:cam2", 2);
    loadMetadata(video);
    expect(video.currentTime).toBeCloseTo(6, 6);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("holds the readout on the carried time while the new source loads", () => {
    const { videoRef, onTimeChange, swapTo } = setup();
    const video = videoRef.current!;
    loadMetadata(video);
    video.currentTime = 8;
    fireEvent.timeUpdate(video);
    onTimeChange.mockClear();

    swapTo("blob:cam2", 12);
    expect(onTimeChange).toHaveBeenLastCalledWith(15);
    // The new source's own zero is not the viewer's position.
    video.currentTime = 0;
    fireEvent.timeUpdate(video);
    expect(onTimeChange).toHaveBeenLastCalledWith(15);
  });

  it("does not seek to the moment again on a swap", () => {
    const { videoRef, swapTo } = setup({ momentTime: 7 }); // 2 s after the beep
    const video = videoRef.current!;
    loadMetadata(video);
    expect(video.currentTime).toBeCloseTo(7, 6);
    video.currentTime = 10; // the viewer moved on: 5 s after the beep
    fireEvent.timeUpdate(video);

    swapTo("blob:cam2", 12, { momentTime: 14 }); // the same moment on cam 2
    loadMetadata(video);
    expect(video.currentTime).toBeCloseTo(17, 6);
  });

  it("clamps a carried position to the new clip", () => {
    const { videoRef, swapTo } = setup();
    const video = videoRef.current!;
    loadMetadata(video);
    video.currentTime = 50;
    fireEvent.timeUpdate(video);

    swapTo("blob:cam2", 20);
    loadMetadata(video, 30);
    expect(video.currentTime).toBe(30);
  });

  it("renders the overlay inside the video box", () => {
    const { container } = setup({ overlay: <div data-testid="overlay" /> });
    const overlay = container.querySelector("[data-testid=overlay]")!;
    expect(overlay.parentElement?.querySelector("video")).not.toBeNull();
  });
});
