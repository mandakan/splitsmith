import { describe, expect, it } from "vitest";

import { exportSlugify, slugify } from "./slugify";

// Mirrors tests/test_export_naming.py literal for literal. The backend's
// export_naming.slugify names files on disk; anything the SPA composes
// against those files must agree byte-for-byte, or a promote looks for a
// fixture under a name nobody ever wrote (the Långvägen 409).
describe("exportSlugify", () => {
  it("keeps accented characters as dashes, exactly like the backend", () => {
    expect(exportSlugify("Långvägen", "stage")).toBe("l-ngv-gen");
  });

  it("collapses symbol runs to single dashes", () => {
    expect(exportSlugify("All Symbols!@#", "stage")).toBe("all-symbols");
    expect(exportSlugify("Stage 1 -- H1", "stage")).toBe("stage-1-h1");
  });

  it("returns the caller's fallback when nothing survives", () => {
    expect(exportSlugify("!!!", "stage")).toBe("stage");
    expect(exportSlugify("", "match")).toBe("match");
  });

  it("passes ordinary names through kebab-cased", () => {
    expect(exportSlugify("Bromma 2026", "match")).toBe("bromma-2026");
  });

  it("disagrees with the accent-stripping slugify on purpose", () => {
    // slugify (match_model.slugify_filename semantics) is for project
    // folder ids; exportSlugify is for backend-written file names. They
    // must not be merged - that would rename existing files.
    expect(slugify("Långvägen")).toBe("langvagen");
    expect(exportSlugify("Långvägen", "stage")).toBe("l-ngv-gen");
  });
});
