/** Plan for "Install command line tool": what to do at /usr/local/bin/splitsmith. */
export const CLI_TARGET = "/usr/local/bin/splitsmith";

export type CliLinkPlan =
  | { kind: "already"; target: string }
  | { kind: "link"; source: string; target: string }
  | { kind: "conflict"; target: string; existing: string };

/** A console script inside any copy of the app bundle: ours to replace. */
const OURS = /\.app\/Contents\/Resources\/python\/bin\/splitsmith$/;

/**
 * ``existing`` is what the target currently resolves to (a symlink's value,
 * a plain file's own path), or null when nothing is there. A link into an
 * older or renamed copy of the app is replaced; anything else (a uv tool
 * install, a Homebrew shim) is left alone and reported.
 */
export function cliLinkPlan(source: string, target: string, existing: string | null): CliLinkPlan {
  if (existing === null) return { kind: "link", source, target };
  if (existing === source) return { kind: "already", target };
  if (OURS.test(existing)) return { kind: "link", source, target };
  return { kind: "conflict", target, existing };
}
