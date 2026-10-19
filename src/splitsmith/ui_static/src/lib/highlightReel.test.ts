import { describe, expect, it } from "vitest";

import type { HighlightMenuItem, HighlightReadiness } from "@/lib/api";
import {
  blockerFix,
  blockingStages,
  defaultSelection,
  groupMenu,
  readinessLine,
  reelBody,
  renderBlock,
  resultLine,
  runFormatLabel,
  showReelCard,
} from "@/lib/highlightReel";

const menu: HighlightMenuItem[] = [
  { kind: "fastest_draw", label: "FASTEST DRAW", group: "default", default: true },
  { kind: "slowest_draw", label: "COFFEE BREAK", group: "lolz", default: false },
  { kind: "fastest_transition", label: "FASTEST TRANSITION", group: "stat", default: false },
  { kind: "fastest_split", label: "FASTEST SPLIT", group: "default", default: true },
];

function readiness(over: Partial<HighlightReadiness> = {}): HighlightReadiness {
  return {
    unlocked: false,
    ready: 5,
    total: 8,
    stages: [
      { stage_number: 1, state: "ready", proposals: 0 },
      { stage_number: 2, state: "proposals_left", proposals: 2 },
      { stage_number: 3, state: "not_audited", proposals: 0 },
      { stage_number: 4, state: "skipped", proposals: 0 },
    ],
    menu,
    cuts: ["normal", "short"],
    ...over,
  };
}

describe("readinessLine", () => {
  it("counts ready stages until every one is done", () => {
    expect(readinessLine(readiness())).toBe("5 of 8 stages ready");
    expect(readinessLine(readiness({ unlocked: true, ready: 8 }))).toBe("Every stage is ready");
  });
});

describe("wording", () => {
  it("names the reel's run in the history and words the finished render", () => {
    expect(runFormatLabel("highlight_reel")).toBe("Highlight reel");
    expect(runFormatLabel("mp4")).toBe("mp4");
    expect(resultLine({ filename: "a-highlights.mp4", duration_seconds: 19, left_out: [] })).toBe(
      "Wrote a-highlights.mp4 (19.0 s). Download or upload it from the export history.",
    );
    expect(resultLine({ filename: "a.mp4", duration_seconds: 19, left_out: [6] })).toContain("without stage 6.");
    expect(resultLine({ filename: "a.mp4", duration_seconds: 19, left_out: [6, 7] })).toContain(
      "without stages 6, 7.",
    );
    expect(
      resultLine({ filename: "a.mp4", duration_seconds: 19, left_out: [], camera_fallbacks: [2] }),
    ).toContain("Stage 2 shows the primary camera.");
    expect(
      resultLine({ filename: "a.mp4", duration_seconds: 19, left_out: [], camera_fallbacks: [2, 4] }),
    ).toContain("Stages 2, 4 show the primary camera.");
  });
});

describe("showReelCard", () => {
  it("waits for the first audited stage", () => {
    const fresh = readiness({
      stages: [
        { stage_number: 1, state: "not_audited", proposals: 0 },
        { stage_number: 2, state: "skipped", proposals: 0 },
      ],
    });
    expect(showReelCard(fresh)).toBe(false);
    expect(showReelCard(readiness())).toBe(true);
  });
});

describe("blockers", () => {
  it("lists only the stages that block, in stage order", () => {
    expect(blockingStages(readiness()).map((s) => s.stage_number)).toEqual([2, 3]);
  });

  it("words each fix and points at the page that fixes it", () => {
    const href = (...parts: string[]) => `/${parts.join("/")}`;
    expect(blockerFix({ stage_number: 2, state: "proposals_left", proposals: 2 }, "me", href)).toEqual({
      text: "Stage 2: 2 proposals left in Breakdown",
      to: "/breakdown/me/2",
    });
    expect(blockerFix({ stage_number: 2, state: "proposals_left", proposals: 1 }, "me", href).text).toBe(
      "Stage 2: 1 proposal left in Breakdown",
    );
    expect(blockerFix({ stage_number: 3, state: "not_audited", proposals: 0 }, "me", href)).toEqual({
      text: "Stage 3: audit its shots",
      to: "/audit/me/3",
    });
    expect(blockerFix({ stage_number: 5, state: "breakdown_not_opened", proposals: 0 }, "me", href)).toEqual({
      text: "Stage 5: open it in Breakdown",
      to: "/breakdown/me/5",
    });
    expect(blockerFix({ stage_number: 6, state: "trim_missing", proposals: 0 }, "me", href).to).toBe("/audit/me/6");
  });
});

describe("the menu", () => {
  it("groups in play order and ticks the defaults, never a lolz moment", () => {
    const groups = groupMenu(menu);
    expect(groups.default.map((m) => m.kind)).toEqual(["fastest_draw", "fastest_split"]);
    expect(groups.stat.map((m) => m.kind)).toEqual(["fastest_transition"]);
    expect(groups.lolz.map((m) => m.kind)).toEqual(["slowest_draw"]);
    expect([...defaultSelection(menu)]).toEqual(["fastest_draw", "fastest_split"]);
  });
});

describe("rendering", () => {
  it("sends the chosen moments in play order with the cut and the format", () => {
    const chosen = new Set(["fastest_split", "slowest_draw"]);
    expect(reelBody(menu, chosen, { cut: "short", format: "landscape", force: false })).toEqual({
      moments: ["slowest_draw", "fastest_split"],
      short: true,
      upright: false,
      force: false,
      youtube_upload: false,
      youtube_privacy: "unlisted",
      main_camera: "default",
    });
  });

  it("sends the main camera chosen under Advanced", () => {
    const body = reelBody(menu, new Set(["fastest_draw"]), {
      cut: "normal",
      format: "portrait",
      force: false,
      mainCamera: "head",
    });
    expect(body.main_camera).toBe("head");
  });

  it("asks for the YouTube upload only when the viewer chose a privacy", () => {
    const body = reelBody(menu, new Set(["fastest_draw"]), {
      cut: "normal",
      format: "portrait",
      force: false,
      after: "private",
    });
    expect([body.youtube_upload, body.youtube_privacy]).toEqual([true, "private"]);
  });

  it("says why it cannot render", () => {
    const r = readiness();
    expect(renderBlock(r, new Set(), false)).toBe("Choose at least one moment.");
    expect(renderBlock(r, new Set(["fastest_draw"]), false)).toBe(
      "Finish every stage first, or choose Render the ready ones.",
    );
    expect(renderBlock(r, new Set(["fastest_draw"]), true)).toBeNull();
    expect(renderBlock(readiness({ ready: 0 }), new Set(["fastest_draw"]), true)).toBe("No stage is ready yet.");
    expect(renderBlock(readiness({ unlocked: true }), new Set(["fastest_draw"]), false)).toBeNull();
  });
});
