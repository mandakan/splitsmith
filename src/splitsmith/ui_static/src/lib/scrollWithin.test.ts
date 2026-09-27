import { describe, expect, it } from "vitest";

import { nearestScrollTop, scrollRowIntoContainer } from "./scrollWithin";

describe("nearestScrollTop", () => {
  it("leaves a fully visible row alone", () => {
    expect(nearestScrollTop(100, 200, 150, 20)).toBe(100);
  });

  it("aligns the top of a row above the view", () => {
    expect(nearestScrollTop(100, 200, 40, 20)).toBe(40);
  });

  it("aligns the bottom of a row below the view", () => {
    expect(nearestScrollTop(100, 200, 310, 20)).toBe(130);
  });

  it("aligns the top of a row taller than the view", () => {
    expect(nearestScrollTop(0, 50, 100, 80)).toBe(100);
  });
});

describe("scrollRowIntoContainer", () => {
  function rect(top: number, height: number): DOMRect {
    return { top, height, bottom: top + height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) };
  }

  it("moves only the container's scrollTop", () => {
    const container = document.createElement("div");
    const row = document.createElement("div");
    container.appendChild(row);
    Object.defineProperty(container, "clientHeight", { value: 200 });
    container.scrollTop = 0;
    container.getBoundingClientRect = () => rect(500, 200);
    // Row sits 300 px into the content, below the 200 px view.
    row.getBoundingClientRect = () => rect(800, 20);
    scrollRowIntoContainer(container, row);
    expect(container.scrollTop).toBe(120);
  });
});
