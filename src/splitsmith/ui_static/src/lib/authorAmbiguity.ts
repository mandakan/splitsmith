/**
 * Detects two comment authors posting under confusingly similar names.
 *
 * The server publishes a display name an account chose for itself, so
 * an account holder can set theirs to another commenter's -- including
 * a generated pseudonym like "Prone Popper 47". Every author also
 * carries an `author_code`, which is always in the DOM and always in a
 * tooltip. This module decides when that code additionally becomes
 * *visible*, so a reader does not have to get suspicious first.
 *
 * The rule is equality after folding, not similarity. It catches case,
 * spacing, punctuation, diacritics, and Unicode compatibility forms --
 * the variants someone would reach for on purpose. It does NOT catch
 * "Mathlas" against "Mathias": no edit distance, no homoglyph table.
 * That limit is deliberate. A fuzzier rule would surface codes on names
 * that merely rhyme, training the reader to ignore the signal, and the
 * always-present tooltip already covers everything this misses.
 *
 * Storage normalization is a different function with different rules
 * (`splitsmith.display_name.normalize_display_name`, NFC): it preserves
 * the name the user typed. This one folds aggressively because it is
 * adversarial. Do not reuse either for the other's job.
 */

/** Fold a display name to its comparison form. */
export function normalizeAuthorName(name: string): string {
  return name
    .normalize("NFKD")
    // Strip combining marks left by NFKD, so "a" with a ring above
    // folds onto a plain "a".
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    // Everything that is not a letter or a number becomes a separator,
    // so punctuation and spacing stop being a way to differ.
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/**
 * Codes whose display name is shared with a *different* author in the
 * same thread.
 *
 * One author posting many times is not ambiguous, and neither is one
 * author who renamed themselves -- both are a single code. Only two
 * distinct codes landing on one normalized name are.
 */
export function ambiguousCodes(
  authors: readonly { author_handle: string; author_code: string }[],
): Set<string> {
  const byName = new Map<string, Set<string>>();
  for (const a of authors) {
    const key = normalizeAuthorName(a.author_handle);
    const codes = byName.get(key) ?? new Set<string>();
    codes.add(a.author_code);
    byName.set(key, codes);
  }
  const ambiguous = new Set<string>();
  for (const codes of byName.values()) {
    if (codes.size < 2) continue;
    for (const code of codes) ambiguous.add(code);
  }
  return ambiguous;
}
