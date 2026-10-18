import { describe, expect, it } from "vitest";

import type { GradeInfo, LookInfo } from "./api";
import { gradeFields, gradeLabel, gradeTiles, grades, selectedTile } from "./grades";
import { BUILTIN_LOOKS } from "./looks";

const graded: LookInfo = { ...BUILTIN_LOOKS[0], name: "freedoom", label: "Freedoom", source: "user", grade: true };
const plain: LookInfo = { ...BUILTIN_LOOKS[0], name: "club", label: "Club", source: "user" };
const presets: GradeInfo[] = [
  { id: "preset:vivid", label: "Vivid", preview: "/api/grades/preview?grade=preset:vivid" },
  { id: "preset:noir", label: "Noir", preview: "/api/grades/preview?grade=preset:noir" },
];
const user: GradeInfo[] = [{ id: "user:film-0123456789ab.cube", label: "film", preview: "/p" }];

describe("gradeTiles", () => {
  it("offers None, the Look's own grade when it has one, the presets, then yours", () => {
    expect(gradeTiles([graded, plain], "freedoom", presets, user).map((t) => t.id)).toEqual([
      "none",
      "look",
      "preset:vivid",
      "preset:noir",
      "user:film-0123456789ab.cube",
    ]);
    expect(gradeTiles([graded, plain], "freedoom", presets, user)[1].label).toBe("Freedoom's grade");
    expect(gradeTiles([graded, plain], "club", presets, []).map((t) => t.id)).toEqual([
      "none",
      "preset:vivid",
      "preset:noir",
    ]);
  });
});

describe("selectedTile", () => {
  const withLook = gradeTiles([graded], "freedoom", presets, []);
  const without = gradeTiles([plain], "club", presets, []);

  it("is the stored choice when listed, else the Look's default", () => {
    expect(selectedTile(withLook, "preset:noir")).toBe("preset:noir");
    expect(selectedTile(withLook, "look")).toBe("look");
    expect(selectedTile(without, "look")).toBe("none");
    expect(selectedTile(withLook, "user:gone-0123456789ab.cube")).toBe("look");
    expect(grades("none")).toBe(false);
    expect(gradeLabel(without, "look")).toBe("None");
  });
});

describe("gradeFields", () => {
  it("sends nothing for an untouched form or a timeline", () => {
    expect(gradeFields({ choice: "look", strength: null }, "mp4")).toEqual({});
    expect(gradeFields({ choice: "preset:noir", strength: 0.5 }, "fcpxml")).toEqual({});
    expect(gradeFields(undefined, "mp4")).toEqual({});
  });

  it("sends a chosen grade and strength, clamped; no strength for none", () => {
    expect(gradeFields({ choice: "preset:noir", strength: 1.4 }, "mp4")).toEqual({ grade: "preset:noir", grade_strength: 1 });
    expect(gradeFields({ choice: "none", strength: 0.4 }, "mp4")).toEqual({ grade: "none" });
  });

  it("sends a choice the gallery no longer lists as the default", () => {
    const tiles = gradeTiles([graded], "freedoom", presets, []);
    expect(gradeFields({ choice: "user:gone-0123456789ab.cube", strength: null }, "mp4", tiles)).toEqual({});
  });
});
