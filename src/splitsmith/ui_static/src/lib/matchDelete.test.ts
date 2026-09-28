import { describe, expect, it } from "vitest";

import { matchDeleteCopy } from "./matchDelete";

const base = { name: "ESs Black Handgun 2026", kind: "match" as const, origin: "local" as const };

describe("matchDeleteCopy", () => {
  it("desktop: says a synced match keeps its hosted copy", () => {
    const copy = matchDeleteCopy({ ...base, synced: true }, false);
    expect(copy.body.join(" ")).toMatch(/copy on splitsmith\.app is not deleted/);
    expect(copy.checkboxes.map((c) => c.key)).toEqual(["deleteLocalFiles"]);
  });

  it("desktop: says a never-synced match has no hosted copy", () => {
    const copy = matchDeleteCopy({ ...base, synced: false }, false);
    expect(copy.body.join(" ")).toMatch(/never been synced/);
  });

  it("desktop: a missing folder only leaves the list and offers no folder delete", () => {
    const copy = matchDeleteCopy({ ...base, kind: "missing" }, false);
    expect(copy.title).toBe("Remove ESs Black Handgun 2026 from the list?");
    expect(copy.confirmLabel).toBe("Remove");
    expect(copy.checkboxes).toEqual([]);
  });

  it("hosted: a desktop-synced match says the desktop copy stays and how it reacts", () => {
    const copy = matchDeleteCopy({ ...base, origin: "desktop" }, true);
    const text = copy.body.join(" ");
    expect(text).toMatch(/Deletes ESs Black Handgun 2026 from splitsmith\.app/);
    expect(text).toMatch(/that copy is not deleted/);
    expect(text).toMatch(/Publish again/);
    expect(copy.checkboxes.map((c) => c.key)).toEqual(["deleteRawUploads"]);
  });

  it("hosted: a native match does not mention a desktop", () => {
    const copy = matchDeleteCopy({ ...base, origin: "hosted" }, true);
    expect(copy.body.join(" ")).not.toMatch(/desktop/);
  });

  it("hosted: an unknown origin (a row with no docs left) hedges", () => {
    const copy = matchDeleteCopy({ ...base, kind: "missing", origin: "local" }, true);
    expect(copy.body.join(" ")).toMatch(/If it was synced from a desktop, that copy is not deleted/);
  });
});
