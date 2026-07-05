# Migrate legacy shadcn token aliases to Shot Timer tokens (#525)

## Problem

`styles/index.css` carries a "Legacy aliases (deprecated)" block: old shadcn
semantic tokens (`--color-background`, `--color-muted-foreground`,
`--color-border`, `--color-primary`, `--color-destructive`, ...) pinned to the
Shot Timer palette so pre-redesign components render correctly on dark. 29
`.tsx` files still speak this dialect (`text-muted-foreground`, `bg-background`,
`border-border`, `text-destructive`, ...).

The aliases are not silent fallbacks -- values are correct for the current
theme -- but they keep two names alive for every color, which invites drift and
contradicts the no-fallbacks rule. Goal: replace the legacy classes with the
canonical Shot Timer classes file-by-file, then delete the alias block.

## Scope

In scope: the shadcn semantic alias block only (`index.css` lines ~156-174):
`background`, `foreground`, `card(-foreground)`, `popover(-foreground)`,
`primary(-foreground)`, `secondary(-foreground)`, `muted-foreground`,
`accent(-foreground)`, `destructive(-foreground)`, `border`, `input`, `ring`.

Out of scope (left untouched):
- The splitsmith-specific legacy block below it (`split-*`, `status-not-started`
  / `in-progress` / `complete` / `warning` / `info`, `marker-*`, `waveform-*`).
  These are not shadcn duplicates and have no grep-gate coverage.
- `--shadow-card` (a `--shadow-*` token, unrelated to `--color-card`). `shadow-card`
  classes stay.
- `--color-muted`, `--color-bg`, `--color-surface*`, `--color-rule`, `--color-led*`:
  already canonical. `bg-muted` / `text-muted` / `border-muted` stay as-is; only
  the `-foreground` variant migrates.

## Mapping

Every mechanical mapping is byte-identical in value pre/post:

| Legacy class | Canonical class | value |
|---|---|---|
| `bg-background` (+`/N`) | `bg-bg` | #0A0B0D |
| `text-foreground`, `*-card-foreground`, `*-popover-foreground`, `*-secondary-foreground`, `*-accent-foreground` | `text-ink` | #F4F4F5 |
| `bg-card` | `bg-surface` | #14171C |
| `bg-popover`, `bg-secondary` | `bg-surface-2` | #1A1E24 |
| `bg-accent` (+`/N`) | `bg-surface-3` | #232831 |
| `text-muted-foreground` (+`/N`) | `text-muted` | #8E939B |
| `border-border` (+`/N`), `border-input` | `border-rule` | #262B33 |
| `text-primary-foreground` | `text-bg` | #0A0B0D |
| `*-primary` (bg/text/border/outline/accent) | `*-led` | #FF2D2D |
| `ring-ring` | `ring-led` | #FF2D2D |
| `ring-border`, `divide-border` | `ring-rule` / `divide-rule` | #262B33 |

This is a **pure value-preserving rename**: every mapping keeps the exact pixel
value, so the migration is a visual no-op and the diff is trivially reviewable.

Deliberately NOT bundled here (separate follow-up): the design system's
red-discipline polish -- small red running text (10-14px) moving from
identity `led` (#FF2D2D) to `led-text` (#FFB4B4), and filled CTAs moving to
`led-fill` (#DC2626). Those change pixel values and belong in their own diff,
not hidden inside a 1000-line rename.

## Token decision: destructive vs ring

`--color-destructive` is deliberately reserved by the design system (see the
`@theme` comment) as a distinct danger semantic, separate from identity red,
"so a future designer can differentiate with one token swap." Decision:

- **Promote** `--color-destructive` + `--color-destructive-foreground` into the
  canonical `@theme` block with a danger-semantic comment. Keep `text-destructive`
  / `bg-destructive` / `border-destructive` classes.
- **Fold** `ring-ring` -> `ring-led` and drop `--color-ring` (a pure shadcn-ism
  with no design-doc justification).

## Execution

1. Sweep all 29 `.tsx` files while the aliases still resolve (nothing breaks
   mid-sweep). With the pure-rename decision the whole sweep is mechanical:
   delegated to Sonnet subagents in parallel batches, each given the full
   keep-list + rename-list so out-of-scope tokens (`split-*`, `status-*`,
   `marker-*`, `waveform-*`, `shadow-card`, and already-canonical
   `muted`/`bg`/`surface`/`rule`/`led`/`ink`) are never touched.
2. Edit `index.css` last: promote destructive, delete the remaining alias lines.

## Verification

`ui_static` has no test runner; verify via:

1. Grep gate empty: `grep -rn "muted-foreground\|border-border\|bg-background" src/`
   plus a wider grep across every migrated token name.
2. `pnpm typecheck` + `pnpm build` clean.
3. Scoped eslint on changed files.
4. Bounded headless screenshot of a dialog + the Review surface to confirm no
   visual regression (values are identical, so this is a sanity check).

## Risk

Low. Every mapping is value-identical, so the migration is a visual no-op. The
only care needed is not touching the out-of-scope tokens that share prefixes
(`shadow-card` vs `bg-card`, `border-border` where the first `border` is the
property). Subagents match full utility classes, not bare token words, to avoid
that. Verified by the grep gate + screenshot after.
