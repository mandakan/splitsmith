import { describe, expect, it } from "vitest";

import type { SortCamera, SortClipView, SortView } from "./api";
import {
  assignClip,
  cameraLabel,
  clockText,
  formatSpan,
  importCount,
  reasonText,
  neighbour,
  resetClip,
  reviewOrder,
  stripFrame,
  whereNow,
  setChecked,
  skipClip,
  sortSections,
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
  offset_seconds: 0,
};
const HEAD: SortCamera = {
  key: "head|||VID_datetime",
  folder: "head",
  model: null,
  scheme: "VID_datetime",
  clip_ids: ["head/VID_1.mp4", "head/VID_2.mp4"],
  clock: "needs_anchor",
  offset_seconds: 0,
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
        run_size: 1,
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
      { key: "mathias", name: "Mathias Axell", stages: [1, 2] },
      { key: "anton", name: "Anton Johansson", stages: [1, 2] },
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
    anchors: [],
    overrides: [],
    user_checked: {},
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
    expect(d.overrides).toEqual([]);
    expect(d.checked["head/VID_1.mp4"]).toBe(true);
  });

  it("naming a clip of a trusted camera is an override and checks it", () => {
    const d = assignClip(view(), "from-martin/IMG_1.MOV", "mathias", 2);

    expect(d.overrides).toEqual([
      { clip_id: "from-martin/IMG_1.MOV", shooter: "mathias", stage: 2 },
    ]);
    expect(d.anchors).toEqual([]);
    expect(d.checked["from-martin/IMG_1.MOV"]).toBe(true);
  });

  it("skip replaces an earlier choice and unchecks; reset forgets both", () => {
    const v = view();
    v.overrides = [
      { clip_id: "from-martin/IMG_1.MOV", shooter: "mathias", stage: 2 },
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
