import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { ResultsPlayer } from "@/components/results/ResultsPlayer";

function renderPlayer(extra: Partial<React.ComponentProps<typeof ResultsPlayer>> = {}) {
  const videoRef = createRef<HTMLVideoElement>();
  const utils = render(
    <ResultsPlayer
      src="blob:test"
      beepTime={3}
      shots={[]}
      videoRef={videoRef}
      onTimeChange={() => {}}
      baselines={null}
      {...extra}
    />,
  );
  return { videoRef, ...utils };
}

describe("ResultsPlayer moment support", () => {
  it("renders a labelled moment marker when momentTime is set", () => {
    renderPlayer({ momentTime: 7.32 });
    expect(screen.getByLabelText(/moment at 4\.32s/i)).toBeTruthy();
  });

  it("renders no marker and no copy button without moment props", () => {
    renderPlayer();
    expect(screen.queryByLabelText(/moment at/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /copy link at moment/i })).toBeNull();
  });

  it("seeks paused to momentTime once metadata loads, exactly once", () => {
    const { videoRef } = renderPlayer({ momentTime: 7.32 });
    const video = videoRef.current!;
    Object.defineProperty(video, "duration", { value: 20, configurable: true });
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBeCloseTo(7.32, 2);
    expect(video.paused).toBe(true);
    video.currentTime = 1;
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBe(1);
  });

  it("clamps an out-of-range momentTime to the clip", () => {
    const { videoRef } = renderPlayer({ momentTime: 999 });
    const video = videoRef.current!;
    Object.defineProperty(video, "duration", { value: 20, configurable: true });
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBe(20);
  });

  it("fires onCopyMoment from the transport-row button", () => {
    const onCopyMoment = vi.fn();
    renderPlayer({ onCopyMoment });
    fireEvent.click(screen.getByRole("button", { name: /copy link at moment/i }));
    expect(onCopyMoment).toHaveBeenCalledTimes(1);
  });
});
