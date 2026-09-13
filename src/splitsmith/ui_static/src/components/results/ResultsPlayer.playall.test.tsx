import { fireEvent, render } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { ResultsPlayer } from "@/components/results/ResultsPlayer";

// beep 3, last shot 10 -> display window [0, 13].
const SHOTS = [
  {
    id: "s1",
    shot_number: 1,
    ms_after_beep: 7000,
    time_from_beep: 7,
    time_absolute: 10,
    split: 7,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  },
];

function renderPlayer(extra: Partial<React.ComponentProps<typeof ResultsPlayer>> = {}) {
  const videoRef = createRef<HTMLVideoElement>();
  const utils = render(
    <ResultsPlayer
      src="blob:test"
      beepTime={3}
      shots={SHOTS}
      videoRef={videoRef}
      onTimeChange={() => {}}
      baselines={null}
      {...extra}
    />,
  );
  const video = videoRef.current!;
  // jsdom implements neither play() nor pause(); model the paused flag.
  let paused = true;
  Object.defineProperty(video, "paused", { get: () => paused, configurable: true });
  const play = vi.fn(() => {
    paused = false;
    return Promise.resolve();
  });
  const pause = vi.fn(() => {
    paused = true;
  });
  video.play = play;
  video.pause = pause;
  Object.defineProperty(video, "duration", { value: 30, configurable: true });
  // What a real element does on play(): flip paused, then dispatch "play".
  const startPlaying = () => {
    void play();
    fireEvent(video, new Event("play"));
  };
  return { video, play, pause, startPlaying, ...utils };
}

describe("ResultsPlayer play-all hooks", () => {
  it("pauses at the window end and reports it once while playing", () => {
    const onWindowEnd = vi.fn();
    const { video, pause, startPlaying } = renderPlayer({ onWindowEnd });
    fireEvent(video, new Event("loadedmetadata"));
    startPlaying();

    video.currentTime = 12.9;
    fireEvent(video, new Event("timeupdate"));
    expect(onWindowEnd).not.toHaveBeenCalled();

    video.currentTime = 13.2;
    fireEvent(video, new Event("timeupdate"));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(video.currentTime).toBeCloseTo(13, 2);
    expect(onWindowEnd).toHaveBeenCalledTimes(1);

    // A trailing timeupdate after the pause must not re-fire.
    fireEvent(video, new Event("timeupdate"));
    expect(onWindowEnd).toHaveBeenCalledTimes(1);
  });

  it("reports the clip's own end too, for trims cut inside the window", () => {
    const onWindowEnd = vi.fn();
    const { video, startPlaying } = renderPlayer({ onWindowEnd });
    fireEvent(video, new Event("loadedmetadata"));
    startPlaying();
    fireEvent(video, new Event("ended"));
    expect(onWindowEnd).toHaveBeenCalledTimes(1);
  });

  it("does not report when paused past the window end (scrubbing), nor without the callback", () => {
    const onWindowEnd = vi.fn();
    const { video, pause } = renderPlayer({ onWindowEnd });
    fireEvent(video, new Event("loadedmetadata"));
    video.currentTime = 13.5;
    fireEvent(video, new Event("timeupdate"));
    expect(onWindowEnd).not.toHaveBeenCalled();
    expect(pause).not.toHaveBeenCalled();

    const plain = renderPlayer();
    fireEvent(plain.video, new Event("loadedmetadata"));
    plain.startPlaying();
    plain.video.currentTime = 14;
    fireEvent(plain.video, new Event("timeupdate"));
    // No callback: playback runs on to the clip end as before.
    expect(plain.pause).not.toHaveBeenCalled();
  });

  it("starts playback from the window start once metadata arrives when autoplay is set", () => {
    const { video, play } = renderPlayer({ autoplay: true, beepTime: 8 });
    expect(play).not.toHaveBeenCalled();
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBeCloseTo(5, 2);
    expect(play).toHaveBeenCalledTimes(1);
    // Only once per mount - a metadata re-fire must not restart playback.
    fireEvent(video, new Event("loadedmetadata"));
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("never autoplays without the flag", () => {
    const { video, play } = renderPlayer();
    fireEvent(video, new Event("loadedmetadata"));
    expect(play).not.toHaveBeenCalled();
  });
});
