# UX redesign -- design system

How the visual language locked in by the polished surfaces becomes a
durable, reusable system in the production app (React 19 + Tailwind 4 +
Radix + shadcn-style primitives).

**Single source of truth:** one `theme.css` file holds every token. No
surface, no component, no inline style references a raw hex value
again.

## Scope

This document defines:

1. The **token system** -- categories, naming, values
2. The **primitives library** -- the small set of React components that
   compose into every surface
3. The **Tailwind 4 integration** -- how tokens become utility classes
4. The **migration path** -- polished HTML to production React
5. **Maintenance rules** -- linting, reviews, no-hex-outside-theme

It does **not** define motion-design specifics beyond easing curves
and a reduced-motion contract. Per-surface animations are owned by
the surface's polished reference.

## 1. Token system

### 1.1 Naming convention

Semantic, not primitive. Tokens describe what they are *for*, not what
hex value they hold. This decouples surfaces from specific colour
values and lets us swap (dark / light / high-contrast) without
renaming.

- Surface tier: `bg`, `bg-glow`, `surface`, `surface-2`, `surface-3`
- Text tier: `ink`, `ink-2`, `muted`, `subtle`, `whisper`
- Borders: `rule`, `rule-strong`
- Brand: `led`, `led-soft`, `led-deep`, `led-glow`, `led-tint`
- Status: `live` (in-progress), `done` (exported), `cold` (archived),
  with `-glow` variants where applicable

If we add a second theme (light mode, high-contrast mode), the token
*names* stay the same; only their values change.

### 1.2 Color (dark theme, current default)

```
/* Surfaces */
--color-bg:           #0A0B0D;
--color-bg-glow:      #0E1014;
--color-surface:      #14171C;
--color-surface-2:    #1A1E24;
--color-surface-3:    #232831;

/* Borders */
--color-rule:         #262B33;
--color-rule-strong:  #3A4049;

/* Text */
--color-ink:          #F4F4F5;
--color-ink-2:        #C9CCD2;
--color-muted:        #8E939B;
--color-subtle:       #6B7079;
--color-whisper:      #4B5058;

/* Brand (chronograph LED).
 *
 * --color-led        identity/accent: dots, focus rings, brand mark,
 *                    hairlines, glow halos. Saturated, instrument-grade.
 * --color-led-soft   hover-up for accents.
 * --color-led-deep   hover-down + heavy fills (rare).
 * --color-led-fill   filled-button + active-tab background. Slightly
 *                    darker than --color-led so cream text reaches
 *                    readable contrast under colorblindness.
 * --color-led-glow   30% red halo for glows + text shadows.
 * --color-led-tint   10% red tint for subtle backgrounds.
 */
--color-led:          #FF2D2D;
--color-led-soft:     #FF5555;
--color-led-deep:     #B91C1C;
--color-led-fill:     #DC2626;
--color-led-glow:     rgba(255, 45, 45, 0.30);
--color-led-tint:     rgba(255, 45, 45, 0.10);

/* Status */
--color-live:         #FBBF24;
--color-live-glow:    rgba(251, 191, 36, 0.30);
--color-live-tint:    rgba(251, 191, 36, 0.10);
--color-done:         #4ADE80;
--color-done-glow:    rgba(74, 222, 128, 0.30);
--color-done-tint:    rgba(74, 222, 128, 0.10);
--color-cold:         #6B7079;
--color-beep:         #06B6D4;
--color-beep-glow:    rgba(6, 182, 212, 0.30);
--color-beep-tint:    rgba(6, 182, 212, 0.10);
--color-manual:       #A78BFA;
--color-manual-glow:  rgba(167, 139, 250, 0.30);
--color-manual-tint:  rgba(167, 139, 250, 0.10);

/* Aliases for status-tinted surfaces (FolderPicker, in-window cues). */
--color-status-info:        #06B6D4;       /* beep cyan */
--color-status-not-started: #6B7079;
--color-status-in-progress: #FBBF24;
--color-status-complete:    #4ADE80;
--color-status-warning:     #FBBF24;
```

### 1.2.2 Mode-aware accent

The app has two modes (Match / Developer). Surfaces that want to follow
the active mode read three resolved tokens whose values flip via a
`data-mode` attribute on the document root:

```
:root {
  --color-accent-mode:      var(--color-led);
  --color-accent-mode-glow: var(--color-led-glow);
  --color-accent-mode-tint: var(--color-led-tint);
}
[data-mode="developer"] {
  --color-accent-mode:      var(--color-beep);
  --color-accent-mode-glow: var(--color-beep-glow);
  --color-accent-mode-tint: var(--color-beep-tint);
}
```

The global focus ring + the small-caps Kicker label read
`--color-accent-mode` so they switch tone automatically. Surfaces that
should NOT follow the mode (the brand mark, primary CTAs in match mode)
read `--color-led` directly.

Every token here has been spot-checked for AA contrast against its
intended background in `05-accessibility.md`. New tokens must clear
the same bar before they enter the file.

### 1.2.1 Per-shooter identity tokens

Introduced in polished surface 07 (Stage Compare). Each shooter has a
5-token palette (base / soft / deep / glow / tint). Used wherever
multiple shooters appear together (Compare, Coach, Shooters
management). The user (Mathias / MA) always uses the LED-red family
because they are "you". The other three intentionally overlap with
the live/done semantic colors so the same scale renders consistently:

Token names in production use the full `--color-shooter-*` prefix (the
earlier `--c-*` shorthand was renamed when these landed in `theme.css`
so they sit alongside the other `--color-*` tokens and Tailwind 4 can
expose them as `bg-shooter-ma` etc):

```
/* MA -- LED red (you / audio source) */
--color-shooter-ma:       #FF2D2D;            /* matches --color-led */
--color-shooter-ma-soft:  #FF5555;
--color-shooter-ma-deep:  #B91C1C;
--color-shooter-ma-glow:  rgba(255, 45, 45, 0.32);
--color-shooter-ma-tint:  rgba(255, 45, 45, 0.10);

/* JL -- amber */
--color-shooter-jl:       #FBBF24;            /* matches --color-live */
--color-shooter-jl-soft:  #FCD34D;
--color-shooter-jl-deep:  #B45309;
--color-shooter-jl-glow:  rgba(251, 191, 36, 0.32);
--color-shooter-jl-tint:  rgba(251, 191, 36, 0.10);

/* PE -- green (leader-coded) */
--color-shooter-pe:       #4ADE80;            /* matches --color-done */
--color-shooter-pe-soft:  #86EFAC;
--color-shooter-pe-deep:  #166534;
--color-shooter-pe-glow:  rgba(74, 222, 128, 0.32);
--color-shooter-pe-tint:  rgba(74, 222, 128, 0.10);

/* RJ -- blue (new addition for 4th-shooter slot) */
--color-shooter-rj:       #60A5FA;
--color-shooter-rj-soft:  #93C5FD;
--color-shooter-rj-deep:  #1E3A8A;
--color-shooter-rj-glow:  rgba(96, 165, 250, 0.32);
--color-shooter-rj-tint:  rgba(96, 165, 250, 0.10);
```

In production, shooter color assignment should be generated from a
stable shooter ID (not initials), drawing from a rotation of 4-6
identity palettes. The "you" shooter always gets the LED-red family.
Reference shooters (future feature) should use a desaturated variant
to distinguish from local participants.

### 1.3 Typography

Three families, each with a clear purpose:

```
--font-sans:    'Geist', system-ui, sans-serif;
--font-display: 'Antonio', 'Helvetica Neue', sans-serif;
--font-mono:    'JetBrains Mono', ui-monospace, monospace;
```

- **`display`** -- Antonio, condensed athletic. Used for headings,
  match titles, status labels, button text. Heavy weight (700)
  uppercase by default.
- **`sans`** -- Geist. Body copy, paragraphs, descriptive text.
- **`mono`** -- JetBrains Mono. **All numerals** (dates, times,
  shot counts, splits, durations, file sizes). Tabular figures
  with `font-feature-settings: 'tnum' 1, 'lnum' 1`.

Size scale (in rem; 1rem = 16px at default):

```
--text-xs:    0.625rem;    /*  10px  small caps labels */
--text-sm:    0.75rem;     /*  12px  meta, captions */
--text-md:    0.875rem;    /*  14px  body sm */
--text-base:  0.9375rem;   /*  15px  body */
--text-lg:    1rem;        /*  16px  emphasised body */
--text-xl:    1.25rem;     /*  20px  subhead */
--text-2xl:   1.5rem;      /*  24px  brand mark, card titles */
--text-3xl:   1.75rem;     /*  28px  match titles */
--text-4xl:   2.5rem;      /*  40px  big readouts */
--text-5xl:   4.25rem;     /*  68px  hero (mid) */
--text-6xl:   5.5rem;      /*  88px  hero (full) */
```

Sizes are in `rem` so the user's browser font-size preference and zoom
behave correctly.

### 1.4 Spacing

A 4px base scale:

```
--space-1:  0.25rem;    /*   4px */
--space-2:  0.5rem;     /*   8px */
--space-3:  0.75rem;    /*  12px */
--space-4:  1rem;       /*  16px */
--space-5:  1.25rem;    /*  20px */
--space-6:  1.5rem;     /*  24px */
--space-7:  1.75rem;    /*  28px */
--space-8:  2rem;       /*  32px */
--space-10: 2.5rem;     /*  40px */
--space-12: 3rem;       /*  48px */
--space-14: 3.5rem;     /*  56px */
--space-16: 4rem;       /*  64px */
```

### 1.5 Radii

```
--radius-xs:   3px;
--radius-sm:   4px;
--radius-md:   6px;
--radius-lg:   8px;
--radius-xl:   10px;
--radius-2xl:  12px;
--radius-3xl:  14px;
--radius-full: 999px;
```

### 1.6 Shadows and glows

```
--shadow-card:    0 1px 0 rgba(255,255,255,0.02) inset,
                  0 18px 40px -24px rgba(0,0,0,0.7);
--shadow-elev:    0 1px 0 rgba(255,255,255,0.03) inset,
                  0 24px 60px -32px rgba(0,0,0,0.8);
--shadow-led:     0 0 0 1px var(--color-led),
                  0 0 24px var(--color-led-glow);
--shadow-live:    0 0 10px var(--color-live-glow);
--shadow-done:    0 0 10px var(--color-done-glow);
```

### 1.7 Motion

```
--ease:      cubic-bezier(0.22, 1, 0.36, 1);
--ease-out:  cubic-bezier(0.16, 1, 0.3, 1);
--ease-in:   cubic-bezier(0.32, 0, 0.68, 0);

--duration-fast:    150ms;
--duration-base:    180ms;
--duration-slow:    220ms;
--duration-reveal:  700ms;
```

Reduced-motion is **not negotiable**. A global block in `theme.css`:

```
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

Components that use motion for meaning (the pulsing in-progress dot)
must still convey their state without animation -- the static frame
must be complete on its own.

## 2. Primitives library

These are the React components every surface composes from. Each lives
in `src/splitsmith/ui_static/src/components/ui/` (the existing
shadcn-style location).

Variants are managed with `class-variance-authority` (already a
dependency).

### 2.1 Foundational

- **`Brand`** -- the chronograph mark + wordmark + optional serial.
  Variants: `compact`, `with-serial`.
- **`ModeSwitch`** -- segmented control for Match / Developer mode.
  Wraps Radix `Tabs.Root`.
- **`Kicker`** -- small-caps mono accent label with optional edition
  text. (`Project Register · Vol. 01 · Ed. 04`)
- **`DisplayHeading`** -- Antonio uppercase heading. Variants: `hero`,
  `page`, `card`.

### 2.2 Buttons and inputs

- **`Button`** -- variants: `primary` (LED), `default`, `muted`,
  `ghost`. Sizes: `sm`, `md`, `lg`. Slots: `leadingIcon`,
  `trailingIcon`, `kbd`.
- **`IconButton`** -- variants: `default`, `subtle`. Optional badge
  slot. Always >= 36px target.
- **`SearchInput`** -- input with leading icon, optional `kbd` hint,
  focus-glow.
- **`FilterChip`** -- segmented filter pill. Variants: `default`,
  `active`. Optional `count` slot.

### 2.3 Status and data display

- **`StatusPill`** -- dot + label. Variants: `in-progress` (pulsing),
  `exported`, `archived`. Always carries a non-color shape cue (per
  a11y).
- **`Tick`** -- single tick mark. States: `todo`, `done`, `flagged`
  (with notch), `current` (with caret). Flagged carries a shape cue.
- **`TickStrip`** -- a row of `Tick`s with an aria-label summarising
  the state. Used wherever stage progress is shown.
- **`AvatarStack`** -- horizontally stacked avatars. Variants per
  avatar: `you` (LED highlight), `1`-`5` (default colour rotation),
  `more` (overflow count).
- **`Readout`** -- telemetry-style numeric cell. Slots: label, value,
  optional unit and variant (`default`, `live`, `done`).

### 2.4 Containers and layout

- **`AppShell`** -- top bar with brand + mode switch + utility +
  profile + heartbeat indicator. Layout primitive that wraps every
  page.
- **`MatchShell`** -- shooter-mode chrome (header + per-match sidebar
  + JobsPanel). The sidebar's nav rows: Overview, Audit, Coach,
  Shooters, Videos, Export. Audit / Coach / Videos / Export rows are
  *shooter-scoped* -- they take the current URL slug (or
  `default_shooter_slug` from `/api/health` when there's no URL slug)
  and land on `/<route>/<slug>`. Slug-less Shooters / Beep-review
  routes stay slug-less.
- **`DeveloperShell`** -- developer-mode chrome (cyan accent,
  workflow stepper sidebar).
- **`ShooterScopedRoute`** -- route wrapper for every shooter-bearing
  URL. Keys on `slug` so the page remounts cleanly when the user
  switches shooters; redirects to `/shooters` when slug is missing.
- **`ShooterChipStrip`** -- shared shooter switcher. Mounted on every
  shooter-scoped page (Audit, Videos, Coach, Export). Props: `shooters`,
  `activeSlug`, `urlBase` ("audit" | "ingest" | "coach" | "export"),
  optional `stage`, `label` (the verb in front of the chips:
  "Auditing", "Adding footage for", "Coaching", "Exporting"), optional
  per-chip `count` formatter. Hides itself in single-shooter matches.
- **`ContextBar`** -- thin bar under the shell with pulse and
  breadcrumb. Hosts ambient state for the active page.
- **`MatchCard`** -- a single match row. Slots: index, primary
  (title + meta), shooters, progress, status, actions.
- **`ArchiveDivider`** -- the labelled separator between active and
  archived sections.
- **`Colophon`** -- footer with build info and links.

### 2.5 Forms (to be added when those surfaces ship)

These do not exist in the polished match picker but are needed for
later surfaces (create-match, ingest, beep review, etc.). To be
defined when those surfaces are polished.

- **`Field`** -- label + control + help/error text
- **`TextInput`**, **`Select`**, **`NumberInput`**, **`TextArea`**
- **`RadioCard`** -- the option cards used in storage choice + output
  format
- **`Checkbox`**, **`Toggle`**

### 2.6 Filled-LED surfaces (contrast recipe)

A specific class of surface caused recurring contrast pain: saturated
`#FF2D2D` red filled with condensed Antonio black text. The math
(5.65:1) cleared AA, but the saturated red shimmer + condensed
letterforms produced uncomfortable reading -- especially for
deuteranopia and aging vision.

The fixed pattern, captured as three utility classes in `theme.css`:

```css
@layer utilities {
  .btn-led-fill {
    background:     var(--color-led-fill);   /* #DC2626, not #FF2D2D */
    color:          var(--color-ink);        /* cream, not near-black */
    font-family:    var(--font-display);     /* Antonio preserved */
    font-size:      0.875rem;                /* 14px, up from 11px */
    font-weight:    700;                     /* bold, up from semibold */
    letter-spacing: 0.06em;                  /* tighter than before */
    text-transform: uppercase;
    text-shadow:    0 0 6px rgba(0, 0, 0, 0.18);
    box-shadow:
      0 0 0 1px var(--color-led-fill),
      0 0 18px var(--color-led-glow);
  }
  .tab-pill-led-fill { /* same recipe at 13px for tab pills */ }
  .badge-led-fill    { /* same color pair for mono-numeral badges */ }
  .link-led-fill {
    /* Antonio red link on dark bg -- e.g. "Manage shooters ->".
     * Bold + 14px + LED-red text-shadow halo so the strokes pick up
     * optical bloom and read confidently. */
    font-family:    var(--font-display);
    font-size:      0.875rem;
    font-weight:    700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color:          var(--color-led);
    text-shadow:
      0 0 8px rgba(255, 45, 45, 0.35),
      0 0 1px rgba(255, 45, 45, 0.6);
  }
}
```

**The four dials that compound:**

1. `--color-led-fill` (#DC2626) instead of `--color-led` (#FF2D2D) so
   cream text reaches readable contrast.
2. `--color-ink` cream instead of near-black text.
3. Antonio **bold** 14px, not semibold 11px -- thicker verticals + more
   colored pixel mass per glyph.
4. A subtle text-shadow halo (dark on filled red, LED-red bloom on
   red-on-dark links) that mimics the brand-mark glow already used on
   the brand mark and focus rings.

**Antonio stays.** The contrast win is in the dials, not in switching
typeface. The Shot Timer aesthetic depends on Antonio.

**Apply to:** filled primary CTAs (`+ NEW MATCH`, `SAVE & NEXT`,
`PROMOTE TO SHIPPED`), active tab pills (Audit / Compare / Coach),
"next-up" stage badge in the MatchShell sidebar, the ModeSwitch active
toggle, P1-priority numeral tiles, and any inline red link on dark bg
(`Manage shooters ->`).

**Do NOT apply to:** small accent dots, 1-2px hairlines, focus rings,
the brand mark -- those keep `#FF2D2D` because the saturation is what
gives the instrument-panel feel at small sizes.

**Sweep history.** Commit `888933c` introduced the three utility
classes and applied them to five surfaces. Every other red CTA / badge
in the SPA continued using the legacy `bg-led + text-bg` pair
(cream-on-saturated-#FF2D2D), and new pages added since then
reproduced the legacy pattern. Commit `6ed90bc` propagated the fix
across the rest of the SPA via a scoped find/replace: every
`bg-led text-bg` became `bg-led-fill text-ink`, and every
`hover:bg-led-soft hover:text-bg` became `hover:bg-led hover:text-ink`
(matching `.btn-led-fill:hover`). Other status colors (`bg-done`,
`bg-beep`, `bg-live`) paired with `text-bg` were left alone -- they
have enough luminance against dark text to pass AA easily.

A side-by-side ideation with deuteranopia + tritanopia + low-vision
simulation toggles lives at
`docs/ux-redesign/explorations/contrast-options.html` and
`docs/ux-redesign/explorations/red-link-contrast.html`.

## 3. Tailwind 4 integration

Tailwind 4 reads CSS variables in an `@theme` block and generates
matching utilities automatically.

### 3.1 File layout

```
src/splitsmith/ui_static/src/styles/
  theme.css       /* @theme block: all tokens */
  reset.css       /* any global resets */
  globals.css     /* imports theme.css + reset.css */
```

`src/main.tsx` imports `globals.css` and nothing else style-wise.

### 3.2 theme.css

```css
@import "tailwindcss";

@theme {
  /* === Colors === */
  --color-bg: #0A0B0D;
  --color-surface: #14171C;
  /* ... etc per section 1.2 ... */

  /* === Typography === */
  --font-display: 'Antonio', sans-serif;
  --font-sans: 'Geist', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;

  --text-xs: 0.625rem;
  /* ... etc ... */

  /* === Spacing, radii, shadows, motion === */
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* Global focus ring */
:focus-visible {
  outline: 2px solid var(--color-led);
  outline-offset: 2px;
  border-radius: 6px;
}
```

### 3.3 Resulting utilities

Tailwind 4 generates `bg-bg`, `bg-surface`, `text-ink`, `text-led`,
`border-rule`, `font-display`, `text-2xl`, `gap-4`, `rounded-2xl`,
etc. directly from the tokens. No `tailwind.config.js` extension
needed.

### 3.4 Component variants

`class-variance-authority` already in the stack. Example for
`StatusPill`:

```ts
const statusPill = cva(
  "inline-flex items-center gap-2 font-display uppercase tracking-wider text-xs font-bold",
  {
    variants: {
      tone: {
        "in-progress": "text-live",
        "exported":    "text-done",
        "archived":    "text-cold",
      },
    },
    defaultVariants: { tone: "archived" },
  }
);
```

Each primitive lives in its own file with its variants, exports a
typed React component, and uses Radix where appropriate (Tabs,
Tooltip, DropdownMenu, etc.).

## 4. Migration path -- polished HTML to production React

### Phase 1 -- Tokens

Land `theme.css` with the full token set. No component work yet.
Visible result: a Storybook-or-equivalent page can render every token
as a swatch / type spec / spacing visualisation. Audit against the
polished match picker to make sure nothing is missing.

### Phase 2 -- Primitives

Build the primitives listed in section 2 in `components/ui/`. Each
primitive has:

- A typed React component
- CVA variants matching the polished states
- A small visual test page in `pages/_design/` that renders all
  variants (the existing `Design.tsx` route is a fine home)
- An a11y test (keyboard, focus, screen-reader)

### Phase 3 -- Port match picker

Replace the existing `Pick.tsx` page with a composition of primitives
that matches the polished surface 1:1. This is the integration test
for the system. Confirm visual parity by side-by-side comparison with
the polished HTML.

### Phase 4 -- Port remaining surfaces

Each remaining polished surface gets the same treatment. Surfaces that
need primitives we haven't built yet (form controls, video tile,
waveform, etc.) prompt new primitives, added to section 2 of this
doc as they land.

## 5. Maintenance rules

- **No raw hex outside `theme.css`.** ESLint rule rejects hex values
  in component files and CSS modules.
- **No new color, size, or radius without a token.** If a surface needs
  a new value, add it to `theme.css` first.
- **Every primitive must work without color.** Greyscale must convey
  every state. PRs that introduce color-only state are blocked.
- **Every primitive must work without motion.** Static rendering must
  be complete on its own.
- **Tokens are versioned.** Significant changes (renaming, removing,
  re-meaning) require a doc update and a migration note.
- **The polished HTML stays in the repo** as a visual spec. When the
  React port and the polished file diverge, the polished file is the
  reviewer's reference -- the React must match it or the polished file
  is updated and re-reviewed.
- **Cascade-layer hygiene.** Custom utility classes that need to
  override Tailwind base utilities (e.g. `.btn-led-fill` overriding
  `bg-primary` from the shadcn-style Button variants) MUST live in
  `@layer utilities`, not `@layer components`. Tailwind v4 puts
  `utilities` after `components` in the cascade, so a class declared
  in components loses to any colliding utility (same single-class
  specificity, source order wins). Verify by grepping the built CSS:
  the custom selector should appear AFTER the conflicting utility in
  `dist/assets/index-*.css`.
- **Verify token references after renames.** Bare `var(--foo)` refs in
  inline `style={}` or arbitrary-value classes (`bg-[color:var(--foo)]`)
  do NOT fail loudly when the variable is missing -- the property
  silently falls back to transparent / inherit / browser default, and
  the surface renders "dim" or "wrong colour but plausible". After any
  token rename, run:
  ```bash
  grep -rohE "var\(--[a-z][a-z0-9-]+\)" src/splitsmith/ui_static/src \
    --include='*.ts' --include='*.tsx' --include='*.css' | sort -u | \
    while read ref; do
      name=$(echo "$ref" | sed -E 's/var\(([^)]+)\)/\1/')
      grep -qE "^\s*${name}:" src/splitsmith/ui_static/src/styles/index.css \
        || echo "MISSING: $name"
    done
  ```
- **Visual verification is non-negotiable for aesthetic fixes.**
  Typecheck + build + tests passing don't catch CSS cascade bugs.
  Before declaring a contrast/colour change shipped, reload the app in
  a browser and confirm the change is visible. Twice during this
  redesign a cascade-layer + token-rename combo produced "fix doesn't
  apply" regressions that landed on main because we only checked the
  source change, not the rendered output.

## 6. Open questions

- **Light mode.** Out of scope for v1. When added, the same token
  names hold; only values change, surfaced via `[data-theme="light"]`
  on the root.
- **Icon library.** Currently using inline SVG in the polished files.
  Production should standardise on `lucide-react` (already a
  dependency) with a small wrapper to enforce stroke-width and size
  consistency.
- **Brand mark variants.** Mono variant (white on transparent) and
  print variant (black on transparent) needed when the app gets
  exported reports or shared graphics. Not blocking for v1.
- **Token export to JSON.** If a designer wants to import tokens into
  Figma, we can write a small script that converts the CSS variables
  into a Figma-compatible JSON. Trivial follow-up if it becomes
  useful.
