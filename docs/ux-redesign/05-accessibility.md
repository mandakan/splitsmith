# UX redesign -- accessibility principles

This document is the canonical accessibility brief for the redesign.
It applies to every wireframe, every polished surface, and the eventual
React/Tailwind implementation. Reviewers should treat it as a checklist
on a par with the JTBD and IA docs.

**Target:** WCAG 2.2 level AA as the baseline; AAA where it does not
compromise the design.

**Audience:** Competitive shooters and coaches who may have red/green
color-vision deficiency (the most common), as well as users with
age-related vision changes. Both are explicit constraints, not
edge cases.

## 1. Color is never the sole carrier of state

Any state expressed by color must also carry a non-color cue. The
non-color cue can be a shape, icon, label, position, or pattern.

Applies to:

- **Status pills** (in-progress / exported / archived) -- already
  have text labels next to the dot. OK.
- **Tick-mark progress** -- "done" (dark), "flagged" (red), "current"
  (with caret), "todo" (light). The flagged state must carry a shape
  cue, not just color. A small notch, an inner mark, or a different
  silhouette is acceptable.
- **Anomaly indicators** in audit and coach views -- text label + icon
  in addition to the red color.
- **Per-shooter colors** in compare timelines -- always carry the
  shooter's initials inline, never rely on color alone.

The test: print the surface in greyscale. If a state becomes ambiguous,
the design is wrong.

## 2. Contrast minimums

All values measured against the surrounding surface color.

- Body text: **4.5:1** (AA)
- Large text (>= 18.66px regular or >= 14px bold): **3:1**
- UI components and graphical objects (icons, borders that convey
  information): **3:1**
- Status pills and badges: **4.5:1** for the text inside; the colored
  dot/border is exempt as decoration as long as a text label is
  present.

Specifically watch the `subtle` / `whisper` tiers on dark surfaces.
Anything used for actionable or informational text must hit AA even
at small sizes.

**Red text and red CTAs.** Saturated LED red (`#FF2D2D`) against cream
text is below AA for normal body (4.5:1) and especially fails for
red-green colorblindness. Two design-system patterns capture the
fixed approach:

- Filled primary CTAs and any "fill with text inside" surface use
  `--color-led-fill` (`#DC2626`, slightly deeper red) with
  `--color-ink` cream text, Antonio 14px bold, and an LED-red glow
  halo. Captured as `.btn-led-fill` / `.tab-pill-led-fill` /
  `.badge-led-fill` in `theme.css`. See `06-design-system.md` section
  2.6.
- Saturated `--color-led` (`#FF2D2D`) is reserved for accent dots,
  hairlines, focus rings, and the brand mark -- decorative roles
  where saturation gives the instrument-panel feel and where the
  shape is the meaning, not the color.

Do **not** combine saturated `--color-led` with `text-bg` (dark text
on saturated red). That pattern caused recurring contrast pain and
was swept out of the codebase in `6ed90bc`.

## 3. Motion and animation

- Respect `prefers-reduced-motion: reduce`. Disable or shorten:
  - Pulse rings on status indicators
  - Fade-up reveals on page load
  - Title-settle animations
  - Hover transforms beyond color changes
- Keep functional transitions short (<= 200ms) in normal mode.
- Never use motion as the sole signal for a state change -- always
  pair with a static visual that's complete by itself.

Implementation: a single global block in CSS plus per-component
opt-outs for any animations that carry meaning.

## 4. Keyboard

- Every interactive element has a visible focus indicator. Use the
  vermillion accent at 2px outline with 2px offset and a 4px border
  radius. Do not rely on default browser outlines (often suppressed
  globally).
- Logical tab order matches visual reading order.
- Keyboard shortcuts (audit-stepping, save, undo, navigate stages,
  etc.) are documented in the in-app help overlay and on the
  relevant surfaces.
- Custom controls (segmented mode switcher, filter chips, tab strips)
  use the right ARIA roles (`role="tablist"`, `role="tab"`,
  `aria-selected`).

## 5. Sizing and zoom

- Body text and primary controls use `rem` units so browser default-
  font-size and zoom both work.
- Layout holds up at 200% browser zoom without horizontal scrolling
  in the main content. Sidebars and rails may stack.
- Primary touch targets (`Open` button, `New match` button, primary
  actions in toolbars) >= 44 x 44 CSS pixels.
- Secondary controls (overflow `...`, small icon buttons) >= 32 x 32
  with at least 4px spacing from neighbours.

## 6. Typography

- Distinct fonts for distinct purposes: serif for display, sans for
  body, mono for tabular numerals. This is already the visual
  language; it also helps low-vision users distinguish content types.
- Line length: keep paragraph body under ~70 characters at default
  zoom.
- Numerals are always tabular and lining (`font-feature-settings:
  'tnum' 1, 'lnum' 1`) -- critical for our domain.
- Italic is **decorative emphasis only**, never the sole carrier of
  meaning. Screen readers do not consistently emphasise italics.

## 7. Forms and inputs

- Every input has a programmatically associated label (visible label
  or aria-label, never only a placeholder).
- Error states carry text plus icon plus color.
- Required fields are marked with an asterisk *and* aria-required.

## 8. Screen-reader hygiene

- Decorative elements have `aria-hidden="true"` (the pulse dot, the
  paper grain, the chronograph rail).
- Status-conveying elements have meaningful `aria-label` (the tick-
  mark progress group should say "8 of 12 stages audited, stage 7
  flagged" -- which the polished file already does).
- Live regions for ambient updates (jobs drawer state, save
  confirmation) use `aria-live="polite"`.

## 9. Per-surface sign-off checklist

Before a surface is marked done:

1. Greyscale screenshot review -- every state still readable?
2. Deuteranopia/protanopia simulator review (browser extension or
   Figma plugin).
3. Keyboard-only walkthrough -- every action reachable, focus
   always visible?
4. 200% browser zoom -- layout holds, no horizontal scroll?
5. `prefers-reduced-motion` toggled on -- animations sensibly
   degraded?
6. Contrast spot check on all text colors used.

## 10. Implementation in the React app

When the polished surfaces are translated into React + Tailwind:

- Tokens (color, spacing, type, radii, motion) become Tailwind 4 CSS
  variables in a single theme file. Apps consume them; no surface
  hardcodes a hex.
- Component primitives (Button, Pill, Tick, AvatarStack, etc.) bake
  the a11y behaviour: focus rings, ARIA props, keyboard handlers.
- A linter rule rejects raw hex values outside the theme file.
- A snapshot test set runs each route through axe-core in CI.

This document is the source of truth for those decisions.
