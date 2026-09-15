# Export presets, option groups and the Look gallery

Approved in chat 2026-09-15. Three problems with the Export page, in the
user's words: the same settings are re-entered every match, the form is
long with many customisation options, and nothing shows what an option
will look like. More effects are coming, so all three get worse with
time.

The design has three parts, shipped as three PRs that are each usable on
their own:

1. **Presets and groups.** A preset row over four collapsible option
   groups, with the form's recurring settings restored between visits.
2. **The Look gallery.** Every card and effect is a tile with a generic
   thumbnail, in the style of Final Cut's titles and effects browsers.
3. **The real-match preview.** One server endpoint renders the selected
   card or overlay with this match's title, stage and backdrop into the
   summary rail.

## What is not in scope

- Syncing saved presets between a desktop install and hosted. Presets
  are stored per install (local) or per user (hosted); a preset saved
  in one is not visible in the other. Revisit once presets are in use.
- A schematic timeline strip ("title 3 s, stage 1, slate, ..."). The
  duration estimate stays as the one structural readout.
- Animated previews. The gallery shows stills; the rail shows one still.
- Recording the options of a run on `ExportRun` (a "repeat this run"
  action from history). Presets cover the recurring case; run replay is
  a separate feature.

## 1. Presets and groups

### What a preset captures

A preset is "how I always render". It holds the recurring settings and
nothing that belongs to one match.

In the preset:

- Output: `mode` (`single` / `trims` / `compare`), `output_format`
  (`fcpxml` / `fcp7xml` / `mp4`), overlay codec, canvas (compare),
  cam options (`includeSecondaries`, `pipLayout`), YouTube encode
  preset on/off.
- Cut: padding preset and head / tail seconds, transition kind and
  seconds.
- Look: every `RenderOptions` field except `titleInfo`; overlay on/off;
  grid overlay on/off and its summary hold seconds.
- Publish: upload-after-render on/off, privacy, playlist name and id,
  notify subscribers. `publishAt` is a date and is not stored.

Never in the preset, asked fresh per match (prefilled from the match
where the page already does so):

- stage selection
- the match title line (`titleInfo`)
- the description lead
- the bundle / project name
- the compare reference shooter (`audioFrom`)
- `publishAt`

### Model

`src/splitsmith/export_presets.py`:

```python
class ExportPresetBody(BaseModel):
    schema_version: int = 1
    mode: Literal["single", "trims", "compare"] = "single"
    output_format: Literal["fcpxml", "fcp7xml", "mp4"] = "fcpxml"
    overlay_codec: Literal["auto", "hevc-alpha", "prores-4444"] = "auto"
    canvas: Literal["uhd", "hd"] = "uhd"
    include_secondaries: bool = True
    pip_layout: Literal["stacked", "pip-corners"] = "stacked"
    youtube_preset: bool = False
    padding_preset: Literal["full", "action", "highlight", "custom"] = "full"
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    transition_kind: Literal["none", "zoom", "static"] = "none"
    transition_seconds: float = 0.5
    title_page: bool = False
    title_page_seconds: float = 3.0
    closing_card: bool = False
    stage_card_style: Literal["none", "slate", "lower-third"] = "none"
    stage_card_seconds: float = 1.5
    summary_hold_seconds: float = 0.0
    overlay: bool = False
    grid_overlay: bool = False
    grid_hold_seconds: float = 0.0
    upload_after_render: bool = False
    upload_privacy: Literal["private", "unlisted", "public"] = "private"
    upload_playlist: str | None = None
    upload_playlist_id: str | None = None
    upload_notify: bool = False


class ExportPreset(BaseModel):
    preset_id: str
    name: str
    builtin: bool = False
    updated_at: datetime
    body: ExportPresetBody
```

Every body field has a default. `model_config = ConfigDict(extra="ignore")`
on the body. Together these are the whole answer to "a preset saved
before a new effect shipped": unknown fields are dropped, missing ones
take the default, and the loader never fails on a body it half
understands. A preset whose *envelope* fails validation (no name, bad
id) is skipped and logged and its siblings survive, the
`export_runs.load_log` pattern. `schema_version` is recorded on write
so a real migration has a hook; nothing reads it yet.

Enum values above are the SPA's own literals today. When a new variant
is added (a new `stage_card_style`, a new transition) the literal widens
here and in `lib/lookGallery.ts` in the same PR.

### Built-ins

Four presets defined in code in the same module, `builtin=True`, with
fixed ids (`builtin:final-cut`, `builtin:youtube`, `builtin:trims`,
`builtin:compare`):

| Name | Body, where it differs from the defaults |
|---|---|
| Final Cut bundle | defaults (`fcpxml`, full padding, cuts, no cards) |
| YouTube match video | `mp4`, `youtube_preset`, `padding_preset=action`, title page, closing card, slates, `summary_hold_seconds=3`, overlay on |
| Quick trims | `mode=trims`, `padding_preset=action` |
| Compare grid | `mode=compare`, `canvas=hd`, `grid_overlay`, `grid_hold_seconds=3` |

The exact bodies are tunable during implementation; the table records
the intent. A built-in can be applied and "Saved as" but never
overwritten, renamed or deleted: the API answers 403 for a write to a
`builtin:` id.

### Storage

Follows `recent_projects`, which already has a local and a hosted
implementation behind one interface.

```python
class ExportPresetStore(Protocol):
    def list(self) -> list[ExportPreset]: ...
    def put(self, preset: ExportPreset) -> None: ...
    def delete(self, preset_id: str) -> None: ...
```

- **Local:** `<user_config home>/export_presets.json`, read and written
  through `splitsmith.user_config`'s atomic helpers. One file, no user
  concept. `SPLITSMITH_DISABLE_USER_CONFIG=1` makes it empty and
  read-only, like everything else in that directory.
- **Hosted:** table `export_presets` with columns `user_id`,
  `preset_id`, `name`, `body` (JSON), `updated_at`; primary key
  `(user_id, preset_id)`; one alembic migration. Not `state_docs`:
  presets belong to a user, not a match, and must stay out of the sync
  manifest and its allowlists.

`ui/export_presets_api.py`:

- `GET /api/settings/export-presets` -> built-ins first, then the user's
  own sorted by name.
- `PUT /api/settings/export-presets/{preset_id}` with `{name, body}`;
  creates or replaces. The server sets `updated_at` and generates the
  id when the client sends `new`.
- `DELETE /api/settings/export-presets/{preset_id}`.

Hosted routes need the signed-in user and scope every query by
`user_id`. Local routes have no auth, like the other local settings.

### Last-used

Separate from presets and never named. The SPA writes the current
form's recurring settings (an `ExportPresetBody`) plus the active
preset id to `localStorage` under one key, debounced, on every change,
and restores them on mount before the first render of the preset row.
Match-specific fields are never written. The read is wrapped in
try/catch; unreadable storage means defaults. A stored preset id that
no longer exists restores the body and shows the row as "Custom".

### The page

Top to bottom in the form column:

1. **Preset row.** `Chip`s: the four built-ins, then the user's own,
   then "Custom" whenever the form's recurring settings differ from the
   active preset's body. Chips are neutral with a tick, never filled;
   red stays with the Export button. The active chip carries a `Menu`:
   Save (own presets only), Save as... (a `Sheet` with a name field),
   Rename, Delete. Applying a preset writes its body into the form,
   switches mode if the preset's differs, and collapses every group but
   Details. The Custom chip's summary names the preset it started from.
2. **Stages.** As today: always open, the blocker ladder, the eligible
   pre-selection.
3. **Output**, **Cut**, **Look** as collapsible `Section`s. `Section`
   gains `summary` and `open` props; a closed header shows the group's
   one-line summary in muted `text-sm` ("MP4 . 1080p . primary only").
   The fields inside are the current `Field` rows, regrouped, not
   restyled. Output: mode, format, codec, canvas, reference, cams,
   YouTube encode preset. Cut: padding, and transitions until part 2
   moves them into the gallery as a slot, after which Cut is padding
   alone. Look: the gallery (part 2; in part 1 the current card,
   overlay and hold fields).
4. **Details.** Always open: title line, description lead, bundle name,
   YouTube connect, upload-after-render with its publish options.
   Preset-owned publish fields sit here with the match-specific text
   because this is where publishing is expected to be found; the
   preset still captures only the recurring ones.

The rail keeps its summary lines, the will-write list and the one
primary button. Part 3 adds the preview above them.

Presets never bypass eligibility: a `compare` preset on a one-shooter
match, or an `mp4` body on a deployment that cannot render, applies to
the form and the existing blocker ladder and format rules say why the
export cannot run.

### Modules

- `lib/exportPresets.ts` (pure, tested): `applyPreset(body) -> form
  patch`, `formToPresetBody(form)`, `isDirty(form, preset)`,
  `groupSummary(form, group)`, `saveLastUsed` / `loadLastUsed`.
- `components/export/PresetRow.tsx`, `OutputGroup.tsx`, `CutGroup.tsx`,
  `DetailsGroup.tsx`. The render, cam and YouTube option blocks move out
  of `Export.tsx` into these; the page keeps its data core (overview
  load, submit, poll, cleanup, delete) and shrinks.
- `lib/api.ts`: the preset types and the three calls.

## 2. The Look gallery

### Model

The Look group is a list of **slots**; each slot has **variants**; each
variant is a tile. A registry, `lib/lookGallery.ts`, is the single
description of the gallery:

```ts
interface LookVariant {
  id: string;             // "slate"
  name: string;           // "Slate"
  thumbnail: string;      // asset path, 480x270 PNG
  /** Which RenderOptions / form fields this variant exposes under its tile. */
  params: LookParam[];
  /** Modes and formats that can draw it; hidden elsewhere. */
  modes: ExportMode[];
  formats: OutputFormat[];
}
interface LookSlot {
  id: "titlePage" | "stageCard" | "closingCard" | "summaryHold" | "overlay" | "transition";
  label: string;          // 1-3 words, a `Label`
  variants: LookVariant[]; // the first is always the "none" variant
  read(form): string;      // selected variant id
  write(form, variantId): form patch;
}
```

Today's slots and variants: Title page (none / title page), Stage card
(none / slate / lower third), Closing card (none / on), Summary hold
(none / on), Overlay (none / on), Transition (cut / zoom / static). The
"none" variant's tile is a blank frame with the word. A future effect
is a new slot or a new variant: one registry entry and one thumbnail.

The rule for which options a mode and format can draw, today spread
over the render panel and the payload mappers, moves into the
registry's `modes` / `formats` and the panel reads it from there. The
mappers keep their own field-level rules (a body must not carry a
field the server would reject), tested against the registry so the two
cannot drift.

### Tiles

One `Label` per slot, a wrapping row of tiles (thumbnail at 160x90 plus
the name, neutral outline, tick on the selected one, the `Chip`
vocabulary), and the selected variant's parameter `Field`s under the
row. Slots hidden by mode or format are not rendered, not disabled.
Hover swaps the rail preview to the tile's generic thumbnail; select
requests the real-match preview (part 3). Tiles are keyboard
reachable; the tile row is a radio group per slot.

### Generic thumbnails

Static PNGs, 480x270, committed under the SPA's assets
(`ui_static/src/assets/look/<slot>-<variant>.png`). Generated by
`scripts/render_look_thumbnails.py`, a sibling of
`render_match_frames.py`, from the same card builders
(`overlay_card.build_card_still`, `build_lower_third`, the summary
cell, the overlay still) with placeholder text ("Match title", "Stage
03 . Standards", sample splits) and a neutral synthetic backdrop. The
transition tiles are drawn by the script as two half-frames with the
transition's mark. Re-run the script when a card's design changes, the
same practice as the frame scripts. The gallery therefore never needs
Chromium at runtime, works on a match with no footage, and a slim
local install sees the same tiles as hosted.

## 3. The real-match preview

### Endpoint

`POST /api/shooters/{slug}/export-preview`

Body: the same render body the export would send (the mappers' output,
so the preview and the render cannot disagree about what a field
means) plus:

- `card`: `title` | `slate` | `lower-third` | `summary` | `closing` |
  `overlay` | `frame` (the plain stage head frame, when no card is on)
- `stage_number`
- `width` (default 960; height follows 16:9)

Returns `image/png`. Cached in `cache_dir` keyed by shooter, stage,
card, width and a hash of the body; the cache is best-effort and a miss
just renders again.

Implementation calls the card builders directly. They are pure
functions of card + backdrop + theme + rasterizer with no ffmpeg in the
path, which is what keeps a preview near the rasterizer's own cost.
The backdrop is the stage's head frame (title, slate, lower third,
frame) or tail frame (summary, closing), grabbed with the export's own
head / tail technique from the trim when one exists, and the theme
surface when not. `overlay` composes the overlay still at the stage's
last shot over the head frame; on a stage without audited shots it
answers 409. A missing rasterizer answers 503.

The compare grid previews its own cards and grid overlay the same way,
against the reference shooter's trim.

Preview requests read the shooter's audit and project docs and write
nothing. They are owner-only, like the export routes.

### The rail

A 16:9 preview at the top of the rail with a one-line caption ("Stage
slate . Stage 03"), followed by the summary lines, the will-write list
and the primary button as today. It shows:

- on hover over a tile: that tile's generic thumbnail, immediately;
- on select, and on any parameter change: the real-match preview for
  the selected tile, requested after a 400 ms debounce, for the first
  selected stage; the previous image stays until the new one lands;
- with no Look slot on: `card=frame`, the plain stage head, which
  itself tells the user the padding and the trim are right.

503 renders as a muted line, "Preview needs a browser"; 409 as
"Overlay needs audited shots"; any other failure as "No preview". None
of them touches the Export button, which never waits on the preview.

`components/export/PreviewPane.tsx` owns the fetch, the debounce and
the three states; `lib/exportPreview.ts` builds the request from the
form and picks the caption.

## Errors and edge cases

- Old-schema preset: defaults for missing fields, unknown fields
  dropped; envelope failures skipped with a log line.
- Preset id in last-used that no longer exists: body restored, row shows
  Custom.
- `localStorage` unreadable: defaults, no error.
- Hosted: a user cannot list, overwrite or delete another user's
  presets; writes to a `builtin:` id answer 403.
- Preview: 503 / 409 / other, each with its rail line, above.
- A registered variant without a thumbnail file fails a test, never
  renders blank.

## Testing

Python:

- `test_export_presets.py`: body round-trip; a body with an unknown
  field and a missing field loads; an envelope without a name is
  skipped and its siblings load; built-ins are returned first and are
  immutable through the API (403); local store writes atomically and
  survives a missing directory; hosted store isolates users.
- `test_export_preview.py`: each `card` returns a PNG of the requested
  size; the same body hits the cache; `overlay` on a bare stage is 409;
  no rasterizer is 503; no trim uses the surface backdrop and still
  answers 200.

TypeScript:

- `exportPresets.test.ts`: `applyPreset` then `formToPresetBody` is the
  identity on every built-in; `isDirty` is false right after apply,
  true after a recurring field changes, and stays false after a
  match-specific field changes; `groupSummary` wording per group;
  last-used save / load with a broken store.
- `lookGallery.test.ts`: every variant has a thumbnail asset; every
  `RenderOptions` field belongs to exactly one variant's `params`; the
  visible slots per mode and format equal the current panel's rules
  (pinned as a table); the mappers never emit a field the registry
  hides for that mode and format.
- Page tests: a preset click updates the form and the summary lines;
  editing a field shows the Custom chip; the rail caption follows hover
  and select; 503 and 409 render their lines and the Export button stays
  enabled.

Visual: `scripts/render_look_thumbnails.py` for the tiles, the existing
frame scripts for the cards, and a screenshot of the page against the
demo match (`scripts/seed_demo_match.py --media`) before any of the
three PRs is called done.

## Order of shipping

1. Presets and groups (part 1). No thumbnails; Look holds the current
   fields.
2. The gallery with generic thumbnails (part 2).
3. The preview endpoint and the rail (part 3).
