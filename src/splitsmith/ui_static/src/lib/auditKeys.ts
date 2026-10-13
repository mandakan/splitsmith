/**
 * Audit's held keys. Holding P or Q peeks at the rejected candidates: P was
 * the first, Q sits under the left hand while the right holds the mouse.
 */
const PEEK_KEYS = new Set(["p", "P", "q", "Q"]);

export function isPeekKey(key: string): boolean {
  return PEEK_KEYS.has(key);
}
