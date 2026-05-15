# UX redesign -- progress and resume notes

Source-of-truth status doc for the redesign initiative.

Last touched: 2026-05-15 (post-implementation polish + doc resync).

## Status: implementation complete, polish ongoing

The visual phase finished on 2026-05-14 with 18 polished surfaces. The
implementation phase finished on 2026-05-15 with all 13 redesign issues
shipped to `main`. Iteration since has been targeted polish (contrast,
history-stack hygiene, marker tokens, waveform colour, coach playhead
sync) plus one new feature (legacy-merge wizard, #332).

## Visual language -- locked in

**Direction: "Shot Timer"** -- dark instrument-panel aesthetic for IPSC
competitive shooters. Think Garmin / G-Shock / chronograph / F1
telemetry, not magazine or blog.

- **Surfaces:** near-black background (`#0A0B0D`), tiered surfaces up
  to `#2A303A`
- **Accent:** chronograph LED red (`#FF2D2D`) for dots/hairlines/glow,
  deeper `#DC2626` for filled CTAs (cream text); cyan `#06B6D4` in
  developer mode via the `data-mode` attribute
- **Status:** amber `#FBBF24` for live/in-progress, green `#4ADE80`
  for done/exported, cyan `#06B6D4` for beep, violet `#A78BFA` for
  manual
- **Type:** Antonio (display, condensed uppercase) + Geist (body) +
  JetBrains Mono (all numerals, with tabular figures)
- **Motion:** subtle LED pulses, scope-trace draw-ins, restrained
  reveals; respects `prefers-reduced-motion`

**Antonio is preserved on filled CTAs.** The "option D" contrast recipe
(bold 14px + deeper red + cream ink + LED glow halo) keeps the
typeface while fixing readability. See `06-design-system.md` section
2.6 for the recipe + `docs/ux-redesign/explorations/contrast-options.html`
for the side-by-side comparison.

**Rejected earlier:** an editorial direction with Fraunces serif on
cream paper. User feedback was that it felt like a "writing tool or
personal blog" rather than action/shooting sports. Do not revisit.

## Per-shooter identities

Each shooter has a 5-token color palette under `--color-shooter-*`. MA
is always "you" (carries the LED-red brand color). Order in any
4-shooter context:

| Shooter | Base    | Notes                          |
| ------- | ------- | ------------------------------ |
| MA (you)| LED red | uses `--color-shooter-ma-*`    |
| JL      | Amber   | uses `--color-shooter-jl-*`    |
| PE      | Green   | uses `--color-shooter-pe-*`    |
| RJ      | Blue    | uses `--color-shooter-rj-*`    |

In production, shooter color assignment is generated from a stable
shooter slug (not initials), drawing from this 4-color rotation.

## Polished surfaces (all 18 shipped)

| # | Surface              | Method        | Notes                              |
| - | -------------------- | ------------- | ---------------------------------- |
| 01| Match picker         | skill (twice) | Sets language. Second attempt is current. |
| 02| Match overview       | applied       | Mission-briefing dashboard         |
| 03| Stage audit          | skill         | Oscilloscope-style waveform, video tiles, hardware-shuttle transport |
| 04| Create match         | applied       | Scoreboard variant + manual variant stacked. Form patterns, search results, stage editor table. |
| 05| Ingest               | applied       | Storage-choice radio cards, per-stage video-row groups, role toggles. |
| 06| Beep review          | applied       | Two-pane queue grouped by stage, mini-waveform detail. |
| 07| Stage compare        | skill         | F1 telemetry sync timeline         |
| 08| Export configurator  | applied       | Numbered sections + sticky summary rail. |
| 09| Developer corpus     | applied       | Workflow stepper, inbox, fixtures table. |
| 10| Developer review queue | applied     | 3-column shell; hand-label vs ensemble waveform diff. |
| 11| Developer validate   | skill         | Run-config bar + per-shooter holdout centerpiece. |
| 12| Developer retrain    | skill         | 6-stage pipeline + log tail + before/after table. |
| 13| Coach (match-wide)   | skill         | CRT-oscilloscope split histogram, instrument stat cards. |
| 14| Coach (per-stage)    | applied       | Shot ruler + video tile + per-shot list. |
| 15| Jobs drawer          | applied       | Right slide-out + worker pool chip. |
| 16| Shooters management  | applied       | Per-shooter cards with racing-color rails. |
| 17| Match overview empty | applied       | Just-created variant of 02. |
| 18| Ingest empty         | applied       | Drop-state variant of 05. |

## Implementation -- redesign issues (all 13 shipped to main)

| #   | Issue                                          | Commit on main |
| --- | ---------------------------------------------- | -------------- |
| 319 | Design tokens + theme.css                      | shipped        |
| 320 | Match-as-object data model + CI fix            | shipped        |
| 321 | Jobs drawer redesign                           | shipped        |
| 322 | Match picker                                   | shipped        |
| 323 | Match overview                                 | shipped        |
| 324 | Shooters management                            | shipped        |
| 325 | Ingest                                         | shipped        |
| 326 | Cross-shooter beep review queue                | shipped        |
| 327 | Audit                                          | shipped        |
| 328 | Stage compare (multi-shooter sync timeline)    | shipped        |
| 329 | Coach (match-wide + per-stage)                 | shipped        |
| 330 | Export configurator                            | shipped        |
| 331 | Developer mode (Corpus / Review / Validate / Retrain) + Lab retirement | shipped |
| 332 | Merge wizard for legacy projects               | shipped        |

## Post-implementation polish (2026-05-15)

| Change                                                              | Why                                                                 |
| ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Contrast option D: cream Geist on deeper red, then reverted to Antonio bold + LED glow halo | User preserved aesthetic; recipe codified across all filled-LED surfaces |
| Marker glyph token fix: `var(--marker-*)` -> `var(--color-marker-*)` | Token rename rot; markers had rendered as gray shapes silently      |
| Waveform LED-red oscilloscope colour                                | Same root cause; bar was reading hardcoded Okabe-Ito gray fallback  |
| Token sweep: `--color-done-tint`, `--color-live-tint`, `--color-manual-tint`, `--color-status-info` added | Refs existed but the tokens were never defined         |
| History-stack hygiene across shells + breadcrumbs                   | "Click back, end up on Create Match" reported by user; 8 sites swept |
| Coach per-stage playhead auto-sync                                  | Shot list + ruler + hero panel froze on click instead of following the video |
| Cascade-layer fix for `.btn-led-fill` family                        | Component-layer custom utility lost to Tailwind base utilities; moved to utilities layer |

## Open items

- **Promote / rollback / multi-version artifact storage** for
  `/dev/retrain` is present-but-disabled. Needs persistent calibration
  versioning before the buttons can wire up.
- **Coach cross-shooter comparison** -- intentionally absent; shooters
  take stages in different orders so shot N is not the same physical
  shot for everyone. Self-referential baselines only.
- **Backup vs. fixture-package overlap** -- needs code research.
- **Export overlays** -- infrastructure present, UI absent. JTBD #1a
  is still a gap.
- **`scripts/build_ensemble_artifacts.py` runner integration** with
  `/dev/retrain` polish: the page wires `rebuildLabCalibration` but
  the live pipeline visualization is structural (running stage maps to
  job.progress quintiles), not driven by real per-stage telemetry.
- **Light mode.** Same token names hold; values change via
  `[data-theme="light"]` on root. No timeline.
- **Lab removal milestone.** `/dev/legacy/lab` is reachable for parity
  verification; remove after a few weeks of confirming the new dev
  pages cover the corpus + review flows.

## Critical rules (do not break)

- **Color is never the sole carrier of state.** Every state needs a
  non-color cue (shape, icon, label). Especially red/green pairings.
- **WCAG 2.2 AA baseline.** See `05-accessibility.md` for the full
  checklist.
- **Numerals in mono with tabular figures.** Times, splits, counts,
  dates -- all in JetBrains Mono with `tnum, lnum`.
- **No raw hex in JSX outside `theme.css`** (the rule exists; not yet
  ESLint-enforced).
- **Custom utilities go in `@layer utilities`** (not `components`)
  when they need to override Tailwind base utilities. See
  `06-design-system.md` maintenance rules.
- **Visual verification is required** for aesthetic changes. Build +
  test passing doesn't catch cascade bugs.
