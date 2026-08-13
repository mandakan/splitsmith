import { beforeEach, describe, expect, it } from "vitest";

import { AUTHOR_KEY_STORAGE_KEY, authorKey } from "./authorKey";

describe("authorKey", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("mints a key on first use and persists it", () => {
    const first = authorKey();
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(localStorage.getItem(AUTHOR_KEY_STORAGE_KEY)).toBe(first);
  });

  it("returns the same key on subsequent calls", () => {
    expect(authorKey()).toBe(authorKey());
  });

  it("replaces a corrupted stored value", () => {
    localStorage.setItem(AUTHOR_KEY_STORAGE_KEY, "not-a-key");
    expect(authorKey()).toMatch(/^[0-9a-f]{64}$/);
  });

  it("survives a localStorage that throws", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("quota");
    };
    try {
      expect(authorKey()).toMatch(/^[0-9a-f]{64}$/);
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});
