import { describe, expect, it } from "vitest";

import type { CoachVideoEntry } from "./api";
import {
  camsParam,
  parseCams,
  resolveCamera,
  selectorFor,
  startingCamera,
  withCams,
} from "./cameraPrefs";

function v(
  role: "primary" | "secondary",
  mount: string | null,
  beep: number | null = 4,
): CoachVideoEntry {
  return {
    path: `raw/${role}-${mount}.mp4`,
    role,
    beep_in_clip: beep,
    kind: "trim",
    mount,
  };
}

const HEAD_AND_PHONE = [v("primary", "head"), v("secondary", "hand")];
const PHONE_FIRST = [v("primary", "hand"), v("secondary", "head")];
const HEAD_ONLY = [v("primary", "head")];

describe("camera choice across stages", () => {
  it("names a camera by its mount, or by role when the mount is shared or missing", () => {
    expect(selectorFor(HEAD_AND_PHONE, 1)).toBe("hand");
    // The head cam is named by its mount even as the primary: on another
    // stage the primary can be the phone.
    expect(selectorFor(HEAD_AND_PHONE, 0)).toBe("head");
    expect(
      selectorFor([v("primary", null), v("secondary", "hand")], 0),
    ).toBeNull();
    expect(selectorFor([v("primary", null), v("secondary", null)], 1)).toBe(
      "secondary",
    );
    expect(selectorFor([v("primary", "hand"), v("secondary", "hand")], 1)).toBe(
      "secondary",
    );
  });

  it("finds the same mount on another stage, wherever it sits in the list", () => {
    expect(resolveCamera(HEAD_AND_PHONE, "hand")).toBe(1);
    expect(resolveCamera(PHONE_FIRST, "hand")).toBe(0);
    expect(resolveCamera(PHONE_FIRST, "head")).toBe(1);
  });

  it("falls back to the role, then the primary; never picks a camera without a beep", () => {
    expect(
      resolveCamera([v("primary", null), v("secondary", null)], "secondary"),
    ).toBe(1);
    expect(resolveCamera(HEAD_ONLY, "hand")).toBe(0);
    expect(
      resolveCamera(
        [v("primary", "head"), v("secondary", "hand", null)],
        "hand",
      ),
    ).toBe(0);
    expect(resolveCamera(HEAD_AND_PHONE, null)).toBe(0);
  });

  it("starts on the choice made while watching, else the saved default", () => {
    expect(startingCamera(HEAD_AND_PHONE, undefined, "hand")).toBe(1);
    expect(startingCamera(HEAD_AND_PHONE, "head", "hand")).toBe(0);
    expect(startingCamera(HEAD_AND_PHONE, undefined, null)).toBe(0);
  });

  it("rides in the URL", () => {
    expect(camsParam({ martin: "hand", anton: "hand" })).toBe(
      "anton:hand,martin:hand",
    );
    expect(parseCams("anton:hand,martin:hand,broken")).toEqual({
      anton: "hand",
      martin: "hand",
    });
    expect(camsParam({})).toBeNull();
    expect(withCams("/m/compare/3?play=all", "anton:hand")).toBe(
      "/m/compare/3?play=all&cams=anton%3Ahand",
    );
    expect(withCams("/m/compare/3", null)).toBe("/m/compare/3");
  });
});
