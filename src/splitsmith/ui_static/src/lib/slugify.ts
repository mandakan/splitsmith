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

/**
 * The backend's `export_naming.slugify`, byte for byte.
 *
 * No accent stripping: `Långvägen` becomes `l-ngv-gen`, because that is
 * the name already on disk. Use this - never `slugify` above - when
 * composing a name the backend also composes (fixture slugs, export file
 * stems); the two functions disagree on accents on purpose and merging
 * them would rename existing files. `fallback` is required for the same
 * reason it is in Python: the caller must say what kind of thing it is
 * naming.
 */
export function exportSlugify(input: string, fallback: string): string {
  return (
    input
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || fallback
  );
}
