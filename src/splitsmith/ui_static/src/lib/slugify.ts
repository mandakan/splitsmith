/**
 * Kebab-case a free-text label into a safe slug.
 *
 * Drops accents, replaces every non-`[a-z0-9]` run with a single dash,
 * trims leading/trailing dashes. Empty/garbage input falls back to
 * ``"match"`` so callers don't need to defend against null slugs.
 */
export function slugify(input: string): string {
  return (
    input
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "match"
  );
}
