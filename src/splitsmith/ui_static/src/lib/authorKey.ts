/**
 * The per-browser opaque key that lets an anonymous commenter delete
 * their own comment, and that the server derives their display handle
 * from.
 *
 * It is deliberately NOT a display name. The server owns the name - if
 * the client could send one, anyone with curl could sign a comment with
 * the match owner's. All the client holds is 32 bytes of randomness.
 *
 * Not a security boundary: anyone can mint one. It must never gate
 * anything whose exposure matters.
 */

export const AUTHOR_KEY_STORAGE_KEY = "splitsmith.authorKey";

const KEY_PATTERN = /^[0-9a-f]{64}$/;

function mint(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// Falls back to an in-memory key when localStorage is unavailable
// (private mode, quota, disabled storage). The comment still posts and
// still gets a handle; only "delete my comment" stops surviving a
// reload, which is the right thing to degrade.
let memoryKey: string | null = null;

export function authorKey(): string {
  try {
    const stored = localStorage.getItem(AUTHOR_KEY_STORAGE_KEY);
    if (stored && KEY_PATTERN.test(stored)) return stored;
    const minted = mint();
    localStorage.setItem(AUTHOR_KEY_STORAGE_KEY, minted);
    return minted;
  } catch {
    if (memoryKey == null) memoryKey = mint();
    return memoryKey;
  }
}
