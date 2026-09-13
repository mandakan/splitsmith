import { describe, expect, it } from "vitest";

import { zoomActionForKey } from "@/lib/zoomKeys";

function key(init: KeyboardEventInit & { target?: HTMLElement | null }) {
  const { target = null, ...rest } = init;
  const e = new KeyboardEvent("keydown", rest);
  if (target) Object.defineProperty(e, "target", { value: target });
  return e;
}

describe("zoomActionForKey", () => {
  it("maps the bare +/=/0/- keys, Shift allowed (+ is Shift+= on most layouts)", () => {
    expect(zoomActionForKey(key({ key: "+", shiftKey: true }))).toBe("in");
    expect(zoomActionForKey(key({ key: "=" }))).toBe("in");
    expect(zoomActionForKey(key({ key: "0" }))).toBe("fit");
    expect(zoomActionForKey(key({ key: "-" }))).toBe("out");
  });

  it("keeps Cmd/Ctrl+1/2/3 as aliases where the browser delivers them", () => {
    expect(zoomActionForKey(key({ key: "1", metaKey: true }))).toBe("in");
    expect(zoomActionForKey(key({ key: "2", ctrlKey: true }))).toBe("fit");
    expect(zoomActionForKey(key({ key: "3", metaKey: true }))).toBe("out");
  });

  it("leaves the browser's own zoom chords alone", () => {
    expect(zoomActionForKey(key({ key: "-", metaKey: true }))).toBeNull();
    expect(zoomActionForKey(key({ key: "=", ctrlKey: true }))).toBeNull();
    expect(zoomActionForKey(key({ key: "0", metaKey: true }))).toBeNull();
    expect(zoomActionForKey(key({ key: "-", altKey: true }))).toBeNull();
  });

  it("ignores the bare keys while typing in a text field, but not the chords", () => {
    const input = document.createElement("input");
    input.type = "text";
    expect(zoomActionForKey(key({ key: "-", target: input }))).toBeNull();
    expect(zoomActionForKey(key({ key: "0", target: input }))).toBeNull();
    expect(zoomActionForKey(key({ key: "1", metaKey: true, target: input }))).toBe("in");
    // A checkbox is not a typing target - the audit chips are hidden checkboxes.
    const box = document.createElement("input");
    box.type = "checkbox";
    expect(zoomActionForKey(key({ key: "-", target: box }))).toBe("out");
  });

  it("returns null for everything else", () => {
    expect(zoomActionForKey(key({ key: "k" }))).toBeNull();
    expect(zoomActionForKey(key({ key: "4", metaKey: true }))).toBeNull();
  });
});
