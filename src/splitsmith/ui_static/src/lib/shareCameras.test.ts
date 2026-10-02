import { describe, expect, it } from "vitest";

import type { ShooterCameraInfo } from "./api";
import { PRIMARY, allChoices, applyToAll, cameraChoices, currentChoice, mountLabel } from "./shareCameras";

const cam = (mount: string | null, role: "primary" | "secondary" = "secondary"): ShooterCameraInfo => ({
  group_key: `x|x|${mount}`,
  make: null,
  model: null,
  mount,
  role,
  video_count: 3,
  stage_numbers: [1, 2, 3],
});

describe("share dialog camera defaults", () => {
  it("offers one entry per camera, the primary's mount first; an unmounted camera adds Primary", () => {
    expect(cameraChoices([cam("hand"), cam("head", "primary")])).toEqual(["head", "hand"]);
    expect(cameraChoices([cam("head", "primary"), cam(null)])).toEqual([PRIMARY, "head"]);
    // One camera: nothing to choose.
    expect(cameraChoices([cam("head", "primary")])).toEqual(["head"]);
  });

  it("reads the saved default; nothing saved shows the primary camera's mount", () => {
    const cams = [cam("head", "primary"), cam("hand")];
    expect(currentChoice("hand", ["head", "hand"], cams)).toBe("hand");
    expect(currentChoice(null, ["head", "hand"], cams)).toBe("head");
    expect(currentChoice("chest", ["head", "hand"], cams)).toBe("head");
    expect(currentChoice(null, [PRIMARY, "head"], [cam(null, "primary"), cam("head")])).toBe(PRIMARY);
  });

  it("everyone on handheld: changes those who have one and names the rest", () => {
    const shooters = [
      { slug: "mathias", name: "Mathias", choices: [PRIMARY, "hand", "head"] },
      { slug: "martin", name: "Martin", choices: [PRIMARY, "hand", "head"] },
      { slug: "anton", name: "Anton", choices: [PRIMARY, "head"] },
    ];
    expect(allChoices(shooters)).toEqual([PRIMARY, "hand", "head"]);
    expect(applyToAll(shooters, "hand")).toEqual({
      changes: [
        { slug: "mathias", selector: "hand" },
        { slug: "martin", selector: "hand" },
      ],
      without: ["Anton"],
    });
  });

  it("words the mounts", () => {
    expect(mountLabel("hand")).toBe("Handheld");
    expect(mountLabel(PRIMARY)).toBe("Primary");
    expect(mountLabel("kneepad")).toBe("Kneepad");
  });
});
