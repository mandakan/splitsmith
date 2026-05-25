/**
 * Platform-aware keyboard shortcut labels.
 *
 * Splitsmith runs on macOS, Linux, and (later) Windows. The actual
 * keyboard handlers always accept both ``metaKey`` (Cmd on Mac) and
 * ``ctrlKey`` (Linux/Windows), so the wiring is platform-neutral. The
 * only thing that needs to flex per platform is the label we show the
 * user: "Cmd + S" on macOS, "Ctrl + S" everywhere else.
 *
 * These helpers are SSR-safe (return Linux/Windows defaults when
 * ``navigator`` is undefined) and centralise the detection logic so we
 * don't sprinkle ``navigator.platform`` sniffs across components.
 */

/** True when the browser is running on a Mac. SSR-safe (false off-DOM). */
export function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  // ``userAgentData.platform`` is the modern surface; fall back to the
  // legacy ``platform`` string for Safari / Firefox that haven't shipped
  // UA-CH yet. Both expose "macOS" / "Mac" / "MacIntel" so a substring
  // match covers them.
  const data = (navigator as Navigator & { userAgentData?: { platform?: string } })
    .userAgentData;
  const value = data?.platform ?? navigator.platform ?? "";
  return /mac/i.test(value);
}

/** Label for the primary modifier (Cmd on macOS, Ctrl elsewhere). */
export function modKeyLabel(): string {
  return isMacPlatform() ? "Cmd" : "Ctrl";
}

/** Compact glyph for the primary modifier (⌘ on macOS, Ctrl elsewhere). */
export function modKeyGlyph(): string {
  return isMacPlatform() ? "⌘" : "Ctrl";
}

/** Compact glyph for Return / Enter. ↵ everywhere -- not platform-specific
 *  but co-located here so shortcut surfaces have one import. */
export function returnKeyGlyph(): string {
  return "↵";
}

/** Convenience: format a chorded shortcut for display, e.g.
 *  ``shortcutLabel(["mod", "S"])`` -> ``"Cmd + S"`` (mac) /
 *  ``"Ctrl + S"`` (linux/windows). Pass ``"mod"`` as a sentinel for
 *  the primary modifier. */
export function shortcutLabel(parts: readonly (string | "mod")[]): string {
  return parts.map((p) => (p === "mod" ? modKeyLabel() : p)).join(" + ");
}
