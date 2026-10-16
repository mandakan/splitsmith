# Freedoom HUD: scoring, ammo and movement for HUD templates

Status: draft, 2026-10-16

## Goal

Render a Freedoom-style status bar over headcam footage, for fun. The template
itself is a **personal desktop Look** (`~/.splitsmith/looks/freedoom/`), never
shipped. What lands in the repo is what any HUD template could use:

1. scoring, ammo and burst data on the HUD context, opt-in per template;
2. movement proposals and a bulk Keep, so the regions behind it exist without
   a manual pass per stage.

Headcam only: the gun is already in the frame, so there is no weapon sprite.
The "weapon" is what the shooter's cadence looks like (ARMS panel, face,
screen flashes).

## 1. Opt-in context

A template asks for extra data with a meta tag, like positions:

```html
<meta name="splitsmith-data" content="scoring,ammo,bursts">
```

`overlay_hud.declared_data(template)` reads it as text. A template that asks
for nothing gets exactly today's context, so the five shipped templates'
contexts, digests and cached MOVs are unchanged (pinned by a test that
builds each shipped template's context before and after).

### Inputs the renderer does not have today

`overlay_hud_render` reads the audit only. Scorecard, division and stage name
live in `project.json`, so the request layer resolves a `HudExtras` (stage
name and number, `StageScorecard | None`, division, capacity from
`DivisionCapacityConfig`) through `ui.exports.hud_extras_for` and passes it
in: the stage export job, the match export's re-cut and reuse check, the
CLI's and the MCP tool's reuse checks. `looks check` uses a fixed sample
(`look_tools.HUD_SAMPLE_EXTRAS`). A template that declares none of the keys
never sees any of it.

As built, the export preview passes no extras: a declaring template previews
with `scoring` fields `null` and `ammo` `null`, and must draw that case
anyway. Wiring the preview needs the scorecard in its request and cache key;
later, if the rail preview turns out to matter for this Look.

The overlay record (`overlay_record_matches`) gains a `hud_data` identity (a
hash of the declared keys at their provider versions and the `HudExtras` the
template saw) **only when the template declared a key**, so a scorecard
re-import redraws a Freedoom overlay and nothing else.

### The provider registry

The keys are not a closed list. Each is a `HudDataProvider` (key, version,
a pure function of `HudInputs`: shots in time order, the beep in clip
seconds, confirmed regions only, `HudExtras`) in `hud_extras.PROVIDERS`. A
new data source is one `register(...)` call, plus a `HudExtras` field when
it needs something the audit lacks; the render, `looks check`, the record
and the reuse checks pick it up with no edit. Bumping a provider's version
redraws exactly the overlays that declare it. The context's own keys
(`stage`, `options`, `safe_area`) cannot be registered.

The same providers are the natural source for detailed exports (a per-stage
JSON or CSV of every key); not built here.

### `data.scoring` (key `scoring`)

```
{ name, number, hit_factor, stage_pct, points,
  totals: {A, C, D, M, NS, P},
  spreads: { even: [...], fast: [...], moving: [...], end: [...] } }
```

There is no per-shot score, so each spread is a deterministic guess: a list,
one entry per shot, of `{"hit": "A"|"C"|"D"|"M"|null, "ns": bool}` plus the
running totals after that shot. Every strategy ends on the scorecard's
totals. Procedurals are not per-shot; they ride `totals` only.

- `even`: the non-A hits and misses spaced evenly over the stage
  (Bresenham-style, no randomness).
- `fast`: worst outcomes on the fastest splits first ("speed costs").
- `moving`: worst outcomes on shots fired moving first, then `fast`.
- `end`: everything A until the last shots, which take the damage.

All four ship at once so the template can switch by editing one line, with no
re-render plumbing. Shots and hits rarely match one-to-one (steel, makeup
shots, two hits per target): outcomes are allotted to shots by proportion,
and a shot with nothing left to allot is `hit: null` (a puff).

Health and armour are the template's mapping, not data. The intended first
try: health falls from 100 to `stage_pct` along the chosen spread's damage;
armour is the A share so far.

Match percentage is not on `StageScorecard`. Out of scope until the project
stores it.

### `data.ammo` (key `ammo`)

```
{ capacity, start, per_shot: [rounds_left...], refills: [{t, rounds}], ok }
```

Computed in Python (`overlay_hud.ammo_plan`), never in the template:

- `start` is `capacity + 1` (one chambered on a full magazine);
- each confirmed reload refills at its `end`: `capacity + 1` when rounds
  were left (a tactical reload keeps the chambered one), `capacity` at
  slide lock;
- `ok` is false when any count would go below zero, which means a reload
  region is missing. The counter shows it rather than clamping: it is an
  audit check as much as a joke.

`capacity` is `None` for a division with no entry (Open, Standard); `ammo`
is then `null` and the template draws something else (infinity, a Doom
"--").

### `data.bursts` (key `bursts`)

Shots grouped into bursts: a new burst starts at any gap classed
`transition`, `movement` or `reload`. Each burst carries
`{first, last, mean_split, min_split}` and each shot its burst index. The
weapon mapping (chaingun under ~0.18 s, super shotgun for a pair, pistol for
slow aimed fire, BFG for the last burst) is the template's, so it can be
tuned without touching the repo.

## 2. Movement and reload regions

**Movement proposals (MVP).** `events.seed_events` proposes a `movement`
region over every gap the auto-classifier classes `movement` (shot i to shot
i+1, both ends on the shot). A crude first cut: shooters fire on the move,
so the true edges are elsewhere. They are proposals, drawn only in
Breakdown until kept. `SEED_VERSION` goes to 3, which re-seeds every stage
whose regions are all untouched proposals and leaves any stage with a manual
region alone (the existing rule).

**Keep all.** Breakdown's region list gets "Keep all" (every auto proposal
on the stage becomes `manual`, one events PUT through `useStageEvents`).
Match-wide Keep is later, if the per-stage pass proves slow.

## 3. The personal Look (not in the repo)

- Freedoom's assets (BSD licence, kept beside them) are extracted once from
  the WAD to PNGs: `STBAR`, the face set `STF*`, the digit glyphs `STTNUM*`,
  the ARMS digits. Digits are images, so `looks check`'s font rule never
  fires.
- `image-rendering: pixelated`, scaled by an integer step of the page height.
- Face: idle frames on a clock, a wince on a shot whose spread says `M` or
  `ns`, the grin from the last shot through `settle()`; looks left or right
  through a movement region.
- Screen tints: a short red flash on a miss, the item-pickup gold on a reload's
  end.
- Landscape first. Upright lifts the bar above the safe band under
  `body.safe`; later.

## Tests

- `ammo_plan`: tactical reload, slide lock, a missing reload (`ok` false),
  no capacity.
- Each spread ends on the totals; determinism; more shots than hits and the
  reverse.
- Bursts against hand-built gaps.
- Opt-in: shipped templates' contexts unchanged; a declaring template gets the
  keys and the record gains `scoring`.
- Seeding v3: movement proposals; a stage with a manual region not re-seeded.
- Keep all in `lib/useStageEvents` / Breakdown tests.

## Order

1. `ammo`, `bursts`, `scoring` in `overlay_hud` with `HudExtras` plumbing and
   the opt-in.
2. Movement seeding v3 and Keep all.
3. The Freedoom Look locally; findings go back into the spreads.

What's new: step 2 is user-facing (an entry); step 1 is not visible until a
template uses it.
