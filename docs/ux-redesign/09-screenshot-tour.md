# UX redesign -- screenshot tour

A checklist for capturing the current shipped state of every redesigned
surface, in the order a reviewer should walk through them. Each row
gives the route to open, what data to seed, the dominant state to
capture, plus secondary states worth covering.

**Why this exists:** the polished HTML files (`docs/ux-redesign/
polished/01-18.html`) are the design spec, but the React port may
have drifted. A design reviewer needs anchored visuals of the *current
shipped* product, not just the spec.

## Setup

```sh
# 1. Ensure venv + ffmpeg ready
cd /Users/mathias/work/splitsmith
uv sync

# 2. Build the SPA so the production build is what you screenshot
cd src/splitsmith/ui_static
npm install
npm run build
cd ../../..

# 3. Pick a project to bind against. Two recommended:
#    a) Blacksmith Handgun Open 2026 -- multi-shooter, all states present
#    b) A freshly-created match -- empty / pristine variants
uv run splitsmith ui  # opens at http://127.0.0.1:5174
```

Set the browser to **1440 x 900** at 100% zoom. Dark mode (the only
mode shipped). Screenshots at 2x DPR for retina sharpness.

## A. Picker + create flows (unbound state)

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| A1 | Match picker | `/pick` | Has recent projects from real use | List, active vs archived sections, hover, project-card actions |
| A2 | Match picker empty | `/pick` (after forgetting all entries) | None | The "no recent projects" empty state + CTAs |
| A3 | Create match (scoreboard) | `/pick/new` | Pick "scoreboard" variant, search a real match | Search field, results dropdown, selected-shooter row, stage-table preview |
| A4 | Create match (manual) | `/pick/new` | Pick "manual" variant | Field stack, stage editor table, primary-shooter row |
| A5 | Merge wizard | `/pick/merge` | Two legacy single-shooter projects on disk | Selection list, plan preview, conflict dialog (if applicable) |

## B. Match-mode shell (bound state)

Open the blacksmith match. The shell is the same across every match-
mode page so it's worth a dedicated set.

| # | Surface | Route | What to capture |
|---|---------|-------|-----------------|
| B1 | Brand header + breadcrumb | any match page | The top bar with brand, mode switch, breadcrumb, identity slot |
| B2 | Match sidebar -- mid-match | `/` (Overview) | Match card + nav rows + stages list with mixed status dots + next-up callout |
| B3 | Match sidebar -- single-shooter match | (open a single-shooter match) | Same shell but `Shooters` row count is 1 |
| B4 | Match sidebar -- multi-shooter match | (multi-shooter) | Same shell with `Shooters` row showing N |
| B5 | JobsPanel closed (FAB) | any page | The FAB in its glow state when work is running |
| B6 | JobsPanel open | click the FAB while jobs run | Running / Needs attention / Queued / Completed groups + worker pool chip |

## C. Match overview

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| C1 | Active match overview | `/` | Audited stages + in-progress + todo | Mission-briefing layout with stat cards, next-up CTA, stages grid, shooters strip |
| C2 | Empty match overview | `/` | Fresh match, no footage | Empty state with ingest CTA + empty shooter slots + help cards |

## D. Shooter management

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| D1 | Shooters list | `/shooters` | Multi-shooter match | Per-shooter cards, racing-color rails, camera counts, missing-trim CTAs |
| D2 | Add shooter | `/shooters` then click +Add | -- | The inline add-shooter form |

## E. Videos / Ingest

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| E1 | Videos -- review state | `/ingest/<slug>` | Existing shooter with assigned + unassigned videos | ShooterChipStrip at top, "Find moved videos" button, storage-choice cards, per-stage blocks, role toggles, beep status pills + Detect-beep buttons |
| E2 | Videos -- empty state | `/ingest/<slug>` | Fresh shooter with no footage | Drop-state hero, FolderPicker inline, tip cards |
| E3 | Folder picker | open from Videos -> "Add more" | Real filesystem | Sidebar with recent / drives / network, file list, video probe results |
| E4 | Find moved videos | open from Videos -> "Find moved videos" | Project with broken symlinks | Relink dialog with scan results + per-video confirmation rows |
| E5 | HITL queue panel | Videos page, when missing beeps exist | Stage with `beep_auto_detect_failed` items | The right-rail queue with rows for beep_missing / beep_low_confidence |

## F. Audit

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| F1 | Audit -- normal state | `/audit/<slug>/3` | Stage with a confirmed beep + detected shots | Toolbar (beep readout, MountSelect, chips), ShooterChipStrip, waveform, video panel, transport, marker layer, shot stepper |
| F2 | Audit -- multi-cam | same | Stage with primary + 1 secondary | Grid-mode video panel toggle on |
| F3 | Audit -- no beep | `/audit/<slug>/<stage with no beep>` | Stage whose primary lacks a beep | "no beep yet" chip + TrimNow + DetectShots blocked |
| F4 | Audit -- Re-pick beep open | open from any audited stage | Click "Re-pick beep" | Inline BeepWaveformPicker under the toolbar with current/draft labels + Apply CTA |
| F5 | Audit -- anomaly nudge | stage with > 2.5s first shot or > 1s overshoot | -- | The yellow "Looks like the beep is wrong" banner above the toolbar |
| F6 | Audit -- list drawer | press `L` | -- | Marker list overlay (kept / rejected / manual filters) |
| F7 | Audit -- help overlay | press `?` | -- | Keyboard shortcuts panel |

## G. Compare

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| G1 | Compare -- 2 shooters playable | `/compare/3` | Stage with 2 shooters who have trim caches | Multi-cam grid, sync timeline, ranking table, audio-source chip with LED ring |
| G2 | Compare -- some shooters unfinished | `/compare/N` | Stage where 1 of 3 shooters lacks trim | The "Unfinished shooters" banner above the grid with Build / Open-in-audit affordances |
| G3 | Compare -- nobody playable | `/compare/N` | Stage where no shooter has trim yet | The CompareEmptyState fallback with full-screen unfinished list |
| G4 | Compare -- layout toggle states | each of grid / row / stack | -- | One screenshot per layout |

## H. Coach

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| H1 | Coach -- match-wide | `/coach/<slug>` | Audited shooter | Headline metrics, per-stage table, distribution histogram, annotations feed |
| H2 | Coach -- per-stage | `/coach/<slug>/3` | Audited stage | Stat cards, shot ruler, video tile, per-shot list with auto-advance |
| H3 | Coach -- no audit yet | `/coach/<slug>/<unaudited>` | -- | The 200-null fallback ("no audit yet") |

## I. Export

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| I1 | Export -- ready | `/export/<slug>` | All stages audited | Numbered sections + sticky summary rail + LED export CTA |
| I2 | Export -- in flight | `/export/<slug>` after clicking Export | -- | Progress states for trim / overlay / stitch |
| I3 | Export -- result panel | `/export/<slug>` after completion | -- | Resulting FCPXML + CSV + report rows with Reveal CTAs |

## J. Cross-shooter beep review

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| J1 | Beep review queue | `/beep-review` | Multi-shooter match with mixed beep statuses | Two-pane queue grouped by stage, status dots, alt-candidate panel, mini-waveform |

## K. Developer mode

Toggle mode in the header. Cyan accent should flip immediately.

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| K1 | Dev shell | any `/dev/*` route | -- | The cyan-accented workflow stepper sidebar, model chip, mode switch active state |
| K2 | Corpus | `/dev/corpus` | Real fixture catalog | Fixtures table, tag taxonomy, inbox card, workflow status banner |
| K3 | Review queue | `/dev/review` | Pending promotions + dev tasks | 3-column shell |
| K4 | Validate | `/dev/validate` | Last validate run cached | Run-config bar, headline metrics, per-shooter holdout, per-venue breakdown |
| K5 | Retrain | `/dev/retrain` | Last calibration cached | 6-stage pipeline, log tail, before/after rows |
| K6 | Legacy Lab | `/dev/legacy/lab` | Real fixture | Catalog table + waveform + ensemble eval (legacy parity) |

## L. Fixture review (no project context)

| # | Surface | Route | Seed | What to capture |
|---|---------|-------|------|-----------------|
| L1 | Review fixture | `/review?fixture=<slug>` | Real fixture audit | Standalone audit (similar shell to Audit but without project chrome) |
| L2 | Promote review | `/promote-review` | Promotion in progress | Anchor-based promotion view |

## M. Design system reference

| # | Surface | Route | What to capture |
|---|---------|-------|-----------------|
| M1 | Token + primitives sandbox | `/_design` | The full design system page -- swatches, type spec, primitives |

## Capture process recommendation

1. **Take F1-F7 first** -- Audit is the most-used surface and the
   highest-leverage screen to review. If anything feels off here, fix
   before continuing.
2. **Then E and G** -- multi-shooter video management + compare. The
   biggest IA change relative to legacy.
3. **Then C and H** -- match-level views (overview + coach).
4. **Then I, J** -- export + beep review.
5. **Save dev mode (K) and design system (M) for last** -- developer
   workflows are stable; design system page is a reference, not a
   destination.

For each surface, also capture:
- The same screen at **prefers-reduced-motion** if you can flip it
  (Chrome devtools -> Rendering -> Emulate CSS media feature).
- The same screen at **200% browser zoom** (Cmd-+ a few times) so the
  reviewer can confirm reflow.
- One **greyscale** version per category (Audit, Compare, Coach,
  Export) to confirm color isn't carrying state alone.

## Annotated review brief

If you want the reviewer to focus on specific things per surface,
pair each screenshot with a one-line note from
`08-known-pain-and-rough-edges.md`. Example:

> **F1 Audit -- normal state.** Q for reviewer: is the beep readout
> in the toolbar legible at a glance? See pain #3.2 -- the "1 beep"
> filter pill was hardcoded for the last six months; same blind spot
> may exist in adjacent chips.

That coupling makes the review a focused critique, not a tour.
