# UX redesign -- progress and resume notes

Source-of-truth status doc for the redesign initiative on the
`ux-redesign` branch. Read this first when resuming work.

Last touched: 2026-05-14 (in the same session where the design
language was locked in).

## Branch state

All work lives on `ux-redesign`. There are 6 design docs + 18
structural wireframes + 4 polished surfaces.

## Visual language -- locked in

**Direction: "Shot Timer"** -- dark instrument-panel aesthetic for
IPSC competitive shooters. Think Garmin / G-Shock / chronograph / F1
telemetry, not magazine or blog.

- **Surfaces:** near-black background (`#0A0B0D`), tiered surfaces up
  to `#2A303A`
- **Accent:** chronograph LED red (`#FF2D2D`) with glow effects
- **Status:** amber `#FBBF24` for live/in-progress, green `#4ADE80`
  for done/exported, cyan `#06B6D4` for beep, violet `#A78BFA` for
  manual
- **Type:** Antonio (display, condensed uppercase) + Geist (body) +
  JetBrains Mono (all numerals, with tabular figures)
- **Motion:** subtle LED pulses, scope-trace draw-ins, restrained
  reveals; respects `prefers-reduced-motion`

**Rejected earlier:** an editorial direction with Fraunces serif on
cream paper. User feedback was that it felt like a "writing tool or
personal blog" rather than action/shooting sports. Do not revisit.

## Per-shooter identities (introduced in wf07 Compare)

Each shooter has a 5-token color palette. MA is always "you" (carries
the LED-red brand color). Order in any 4-shooter context:

| Shooter | Base    | Notes                          |
| ------- | ------- | ------------------------------ |
| MA (you)| LED red | uses `--led` family            |
| JL      | Amber   | uses `--live` / amber family   |
| PE      | Green   | uses `--done` / green family   |
| RJ      | Blue    | new -- `--c-rj` family         |

For surfaces beyond Compare that show multiple shooters (Coach,
Shooters management, etc.), reuse these identities. Token names live
in `06-design-system.md` under "Per-shooter identities" (added in
this session).

## Polished surfaces -- what's done

| # | Surface              | Method        | Notes                              |
| - | -------------------- | ------------- | ---------------------------------- |
| 01| Match picker         | skill (twice) | Sets language. Second attempt is current. |
| 02| Match overview       | applied       | Mission-briefing dashboard         |
| 03| Stage audit          | skill         | Oscilloscope-style waveform, video tiles, hardware-shuttle transport |
| 04| Create match         | applied       | Scoreboard variant + manual variant stacked. Form patterns, search results, stage editor table. |
| 07| Stage compare        | skill         | F1 telemetry sync timeline         |
| 09| Developer corpus     | applied       | Developer shell variant (cyan accent), workflow stepper, inbox, fixtures table. |

## Polished surfaces -- still to do

12 surfaces remain. Recommended ordering by primitive coverage:

**Batch 2 -- COMPLETE.** wf07 + wf04 + wf09 shipped.

**Batch 3 (recommended):**
- wf13 coach match-wide -- via skill (data viz: histogram, per-stage breakdown bars, recommendations)
- wf14 coach per-stage -- applied (shot ruler + per-shot detail)
- wf08 export configurator -- applied (form-heavy; overlay/transition previews)

**Batch 4:**
- wf05 ingest, wf06 beep review, wf10 developer review queue,
  wf11 developer validate, wf12 developer retrain

**Batch 5:**
- wf15 jobs drawer, wf16 shooters management, wf17 match overview
  empty, wf18 ingest empty

## How to resume

1. Open the most recent polished surface (07) and the design system
   doc (06) side-by-side.
2. Open the next wireframe you want to polish.
3. Pick path:
   - **Apply established language directly:** reuse tokens, sidebar
     pattern, shell, page-head pattern. Compose the surface from
     primitives that already appear in 01/02/03/07. This is the
     default for most remaining surfaces.
   - **Invoke `frontend-design` skill:** only when a surface has
     novel components (e.g. histogram for Coach match-wide, retrain
     pipeline progress). Brief the skill carefully -- always require
     it to extend the existing tokens and not switch fonts or accent.

## Output format reminder

- Polished HTML files are high-fidelity reference designs, NOT
  production code.
- Production target: React 19 + Tailwind 4 + Radix + shadcn-style
  primitives. See `06-design-system.md` for the migration path.
- One `theme.css` will hold every token. No raw hex outside it.

## Open items to remember

- **Backup vs. fixture package overlap** -- needs code research, see
  `02-information-architecture.md` open questions section.
- **Lab retirement checklist** -- minimum: Review feature must move
  into Developer mode > Review queue.
- **Per-shot cross-shooter comparison was removed from Coach** -- 
  shooters take stages in different orders, so shot N is not the same
  physical shot for everyone. Self-referential comparisons only.

## Critical rules (do not break)

- **Color is never the sole carrier of state.** Every state needs a
  non-color cue (shape, icon, label). Especially red/green pairings.
- **WCAG 2.2 AA baseline.** See `05-accessibility.md` for the full
  checklist.
- **Numerals in mono with tabular figures.** Times, splits, counts,
  dates -- all in JetBrains Mono with `tnum, lnum`.
- **No raw hex in HTML/CSS outside the theme file** (when in
  production). Polished references can still inline values during
  iteration.
