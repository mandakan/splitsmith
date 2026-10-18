---
paths:
  - "src/splitsmith/safe_area.py"
  - "src/splitsmith/overlay_summary_cell.py"
  - "src/splitsmith/overlay_hud*.py"
  - "src/splitsmith/overlay_html.py"
  - "src/splitsmith/overlay_render.py"
  - "src/splitsmith/match_summary.py"
  - "src/splitsmith/summary_card.py"
  - "src/splitsmith/compare/overlay_summary.py"
  - "src/splitsmith/data/looks/**"
  - "tests/test_upright_*.py"
  - "tests/test_card_logos.py"
---

# Upright (portrait) cards, grids and HUD

A canvas taller than wide is upright (`safe_area.is_upright`; square is not).
`safe_area.safe_area(w, h)` is **the one definition** of what Shorts / Reels /
TikTok draw over: the bottom 13 % and a right-edge button column 12 % wide from
40 % down (`None` when not upright). Documents read it as `--safe-bottom` /
`--safe-right` / `--safe-right-top` (`SafeArea.css_vars`).

**Square and wider output must stay byte- and pixel-identical.** Every upright
rule is gated on upright: CSS vars are added only on upright contexts,
`classic_cell_style` returns `None` on landscape, and so on. Check landscape
frames against main with the frame scripts.

The tests measure in Chromium against the area written out by hand:
`tests/test_upright_cards.py`, `test_upright_grids.py`, `test_upright_hud.py`,
`test_card_logos.py`. Keep them that way; don't derive the expected area from
`safe_area` itself.

## Single-shooter cards

- Match summary: `upright_match_summary_html`, its own fit script (rows shrink to
  their columns; the table shrinks until clear of the safe line, then spreads).
- Stage summary: through the engine (`summary_still_html`):
  `upright_summary_scale` keys type to width, `summary_groups(upright=True)`
  puts Splits first and stacked, `upright_cell_style` pads out of the area.
- Other cards: `card_context` adds `SafeArea.css_vars()` (upright only);
  `cell.js` pads by `--safe-bottom` and `--safe-right` (both sides, lifting a
  lower third with it) and turns on fit.js's right inset; `credit.js` sits just
  above the band.
- Info lines under the name (all `Role.DETAIL`, the date included) fit as one
  block (`fitInfoBlock`): one size, the largest at which every line fits, down
  to the legibility floor; only a line that does not fit there wraps.
- A name that would end in "..." may wrap to two lines (`fitLines`). The end
  screen's name wraps at spaces only: a word wider than the column shrinks the
  name, and only a word too long at the floor is cut with "...".
- The logo-box reservation (`rendering.md`, logo spots) and `_shared/event.js`'s
  corner settling apply at every aspect, not only upright.

## Grids

- Grids keep 2x2 / 3x3 / 4x4. `compare/overlay_summary.upright_grid` measures
  each tile minus the area (`SafeArea.box_insets`: the bottom band, and the
  button column across a whole tile any of whose rows reach it), and
  `overlay_summary_cell.upright_grid_type` picks **one** `CellScale` for the
  whole grid, keyed to cell width and shrunk until the fullest tile's text fits
  every tile and its widest rows fit the narrowest (pixel model
  `_grid_tile_height`, a reload row counted where a tile has one).
- **A tile is never sized on its own**: `grid_html(upright=area)` enables
  fit.js's uniform step (`window.__splitsmithFitUniform`: every band ends at the
  smallest `--fit-scale` any band needed) and ends a line at the button column,
  not the tile's edge.
- Hold tile: `summary_groups(upright=True, grid=kind)`: Splits first as a 2x2;
  the reload row three across unless the narrowest tile that draws one is too
  narrow; counts three to a row; hit factor and time at the counts' size, over
  each other when the narrowest tile has no room or any time was entered by
  hand.
- Two-tile grids (`caption_rows`): caption-value rows, counts two to a row, the
  type grows past the width key (to 3x) until the tiles are full, while name,
  captions and padding stay at the width key.
- Match summary tile (`match_summary_groups(upright=True)`): Avg / Best draw /
  Rounds as caption-value rows (`Group.content_columns`, so "Best draw" never
  wraps). Its title strip (`upright_match_summary_strip_html`) is two lines at
  most, shrunk rather than cut.
- Captions: from 85 % of the floor draw at the floor; further under, every
  caption in the grid is dropped (`without_captions`), never drawn smaller.
- When nothing fits at the floor, the bottom row's tiles drop by priority.

## HUD

- `overlay_hud_render.hud_context` gives a template the area in *page* pixels
  (the HUD page is capped at 1080 lines, so 1080x1920 is a 608x1080 page) as
  `data.safe_area` (`hud_safe_area_data`) and as CSS vars on `:root`. A square or
  wider page gets neither.
- Shipped templates set `body.safe` from `data.safe_area` and write every
  upright rule under it. Classic only pads its sprite cell
  (`overlay_render.classic_cell_style`); its drawtext clock and counter are top
  corners.
- An upright overlay record carries `layout: upright-safe-1`
  (`UPRIGHT_LAYOUT`); landscape records carry no key. The canvas is read off a
  file (`ui/exports.overlay_canvas_upright`, ffprobe; unreadable is not
  upright), and the MCP tool and CLI pass `upright=` to
  `overlay_record_matches`.
- `looks check` probes each HUD sample again on a 608x1080 page, also just after
  a shot; anything in the area (`TemplateProbe.unsafe`) is a warning.
