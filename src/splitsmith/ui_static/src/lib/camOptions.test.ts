import { describe, expect, it } from "vitest";

import type { StageEntry } from "./api";
import {
  cameraChoices,
  camExportFields,
  camsSummary,
  DEFAULT_CAM_OPTIONS,
  fromPipLayout,
  syncedSecondaryCount,
} from "./camOptions";

function stage(
  n: number,
  videos: Array<[StageEntry["videos"][number]["role"], number | null]>,
) {
  return {
    stage_number: n,
    videos: videos.map(([role, beep_time], i) => ({
      role,
      beep_time,
      video_id: `v${n}-${i}`,
    })),
  } as unknown as Pick<StageEntry, "stage_number" | "videos">;
}

describe("syncedSecondaryCount", () => {
  const stages = [
    stage(1, [
      ["primary", 5],
      ["secondary", 4.2],
      ["secondary", null],
    ]),
    stage(2, [
      ["primary", 5],
      ["secondary", 3.9],
    ]),
    stage(3, [
      ["primary", 5],
      ["ignored", 1],
    ]),
  ];

  it("counts secondaries with a beep, never the unsynced or the ignored", () => {
    expect(syncedSecondaryCount(stages)).toBe(2);
  });

  it("narrows to the stages an export selects", () => {
    expect(syncedSecondaryCount(stages, [2, 3])).toBe(1);
    expect(syncedSecondaryCount(stages, [3])).toBe(0);
  });

  it("is zero for a single-camera project", () => {
    expect(syncedSecondaryCount([stage(1, [["primary", 5]])])).toBe(0);
  });
});

describe("camExportFields", () => {
  it("mirrors the server's defaults", () => {
    expect(camExportFields(DEFAULT_CAM_OPTIONS)).toEqual({
      include_secondaries: true,
      pip_layout: "stacked",
      main_camera: "default",
      inset_camera: null,
      inset_corner: "bottom-right",
      inset_size: "medium",
    });
  });

  it("never sends the rotating corners again; an inset stands for them", () => {
    const fields = camExportFields({
      ...DEFAULT_CAM_OPTIONS,
      pipLayout: "pip-corners",
      insetCamera: "head",
    });
    expect(fields.pip_layout).toBe("stacked");
    expect(fields.inset_camera).toBe("head");
  });
});

describe("old presets", () => {
  it("read rotating corners as one inset, bottom-left", () => {
    expect(fromPipLayout(DEFAULT_CAM_OPTIONS, "pip-corners")).toMatchObject({
      insetCamera: "secondary",
      insetCorner: "bottom-left",
    });
    expect(fromPipLayout(DEFAULT_CAM_OPTIONS, "stacked")).toBe(
      DEFAULT_CAM_OPTIONS,
    );
  });
});

describe("camera choices", () => {
  const stages = [
    {
      stage_number: 1,
      videos: [
        { role: "primary", beep_time: 5, camera_mount: "head" },
        { role: "secondary", beep_time: 4, camera_mount: "hand" },
        { role: "secondary", beep_time: 4, camera_mount: null },
        { role: "secondary", beep_time: null, camera_mount: "chest" },
      ],
    },
  ] as unknown as Pick<StageEntry, "stage_number" | "videos">[];

  it("lists the primary, each synced mount and Secondary for an unmounted one", () => {
    expect(cameraChoices(stages).map((c) => c.label)).toEqual([
      "Primary",
      "Handheld",
      "Head cam",
      "Secondary",
    ]);
  });

  it("says the choice in words for the summary rail", () => {
    const choices = cameraChoices(stages);
    expect(camsSummary(DEFAULT_CAM_OPTIONS, choices, "Handheld")).toBe(
      "Handheld",
    );
    expect(
      camsSummary(
        { ...DEFAULT_CAM_OPTIONS, mainCamera: "hand", insetCamera: "head" },
        choices,
        "x",
      ),
    ).toBe("Handheld + Head cam inset");
  });
});
