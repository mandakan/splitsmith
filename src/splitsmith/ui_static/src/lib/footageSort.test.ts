import { describe, expect, it } from "vitest";

import type { SortCamera, SortClipView, SortView } from "./api";
import {
  assignClip,
  cameraLabel,
  clockText,
  formatSpan,
  importCount,
  importedText,
  reasonText,
  setCameraShooter,
  neighbour,
  resetClip,
  reviewOrder,
  stripFrame,
  whereNow,
  checkState,
  setChecked,
  setCheckedMany,
  skipClip,
  sortSections,
  stageLabel,
  defaultReferenceShooter,
  missingCount,
  referenceShooters,
  referenceTiles,
} from "./footageSort";

const PHONE: SortCamera = {
  key: "from-martin|Apple|iPhone 17 Pro Max|IMG",
  folder: "from-martin",
  model: "iPhone 17 Pro Max",
  scheme: "IMG",
  clip_ids: [
    "from-martin/IMG_1.MOV",
    "from-martin/IMG_2.MOV",
    "from-martin/IMG_3.MOV",
  ],
  clock: "trusted",
  offset_seconds: 0, shooter: null, shooter_source: null,
};
const HEAD: SortCamera = {
  key: "head|||VID_datetime",
  folder: "head",
  model: null,
  scheme: "VID_datetime",
  clip_ids: ["head/VID_1.mp4", "head/VID_2.mp4"],
  clock: "needs_anchor",
  offset_seconds: 0, shooter: null, shooter_source: null,
};

function clip(
  clipId: string,
  camera: SortCamera,
  over: Partial<SortClipView["proposal"]> & {
    checked?: boolean;
    imported_by?: string | null;
  } = {},
): SortClipView {
  const { checked = false, imported_by = null, ...proposal } = over;
  return {
    clip_id: clipId,
    index: 0,
    folder: camera.folder,
    filename: clipId.split("/")[1],
    start: "2026-09-26T11:00:00Z",
    duration: 40,
    model: camera.model,
    imported_by,
    unassigned_in: null,
    thumbnail: true,
    strip: false,
    checked,
    proposal: {
      clip_id: clipId,
      camera_key: camera.key,
      shooter: null,
      stage: null,
      confidence: "high",
      run_id: null,
      role: null,
      decided_by: "engine",
      reason: {
        scorecard_at: null,
        lead_seconds: 102,
        offset_seconds: 0,
        issue: null,
        rival: null,
        rival_stage: null,
        run_size: 1, order_rank: null, order_position: null, order_evidence: null, stage_from_name: false,
      },
      ...proposal,
    },
  };
}

function view(): SortView {
  return {
    scan_id: "abc",
    status: "ready",
    error: null,
    source_dir: "/shared",
    skipped_files: 3,
    shooters: [
      {
        key: "mathias",
        name: "Mathias Axell",
        stages: [1, 2],
        stages_with_footage: [1, 2],
      },
      {
        key: "anton",
        name: "Anton Johansson",
        stages: [1, 2],
        stages_with_footage: [],
      },
    ],
    stages: [
      { stage_number: 1, stage_name: "Lucky Seven" },
      { stage_number: 2, stage_name: "" },
    ],
    cameras: [PHONE, HEAD],
    clips: [
      clip("from-martin/IMG_1.MOV", PHONE, {
        shooter: "anton",
        stage: 2,
        checked: true,
      }),
      clip("from-martin/IMG_2.MOV", PHONE, {
        shooter: "mathias",
        stage: 1,
        checked: true,
      }),
      clip("from-martin/IMG_3.MOV", PHONE, {
        confidence: "skipped",
        reason: {
          ...clip("x/y", PHONE).proposal.reason,
          issue: "no_candidate",
        },
      }),
      clip("head/VID_1.mp4", HEAD, { confidence: "needs_you" }),
      clip("head/VID_2.mp4", HEAD, {
        confidence: "needs_you",
        imported_by: "mathias",
      }),
    ],
    strips_pending: 0,
    references: [
      {
        shooter: "mathias",
        stage: 1,
        source: "project",
        video_path: "raw/stage1.mp4",
        video_id: "vid1",
        clip_id: null,
        stream_kind: "trim",
        frame: true,
      },
      {
        shooter: "mathias",
        stage: 2,
        source: "project",
        video_path: "raw/stage2.mp4",
        video_id: "vid2",
        clip_id: null,
        stream_kind: "auto",
        frame: false,
      },
      {
        shooter: "anton",
        stage: 2,
        source: "sort",
        video_path: null,
        video_id: null,
        clip_id: "from-martin/IMG_1.MOV",
        stream_kind: "auto",
        frame: true,
      },
    ],
    frames_pending: 1,
    anchors: [],
    overrides: [],
    user_checked: {},
    camera_shooters: [],
  };
}

describe("sortSections", () => {
  it("puts unknown-clock cameras first, then shooters in match order, leftovers last", () => {
    const s = sortSections(view());

    expect(
      s.anchorCameras.map((g) => [g.camera.key, g.clips.map((c) => c.clip_id)]),
    ).toEqual([[HEAD.key, ["head/VID_1.mp4"]]]);
    expect(s.needsYou).toEqual([]);
    expect(
      s.byShooter.map((g) => [g.key, g.clips.map((c) => c.clip_id)]),
    ).toEqual([
      ["mathias", ["from-martin/IMG_2.MOV"]],
      ["anton", ["from-martin/IMG_1.MOV"]],
    ]);
    expect(s.skipped.map((c) => c.clip_id)).toEqual(["from-martin/IMG_3.MOV"]);
    expect(s.imported.map((c) => c.clip_id)).toEqual(["head/VID_2.mp4"]);
  });

  it("counts the checked clips for the import button", () => {
    expect(importCount(view())).toBe(2);
  });
});

describe("decisions", () => {
  it("naming a clip of an unknown-clock camera anchors the camera, once", () => {
    const v = view();
    v.anchors = [{ clip_id: "head/VID_2.mp4", shooter: "mathias", stage: 2 }];

    const d = assignClip(v, "head/VID_1.mp4", "mathias", 1);

    expect(d.anchors).toEqual([
      { clip_id: "head/VID_1.mp4", shooter: "mathias", stage: 1 },
    ]);
    // The earlier anchor clip keeps its answer as its own choice.
    expect(d.overrides).toEqual([
      { clip_id: "head/VID_2.mp4", shooter: "mathias", stage: 2, skip: false },
    ]);
    expect(d.checked["head/VID_1.mp4"]).toBe(true);
  });

  it("naming clip after clip on a camera whose clock stays unknown keeps every answer", () => {
    // No scorecard time: an anchor cannot set the clock, so each clip is
    // named by hand and none may undo the one before.
    let v = view();
    const apply = (d: ReturnType<typeof assignClip>) => {
      v = { ...v, anchors: d.anchors, overrides: d.overrides, user_checked: d.checked };
    };
    apply(assignClip(v, "head/VID_1.mp4", "anton", 1));
    apply(assignClip(v, "head/VID_2.mp4", "anton", 2));

    const named = [...v.anchors, ...v.overrides].map((x) => [x.clip_id, "stage" in x ? x.stage : null]);
    expect(named).toEqual(
      expect.arrayContaining([
        ["head/VID_1.mp4", 1],
        ["head/VID_2.mp4", 2],
      ]),
    );
    expect(v.user_checked).toEqual({ "head/VID_1.mp4": true, "head/VID_2.mp4": true });
  });

  it("naming a clip of a trusted camera is an override and checks it", () => {
    const d = assignClip(view(), "from-martin/IMG_1.MOV", "mathias", 2);

    expect(d.overrides).toEqual([
      { clip_id: "from-martin/IMG_1.MOV", shooter: "mathias", stage: 2, skip: false },
    ]);
    expect(d.anchors).toEqual([]);
    expect(d.checked["from-martin/IMG_1.MOV"]).toBe(true);
  });

  it("skip replaces an earlier choice and unchecks; reset forgets both", () => {
    const v = view();
    v.overrides = [
      { skip: false, clip_id: "from-martin/IMG_1.MOV", shooter: "mathias", stage: 2 },
    ];
    v.user_checked = { "from-martin/IMG_1.MOV": true };

    const skipped = skipClip(v, "from-martin/IMG_1.MOV");
    expect(skipped.overrides).toEqual([
      { clip_id: "from-martin/IMG_1.MOV", skip: true },
    ]);
    expect(skipped.checked["from-martin/IMG_1.MOV"]).toBe(false);

    const reset = resetClip(
      { ...v, overrides: skipped.overrides, user_checked: skipped.checked },
      "from-martin/IMG_1.MOV",
    );
    expect(reset.overrides).toEqual([]);
    expect("from-martin/IMG_1.MOV" in reset.checked).toBe(false);
  });

  it("a check mark keeps every other decision", () => {
    const v = view();
    v.overrides = [{ clip_id: "from-martin/IMG_2.MOV", skip: true }];

    const d = setChecked(v, "from-martin/IMG_1.MOV", false);

    expect(d.overrides).toEqual(v.overrides);
    expect(d.checked).toEqual({ "from-martin/IMG_1.MOV": false });
  });
});

describe("wording", () => {
  it("formats spans from seconds to days", () => {
    expect(formatSpan(42)).toBe("42 s");
    expect(formatSpan(102)).toBe("1:42");
    expect(formatSpan(-7500)).toBe("2 h 05 min");
    expect(formatSpan(6538320)).toBe("76 days");
  });

  it("says why a clip landed where it did", () => {
    const v = view();
    expect(reasonText(v, v.clips[0])).toBe(
      "Scored 1:42 after the clip started",
    );
    expect(reasonText(v, v.clips[2])).toBe("No squad score follows it");
    const tied = clip("from-martin/IMG_1.MOV", PHONE, {
      shooter: "mathias",
      stage: 1,
      confidence: "needs_you",
      reason: {
        ...v.clips[0].proposal.reason,
        issue: "ambiguous",
        rival: "anton",
        rival_stage: 1,
      },
    });
    expect(reasonText(v, tied)).toBe(
      "Mathias Axell Stage 01 and Anton Johansson Stage 01 were scored seconds apart",
    );
  });

  it("words a clip placed by shooting order, by its file name, or waiting for an order", () => {
    const v = view();
    const base = v.clips[0].proposal.reason;
    const byOrder = clip("head/IMG_0008.MOV", HEAD, {
      shooter: "anton",
      stage: 5,
      confidence: "needs_you",
      reason: { ...base, order_rank: 8, order_position: 2, order_evidence: "name_number" },
    });
    expect(reasonText(v, byOrder)).toBe(
      "8th clip from this camera by file number, and stage 05 is the 2nd stage Anton Johansson shot. A suggestion: the clock does not say.",
    );
    const byName = clip("head/anton_stage_3.MOV", HEAD, {
      shooter: "anton",
      stage: 3,
      confidence: "medium",
      reason: { ...base, stage_from_name: true },
    });
    expect(reasonText(v, byName)).toBe("Stage number in the file name");
    const owned = { ...v, cameras: v.cameras.map((c) => (c.key === HEAD.key ? { ...c, shooter: "anton" } : c)) };
    const waiting = clip("head/IMG_0009.MOV", HEAD, {
      confidence: "needs_you",
      reason: { ...base, issue: "needs_order" },
    });
    expect(reasonText(owned, waiting)).toBe("Set the order Anton Johansson shot the stages to place this clip");
  });

  it("sets and clears whose camera, keeping the other decisions", () => {
    const v = { ...view(), user_checked: { x: true } };
    const set = setCameraShooter(v, HEAD.key, "anton");
    expect(set.camera_shooters).toEqual([{ camera_key: HEAD.key, shooter: "anton" }]);
    expect(set.checked).toEqual({ x: true });
    const cleared = setCameraShooter({ ...v, camera_shooters: set.camera_shooters }, HEAD.key, null);
    expect(cleared.camera_shooters).toEqual([]);
  });

  it("names a camera's clock only when it is not trusted", () => {
    expect(clockText(PHONE)).toBeNull();
    expect(clockText(HEAD)).toBe("Clock unknown");
    expect(clockText({ ...HEAD, clock: "fitted", offset_seconds: -180 })).toBe(
      "Clock 3:00 fast",
    );
    expect(
      clockText({ ...HEAD, clock: "anchored", offset_seconds: 6538320 }),
    ).toBe("Clock 76 days slow");
  });

  it("labels a camera by device and folder", () => {
    expect(cameraLabel(PHONE)).toBe("iPhone 17 Pro Max · from-martin");
    expect(cameraLabel(HEAD)).toBe("Action cam · head");
  });
});

describe("whereNow", () => {
  it("names the shooter a clip imported unplaced moves from", () => {
    const v = view();
    const clipOf = (unassigned_in: string | null, shooter: string | null) => ({
      ...v.clips[0],
      unassigned_in,
      proposal: { ...v.clips[0].proposal, shooter },
    });
    expect(whereNow(v, clipOf(null, "anton"))).toBeNull();
    expect(whereNow(v, clipOf("mathias", "anton"))).toBe(
      "Moves from Mathias Axell",
    );
    expect(whereNow(v, clipOf("mathias", "mathias"))).toBe(
      "Unassigned under Mathias Axell",
    );
  });
});

describe("review order", () => {
  it("follows the page: unknown-clock camera, then shooters in match order, then skipped", () => {
    expect(reviewOrder(view())).toEqual([
      "head/VID_1.mp4",
      "from-martin/IMG_2.MOV",
      "from-martin/IMG_1.MOV",
      "from-martin/IMG_3.MOV",
    ]);
  });

  it("steps to the neighbour and stops at either end", () => {
    const v = view();
    expect(neighbour(v, "head/VID_1.mp4", 1)).toBe("from-martin/IMG_2.MOV");
    expect(neighbour(v, "head/VID_1.mp4", -1)).toBeNull();
    expect(neighbour(v, "from-martin/IMG_3.MOV", 1)).toBeNull();
    expect(neighbour(v, "head/VID_2.mp4", 1)).toBeNull();
  });

  it("maps a pointer position to a strip frame", () => {
    expect(stripFrame(0, 160, 10)).toBe(0);
    expect(stripFrame(79, 160, 10)).toBe(4);
    expect(stripFrame(160, 160, 10)).toBe(9);
    expect(stripFrame(-5, 160, 10)).toBe(0);
    expect(stripFrame(10, 0, 10)).toBe(0);
  });
});

describe("select all", () => {
  it("checks or unchecks a group in one decision and keeps the others", () => {
    const v = view();
    v.user_checked = { "head/VID_1.mp4": true };

    const d = setCheckedMany(
      v,
      ["from-martin/IMG_1.MOV", "from-martin/IMG_2.MOV"],
      false,
    );

    expect(d.checked).toEqual({
      "head/VID_1.mp4": true,
      "from-martin/IMG_1.MOV": false,
      "from-martin/IMG_2.MOV": false,
    });
  });

  it("reads a group as all, none or some", () => {
    const [a, b] = view().clips;
    expect(checkState([a, b])).toBe("all");
    expect(checkState([{ ...a, checked: false }, b])).toBe("some");
    expect(
      checkState([
        { ...a, checked: false },
        { ...b, checked: false },
      ]),
    ).toBe("none");
  });
});

describe("import summary", () => {
  it("names the shooters and what still needs the user", () => {
    const v = view();
    const imported = [
      { shooter: "anton" },
      { shooter: "mathias" },
      { shooter: "anton" },
    ];
    expect(importedText(v, imported)).toBe(
      "Imported 3 clips: Mathias Axell 1, Anton Johansson 2",
    );
    expect(importedText(v, imported, 8)).toBe(
      "Imported 3 clips: Mathias Axell 1, Anton Johansson 2. 8 still need you",
    );
  });
});

describe("stageLabel", () => {
  it("names the stage when the match does", () => {
    expect(stageLabel(1, view())).toBe("Stage 01 · Lucky Seven");
  });

  it("falls back to the number for a placeholder or an unknown stage", () => {
    expect(stageLabel(2, view())).toBe("Stage 02");
    expect(stageLabel(9, view())).toBe("Stage 09");
    expect(stageLabel(1)).toBe("Stage 01");
  });
});

describe("reference footage", () => {
  it("ranks the shooters with footage by how many stages they cover", () => {
    expect(referenceShooters(view()).map((s) => s.key)).toEqual([
      "mathias",
      "anton",
    ]);
    const nobody = { ...view(), references: [] };
    expect(referenceShooters(nobody)).toEqual([]);
  });

  it("defaults to the best other shooter", () => {
    expect(defaultReferenceShooter(view(), "mathias")).toBe("anton");
    expect(defaultReferenceShooter(view(), "anton")).toBe("mathias");
    expect(defaultReferenceShooter({ ...view(), references: [] }, "anton")).toBeNull();
  });

  it("builds one tile per match stage with the reference and the frame", () => {
    const tiles = referenceTiles(view(), "mathias", "anton", "head/VID_1.mp4");
    expect(tiles.map((t) => [t.stage, t.name, t.reference?.source ?? null, t.frame])).toEqual([
      [1, "Lucky Seven", "project", true],
      [2, "", "project", false],
    ]);
  });

  it("marks the stages the assigned shooter has, from the project and the sort", () => {
    // mathias has both stages in his project; anton's checked IMG_1 proposes stage 2.
    expect(referenceTiles(view(), "anton", "mathias", "head/VID_1.mp4").map((t) => t.has)).toEqual([
      true,
      true,
    ]);
    const tiles = referenceTiles(view(), "mathias", "anton", "head/VID_1.mp4");
    expect(tiles.map((t) => t.has)).toEqual([false, true]);
    expect(missingCount(tiles)).toBe(1);
  });

  it("excludes the clip being assigned", () => {
    const tiles = referenceTiles(view(), "mathias", "anton", "from-martin/IMG_1.MOV");
    expect(tiles.map((t) => t.has)).toEqual([false, false]);
    expect(missingCount(tiles)).toBe(2);
  });

  it("an unchecked or imported clip never counts", () => {
    const v = view();
    v.clips[0].checked = false;
    expect(referenceTiles(v, "mathias", "anton", "head/VID_1.mp4").map((t) => t.has)).toEqual([false, false]);
    v.clips[0].checked = true;
    v.clips[0].imported_by = "anton";
    expect(referenceTiles(v, "mathias", "anton", "head/VID_1.mp4").map((t) => t.has)).toEqual([false, false]);
  });
});
