import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ScrubThumb, STRIP_FRAMES } from "./ScrubThumb";

function frameShown(button: HTMLElement): string | null {
  const layer = button.querySelector<HTMLElement>("span[style]");
  return layer ? layer.style.backgroundPosition : null;
}

describe("ScrubThumb", () => {
  it("scrubs the strip under the pointer and returns to the thumbnail on leave", () => {
    const onOpen = vi.fn();
    render(
      <ScrubThumb
        label="Play a.mov"
        thumbUrl="/t.jpg"
        stripUrl="/s.jpg"
        onOpen={onOpen}
      />,
    );
    const button = screen.getByRole("button", { name: "Play a.mov" });
    button.getBoundingClientRect = () =>
      ({ left: 0, width: 160, top: 0, height: 90 }) as DOMRect;

    fireEvent.pointerMove(button, { clientX: 159 });
    expect(frameShown(button)).toBe("100% 0px");
    fireEvent.pointerMove(button, { clientX: 0 });
    expect(frameShown(button)).toBe("0% 0px");

    fireEvent.pointerLeave(button);
    expect(frameShown(button)).toBeNull();
    expect(button.querySelector("img")).not.toBeNull();
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("steps frames with the arrow keys", () => {
    render(
      <ScrubThumb
        label="Play a.mov"
        thumbUrl={null}
        stripUrl="/s.jpg"
        onOpen={() => {}}
      />,
    );
    const button = screen.getByRole("button", { name: "Play a.mov" });

    fireEvent.keyDown(button, { key: "ArrowRight" });
    expect(frameShown(button)).toBe("0% 0px");
    fireEvent.keyDown(button, { key: "ArrowLeft" });
    expect(frameShown(button)).toBe("0% 0px");
    for (let i = 0; i < STRIP_FRAMES + 2; i++)
      fireEvent.keyDown(button, { key: "ArrowRight" });
    expect(frameShown(button)).toBe("100% 0px");
  });

  it("waits with scrubbing until the strip exists", () => {
    render(
      <ScrubThumb
        label="Play a.mov"
        thumbUrl="/t.jpg"
        stripUrl={null}
        onOpen={() => {}}
      />,
    );
    const button = screen.getByRole("button", { name: "Play a.mov" });
    button.getBoundingClientRect = () =>
      ({ left: 0, width: 160, top: 0, height: 90 }) as DOMRect;

    fireEvent.pointerMove(button, { clientX: 80 });

    expect(frameShown(button)).toBeNull();
  });
});
