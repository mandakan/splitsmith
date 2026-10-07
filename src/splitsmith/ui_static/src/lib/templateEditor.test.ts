import { describe, expect, it } from "vitest";

import type { TemplateInfo } from "@/lib/api";
import { editKey, editsList, previewFocusFor, sharedWith, templateKey, templateLabel, templateText } from "@/lib/templateEditor";

const info = (slot: string, variant: string, own = false): TemplateInfo => ({
  slot,
  variant,
  file: `${slot}.html`,
  own,
  content: `<p>${slot} ${variant}</p>`,
});

describe("templateEditor", () => {
  it("names a template as the UI does", () => {
    expect(templateLabel(info("title_page", "default"))).toBe("Title page");
    expect(templateLabel(info("slate", "rise"))).toBe("Stage slate, Rise");
    expect(templateLabel(info("transition", "wipe"))).toBe("Sting, Wipe");
  });

  it("previews the card and variant being edited", () => {
    expect(previewFocusFor("title_page", "rise")).toEqual({ card: "title", variant: "rise" });
    expect(previewFocusFor("lower_third", "default")).toEqual({ card: "lower-third", variant: "default" });
    expect(previewFocusFor("transition", "wipe")).toEqual({ card: "sting", variant: "wipe" });
  });

  it("holds unsaved text per slot and variant, and sends only what changed", () => {
    const edits = { [templateKey("slate", "rise")]: "<p>mine</p>" };
    expect(templateText(info("slate", "rise"), edits)).toBe("<p>mine</p>");
    expect(templateText(info("slate", "default"), edits)).toBe("<p>slate default</p>");
    expect(editsList(edits)).toEqual([{ slot: "slate", variant: "rise", content: "<p>mine</p>" }]);
    expect(editsList({})).toEqual([]);
  });
});

describe("sharedWith", () => {
  it("names the other cards one file draws, only for the Look's own files", () => {
    const own = (slot: string, variant: string, file: string): TemplateInfo => ({ ...info(slot, variant, true), file });
    const all = [
      own("title_page", "default", "card.html"),
      own("slate", "default", "card.html"),
      own("closing", "default", "card.html"),
      own("slate", "rise", "card-rise.html"),
      { ...info("lower_third", "default"), file: "card.html" },
    ];
    expect(sharedWith(all, all[0])).toEqual(["Stage slate", "Closing card"]);
    expect(sharedWith(all, all[3])).toEqual([]);
    expect(sharedWith(all, all[4])).toEqual([]);
  });
});

describe("editKey", () => {
  it("gives every card that draws one own file the same unsaved text", () => {
    const own = (slot: string, variant: string, file: string): TemplateInfo => ({ ...info(slot, variant, true), file });
    const all = [own("title_page", "default", "card.html"), own("slate", "default", "card.html"), info("slate", "rise")];
    expect(editKey(all, all[1])).toBe(editKey(all, all[0]));
    expect(editKey(all, all[2])).toBe(templateKey("slate", "rise"));
    const edits = { [editKey(all, all[0])]: "<p>shared</p>" };
    expect(templateText(all[1], edits, all)).toBe("<p>shared</p>");
  });
});
