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

/* Brand (chronograph LED) */
--color-led:          #FF2D2D;
--color-led-soft:     #FF5555;
--color-led-deep:     #B91C1C;
--color-led-glow:     rgba(255, 45, 45, 0.30);
--color-led-tint:     rgba(255, 45, 45, 0.10);

/* Status */
--color-live:         #FBBF24;
--color-live-glow:    rgba(251, 191, 36, 0.30);
--color-done:         #4ADE80;
--color-done-glow:    rgba(74, 222, 128, 0.30);
--color-cold:         #6B7079;
```

Every token here has been spot-checked for AA contrast against its
intended background in `05-accessibility.md`. New tokens must clear
the same bar before they enter the file.

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
