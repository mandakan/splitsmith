/**
 * Name-collision detection for a comment thread (#867).
 *
 * The rule is deliberately narrow -- equality after folding, not edit
 * distance. The test for what it does NOT catch is as load-bearing as
 * the ones for what it does: the always-present tooltip is what covers
 * the rest, and a rule that quietly widened would make the visible code
 * appear on names that merely rhyme.
 */
import { describe, expect, it } from "vitest";

import { ambiguousCodes, normalizeAuthorName } from "@/lib/authorAmbiguity";

function author(author_handle: string, author_code: string) {
  return { author_handle, author_code };
}

describe("normalizeAuthorName", () => {
  it("folds case", () => {
    expect(normalizeAuthorName("Mathias Axell")).toBe(
      normalizeAuthorName("mathias axell"),
    );
  });

  it("collapses whitespace", () => {
    expect(normalizeAuthorName("Mathias  Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("strips diacritics", () => {
    expect(normalizeAuthorName("M\u00e5thias Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("drops non-alphanumeric characters", () => {
    expect(normalizeAuthorName("Mathias-Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("folds compatibility forms", () => {
    // U+FF2D, fullwidth Latin capital M. Written as an escape so the
    // case is legible; a literal would look like a plain "M".
    expect(normalizeAuthorName("\uff2dathias Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("keeps genuinely different names apart", () => {
    expect(normalizeAuthorName("Mathlas Axell")).not.toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });
});

describe("ambiguousCodes", () => {
  it("is empty when every name is distinct", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Anders Berg", "BBB222"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("flags both codes when two authors share a name", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("mathias  axell", "BBB222"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });

  it("does not flag one author posting twice under one name", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Mathias Axell", "AAA111"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("does not flag one author who renamed themselves", () => {
    // Same code, two names. That is the owner view's business, not the
    // thread's -- nothing here is ambiguous to a reader.
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Anders Berg", "AAA111"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("flags an account shadowing a generated handle", () => {
    const codes = ambiguousCodes([
      author("Prone Popper 47", "AAA111"),
      author("Prone Popper 47", "BBB222"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });

  it("does not flag two different symbol-only names, which both fold to empty", () => {
    // "★★★" and "♦♦♦" have no letters or
    // digits, so normalizeAuthorName strips everything and both land on
    // "". They are not the same name -- they just both lost all their
    // content to folding -- so grouping on the empty key would report a
    // false collision.
    const codes = ambiguousCodes([
      author("★★★", "AAA111"),
      author("♦♦♦", "BBB222"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("flags only the colliding pair in a mixed thread", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Mathias Axell", "BBB222"),
      author("Anders Berg", "CCC333"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });
});
