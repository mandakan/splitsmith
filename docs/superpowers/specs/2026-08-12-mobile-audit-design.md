# Mobile audit - design

Date: 2026-08-12
Status: draft for review

## Context

The mobile operator programme (`2026-08-10-mobile-operator-surfaces-design.md`)
shipped five slices: the jobs page, bidirectional sync, mobile beep review,
audit triage, and interval reclassify. It listed full shot-level audit on
mobile as an explicit non-goal and backlogged a narrower version, "nudge kept
shots, add missed shots, never show the rejected-candidate list".

This design revisits that. The conclusion is that the backlogged scope is
right, but the interaction model assumed in it - a shrunken desktop timeline
with marker dragging - is not, and a different one makes the whole pass
feasible on a phone.

### What already works on hosted

The read path needs nothing new. `ensure_audit_audio` (`ui/audio.py:462`)
prefers the trimmed clip and pulls it from storage, extracting the WAV
server-side on a miss, so a hosted mirror serves both the audit waveform peaks
and the short-GOP scrub video today. `_SYNC_MEDIA_KEY_RE` (`ui/sync_api.py:62`)
already permits `trimmed/*.{mp4,json}`.

### What does not

Two separate blocks.

1. `Audit` is behind `DesktopGate` (`App.tsx:243`), so no audit surface is
   reachable from a phone at all, on either match origin.
2. On a `desktop`-origin match the mirror write gate (`ui/server.py:6507`)
   returns `403 read_only_mirror` for every non-safe method except a
   fail-closed allow-list. `PUT /stages/{n}/audit` is not on it, and
   `tests/test_mirror_read_only.py::test_mirror_still_blocks_audit_put` pins
   the refusal. Slices 3, 4 and 5 each added their own entry; the comment
   above the list says the rest "stays desktop-owned until its slice ships a
   whitelist entry".

Beyond the gate, `sync/merge.py:223` states the deeper constraint outright:
"shot membership is desktop-owned; ignored". A remote-added shot is dropped on
the desktop's next pull with only a note, and `time` is not in `COACH_FIELDS`
so a moved shot is discarded and trips the non-whitelisted-fields tripwire at
line 302.

## Users and contexts

Primary user: the operator, logged in as themselves. Same two contexts as the
parent design - same-day triage away from the laptop, and remote review later.
Not designed for live at-the-range use, and not for coaches or shooters via
share links.

## Goals

- The operator can run a complete audit pass on a stage from a phone: cover
  every second of it, and fix what is wrong without opening the laptop.
- The pass is a linear review, not a worklist. Its value is that nothing is
  skipped, so any design that jumps between flagged items is the wrong shape.
- A phone shot edit survives the desktop's next sync pull.

## Non-goals

- Multi-cam. Audit truth is the primary's audio; the phone shows only that.
- The rejected-candidate list as a browsable list. Candidates appear only
  inside the target band (below).
- Offline operation, native packaging, push notifications.
- Replacing the desktop audit screen. This is a second, phone-shaped surface.

## What the prototype settled

A working prototype was built against real data - peaks computed at the
server's 8192-bin ceiling from the actual audit WAVs of
`~/work/hfo-masters-2026`, shot times and rejected candidates from that match's
audit docs, and the real stage audio. Three stages, chosen to bracket the
difficulty. The measurements below are from the hardest of them: 39.5 s, 37
shots, 48 rejected candidates, tightest split 0.138 s.

| view | px/s | tightest split reads as |
|---|---|---|
| desktop, fit to a 1400 px window | 35 | 5 px |
| phone, fit to width | 10 | 1 px |
| phone, wrapped into 11 rows | 96 | 13 px |
| zoom lane at 3x | 292 | 40 px |
| zoom lane at 5x | 480 | 66 px |

Three findings drove the design.

**Zoom depth is not the constraint.** A phone showing a one-second window
renders at several times the resolution of the desktop's fit view, over finer
data than the desktop currently requests - `Audit.tsx:574` fetches
`PEAK_BINS = 1500` and never refetches on zoom, while the endpoint accepts up
to 8192 (`server.py:10356`). What a phone cannot do is show depth and extent at
once: at 96 px/s a 39.5 s stage is ten screen-widths.

**The desktop pass depends on extent, not depth.** The operator's method is to
play the whole stage and watch a static waveform while the playhead sweeps it.
That is a completeness pass. Wrapping the timeline into stacked rows preserves
it - whole stage on one screen, playhead sweeping row to row, nothing scrolls -
at roughly 2.7x the desktop's own fit resolution.

**Placement precision was never a pixel problem.** `snapToPeak`
(`lib/peak-snap.ts`) already moves any drop within 25 ms onto the strongest
local peak. Desktop clicks are not accurate either; the snap is what lands
them.

## Surface design

Routes: the existing `audit/:slug` and `audit/:slug/:stage`
(`App.tsx:241-249`), with a `useIsMobile` branch at the route level following
the pattern `MatchShell` uses. `DesktopGate` is lifted for the phone branch
only, so the desktop screen is untouched.

The screen is one fixed-height column: header, wrapped rows, fixed footer. The
page itself never scrolls.

### Wrapped rows

The trimmed clip's peaks, wrapped into N rows like a text editor wraps a long
line. Default 11; 9 and 13 are worth trying during implementation but only one
ships. Each row carries its start time in a narrow gutter. The playhead sweeps
row to row during playback. Markers render on the rows so the whole stage's
shot pattern is visible at a glance.

Peaks are requested at the 8192 cap rather than `PEAK_BINS`, which is what
makes the rows legible; on a 39.5 s stage that is 4.8 ms per bin, for about
65 KB of JSON.

### Zoom lane

Fixed in the footer, playhead pinned dead centre, 2x / 3x / 5x of the row
scale. This is where placement happens. Its zoom control lives inside the lane
rather than in the transport row - the transport overflows a 393 px screen
otherwise, which only showed up when rendering at real size.

### The target band, and the absence of selection

There is no selection state. A dashed band of +/- 120 ms sits at the lane's
centre; whichever marker falls inside it is the target, renders amber, and is
named in the footer. Jogging hands the target to a different marker. The band
is fixed in *time*, not in pixels, so changing the zoom factor changes how wide
it looks but never which marker it selects.

This replaces an earlier tap-to-select model that failed review for being
unanswerable: selection happened as an invisible side effect, had an invisible
exit, and had no presence in the lane where the precise work happens. A phone
screen has no room to display mode state, so the design removes the mode.

Nudging holds the target still while the marker walks away from the fixed
playhead, and the readout counts the offset being dialled in. The hold releases
on the next playhead movement.

### The action area, three states

One slot in the footer, which always names what it will act on.

| in the band | readout | action |
|---|---|---|
| a kept shot | `shot 17/37 . 0.447 s . +20 ms` | -10 ms, +10 ms, delete, video |
| a rejected candidate | `rejected candidate . conf 0.10` | Promote candidate |
| nothing | `no shot at playhead` | Add shot at playhead |

### Rejected candidates

Band-scoped. They are invisible across the rows and the wider lane, and surface
only inside the band as a dim lollipop with opacity scaled by the ensemble's
confidence. This turns 48 to 95 markers per stage into nought to two on screen,
which is why no opt-in mode is needed - the band is the scoping.

Promotion matters beyond convenience. A free-hand add discards what the
detector knew; promoting preserves `candidate_number` and confidence, so the
audit doc records a labelled false negative, which is the input
`scripts/build_ensemble_artifacts.py` needs. The reverse holds: deleting a
detected shot returns it to the candidate pool with its confidence intact.

### Transport and gestures

Two verbs, applied identically to the rows and the lane:

- **Grab the waveform and playback stops**, and stays stopped on release, so
  positioning does not fight the audio.
- **Tap the waveform and playback starts from there.**

Dragging a row scrubs; dragging the lane jogs at fine scale. Both feed audio
that follows the finger. Loop repeats 1.4 s, anchored when it is switched on -
on the target shot if there is one, otherwise the playhead - and held there, so
jogging inside the loop does not drag the region along. A repeat pass therefore
costs one tap rather than re-aiming a thumb, which is the answer to a fingertip
covering roughly 460 ms at the row scale. Speed offers 1x, 0.5x and 0.25x;
`preservesPitch` stays on so slow playback time-stretches rather than
pitch-shifts.

Scrub audio is grain-based: the clip is decoded once into an `AudioBuffer` and
dragging fires short windowed grains. This is an imitation of continuous
varispeed, not the real thing, and is the one interaction whose quality is
still unproven on real hardware.

### Video

A button on the target, not a permanent pane. The operator ranks video third
behind waveform and audio, and the rows need the vertical space.

## Module breakdown

Frontend, under `src/splitsmith/ui_static/src/`:

- `pages/MobileAudit.tsx` - the screen. A separate page rather than a branch
  inside `Audit.tsx`, which is already 2850 lines.
- `components/audit/mobile/WrappedWaveform.tsx` - the row stack.
- `components/audit/mobile/ZoomLane.tsx` - pinned playhead, band, jog.
- `components/audit/mobile/AuditTransport.tsx` - play, loop, speed.
- `components/audit/mobile/ActionArea.tsx` - the three states.
- `lib/audit-target.ts` - pure: given shots, candidates, playhead and band
  width, return the target. Unit-testable, no DOM.
- `lib/scrub-audio.ts` - the grain scrubber.
- `lib/shot-id.ts` - id derivation, shared with the save path.

Existing `MarkerLayer`, `Waveform` and `AuditControls` are desktop-shaped
(pointer drag, pixel hit-testing, zoom chrome) and are not reused.

## Shot identity

The novel part. Beep fields, coach fields and `needs_attention` are scalars on
a stable key, so their merge units are last-writer-wins comparisons. Shots are
a collection with membership, and `shot_number` is positional
(`server.py:2958` writes `"shot_number": i`), so it renumbers on every insert
or delete and cannot key a merge.

Shots gain a persisted `id`. The derivation already exists client-side and is
simply not saved today - `Audit.tsx:2821` builds `cand-${candidate_number}`
for detected markers.

- Detected or promoted: `cand-<candidate_number>`. Stable, and identical on
  both sides for every existing doc, so no migration is needed.
- Manual, newly created: the id the SPA already mints
  (`manual-${Date.now()}-${random}`, `Audit.tsx:965`), now persisted rather
  than discarded. Where the server has to mint one itself it uses **uuid4 hex,
  not ULID** - matching `_new_event_id` (`server.py:621`), whose docstring
  gives the reason: the ulid package is a hosted-only extra while these
  documents are also written on slim local installs.
- Manual, legacy with no id: `manual-t<milliseconds>` derived from the rounded
  time. Deterministic, so both sides mint the same id for the same pre-existing
  shot without coordination.

Ids are stamped at the save boundary in `put_stage_audit`, alongside the
existing event-id stamping, so any client that omits them still produces a
well-formed doc.

## Sync

A fourth merge unit in `sync/merge.py`, replacing the "shot membership is
desktop-owned" note at line 223.

- **Membership** resolves from the merged `audit_events` log, which already
  unions losslessly by event id. A shot is present unless its most recent
  membership event says removed. Shots with no membership events are original
  detector output and are present. This avoids a tombstone field and reuses a
  mechanism that cannot lose an edit.
- **Time** merges last-writer-wins per id, by the timestamp of the newest
  `shot_moved` event for that id, falling back to the doc timestamp.
- **Coach fields** keep their existing per-shot unit, rekeyed from
  `shot_number` to `id`.

**No new event kinds are needed.** The desktop already emits a complete
membership vocabulary, every payload keyed on `id`:

| event | payload | meaning |
|---|---|---|
| `marker_added_manual` | `{id, time}` | present |
| `marker_kept` | `{id, time, candidate_number}` | present |
| `marker_rejected` | `{id, time, candidate_number}` | absent |
| `marker_deleted` | `{id, time, kind}` | absent |
| `marker_time_changed` | `{id, from_time, to_time}` | move |

So the merge works against documents the desktop has been writing all along,
and the mobile screen emits the same kinds rather than a parallel vocabulary.
Promoting a candidate is `marker_kept`; deleting a detected shot is
`marker_rejected`, which is already how the desktop returns one to the
candidate pool.

This works only if the persisted shot `id` equals the marker id the SPA uses
in those payloads. It already does for detected shots (`cand-<n>` on both
sides), and `Audit.tsx:965` already mints `manual-${Date.now()}-${random}` for
a new manual marker - it is simply discarded at save today. Persisting it is
what closes the loop.

`shot_number` stays in the document as a positional display field, recomputed
on save. It is no longer an identity.

### The renumbering hazard

Adding or deleting a shot renumbers every shot after it, and `shot_number` is
still the key on two live surfaces: `PATCH /stages/{n}/shots/{shot_number}/coach`
(`server.py:10900`) and the mirror exemption `_mirror_coach_patch_re` that
guards it. So a coach annotation written against a stale `shot_number` - from a
Coach page or share view opened before the mobile edit - lands on the wrong
shot. This is silent, and it is a data-corruption class of bug rather than a
UI one.

Today it cannot happen, because only the desktop changes shot membership and it
holds the whole document. Mobile insertion makes it reachable, so this design
owns the fix:

- The coach PATCH gains an id-addressed form and the `shot_number` form is
  kept only for compatibility, resolving through the id when one is present.
- The audit doc's version, already returned by `load_audit` and enforced by
  `save_audit`, is the guard for the annotation surfaces too: a PATCH carrying
  a stale version is refused with 409 rather than applied to whatever now sits
  at that index.

This is not optional polish. Without it, the first mobile insert on a stage
someone else has open silently mislabels their coaching notes.

## Mirror write gate

One entry in the allow-list at `ui/server.py:6507`, matching the shape of the
slice 3 to 5 patterns:

```
_mirror_audit_write_re = re.compile(r"^shooters/[^/]+/stages/\d+/audit$")
```

`test_mirror_still_blocks_audit_put` is inverted to assert the PUT now passes,
and a boundary test is added in the style of
`test_mirror_coach_exemption_boundary_pins` so the pattern cannot widen by
accident.

## What the save path already handles

`put_stage_audit` (`server.py:10470`) needs no change beyond id stamping. It
already runs `classify_intervals_in_dicts` on every save, upholding the #775
"audited implies fully classified" invariant server-side; stamps event ids;
clears an open triage flag on a save event (#823); and optimistic-locks against
the stored version, returning 409 on a race.

## Writability

The mobile screen must know before the operator starts editing whether the save
can land, rather than discovering it on a 403. This is issue **#756** - derive
match writability as a capability rather than an origin check. Treated as a
dependency, not folded in: the mobile screen reads the capability, and #756
supplies it.

Until #756 lands, the screen falls back to the origin already present on the
project payload, and a 403 on save surfaces as a clear message naming the
desktop rather than a generic error.

## Error handling

- 409 on save: reload the doc and tell the operator the stage changed
  elsewhere, matching the triage surface's `version_conflict` copy.
- 403 on save: the match is a mirror whose gate has not been opened. Should be
  unreachable once the allow-list entry ships; if seen, it is a bug and says so.
- Peaks or audio 404: the trimmed clip has not synced. Distinguish "still on
  the desktop" from "failed", per the standing guidance in #757.
- Decode failure for the scrub buffer: scrubbing degrades to silent seeking,
  never blocks the pass.

## Testing

- `lib/audit-target.ts` gets vitest coverage over the band rule: kept shot
  wins over candidate, candidate wins over nothing, the nudge hold, and the
  release on playhead movement.
- `lib/shot-id.ts` gets coverage that derivation is stable across a
  delete-and-reinsert, and identical for the same input on both sides.
- The merge unit gets pytest coverage including the conflict matrix: add on
  one side, delete on the other, move on both, and the promote-then-delete
  round trip. Plus a `pytest -m docker` run, since it touches store and DB
  paths.
- The gate exemption gets a pass test and a boundary test.
- The renumbering fix gets a test that inserts a shot on one client and then
  applies a coach PATCH held from before the insert, asserting it is refused
  rather than landing on the neighbour.
- Every new test must be checked against the pre-change code, per the standing
  rule that a test which would have passed against the bug proves nothing.
- Visual verification at phone width via the bounded headless screenshot
  recipe, and live verification on staging with the phone login flow.

## Build order

One PR per step, matching the parent programme's convention.

1. Shot ids: derivation, save-boundary stamping, tests. No behaviour change.
2. The renumbering fix: id-addressed coach PATCH plus version guard. Still no
   user-visible change, and it must precede anything that can insert a shot.
3. The merge unit, against the ids.
4. The gate exemption.
5. The mobile UI.

Steps 1 to 4 ship before the UI, for the same reason the parent design put
pull-merge before any mobile write surface: otherwise a desktop push clobbers
phone edits. The UI is independently demonstrable on a hosted-native match
earlier than that, which is a useful checkpoint but not a shipping state.

## Open questions

- **Default row count.** 11 is the prototype default. 9 and 13 are worth
  feeling on the real device before fixing it.
- **Default zoom.** 3x currently. 5x is the tightest and may be the better
  default given the 0.138 s worst case.
- **Scrub fidelity.** Grain-based scrub is unproven on a phone. If it reads
  badly the fallback is real time-stretch playback, which is meaningfully more
  work and would want its own decision.
- **Stop-on-pointerdown sensitivity.** A stray graze halts playback during a
  review pass. The guard is to stop only after a few pixels of movement, at the
  cost of a slightly late stop.

## Backlog

- Coach access to this surface via a write-scoped share token.
- A unified mobile work inbox aggregating beeps, anomalies and jobs.
- Multi-cam on the phone.
