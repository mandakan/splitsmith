import { describe, expect, it } from "vitest";

import type { CoachVideoEntry } from "./api";
import { cameraOptions } from "./cameraSwitch";

function cam(
  label: string | undefined,
  beep: number | null = 4,
): CoachVideoEntry {
  return {
    path: `raw/${label ?? "x"}.mp4`,
    role: "secondary",
    beep_in_clip: beep,
    kind: "trim",
    label,
  };
}

describe("camera switch", () => {
  it("names each camera by its label and disables one without a beep", () => {
    expect(
      cameraOptions([cam("Insta360 GO 3S"), cam("iPhone 17 Pro", null)]),
    ).toEqual([
      { index: 0, label: "Insta360 GO 3S", disabled: false },
      { index: 1, label: "iPhone 17 Pro", disabled: true },
    ]);
  });

  it("falls back to Primary and numbers when the server sends no label", () => {
    expect(
      cameraOptions([cam(undefined), cam(undefined)]).map((o) => o.label),
    ).toEqual(["Primary", "Camera 2"]);
  });
});
