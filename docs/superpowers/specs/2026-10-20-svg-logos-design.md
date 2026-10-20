# SVG logos everywhere a logo is accepted

Date: 2026-10-20. Decision: the user asked for SVG input wherever the app
takes an image today (shooter logo, your brand, event logo, a Look's brand
file) and in Look templates, and chose `resvg-py` over Chromium for the
raster copies.

## Why it was refused until now

Every logo upload sniffs bytes with Pillow and refuses anything but PNG,
JPEG (MPO) and WebP, because an SVG is a document: it can carry script,
event handlers, `foreignObject` HTML and references to other files, and a
template loads the logo in Chromium. The Pillow surfaces (summary and grid
logos, the watermark, the palette's logo colours) cannot open an SVG at all.

## The rule

An SVG is accepted when it is **an image and nothing else**. `svg_image.check_svg`
refuses, with the reason in the user's words:

- not well-formed XML, or a root that is not `svg`;
- a `DOCTYPE` or an entity declaration (entity expansion is not worth parsing
  around);
- any `script`, `foreignObject`, `iframe`, `embed`, `object`, `audio`,
  `video` or `set`/`animate` with a `href`;
- any attribute whose name starts with `on`;
- any `href` / `xlink:href` that is not a `#fragment` or a `data:image/png`,
  `data:image/jpeg`, `data:image/webp` URL (so no `<image>` or `<use>` can
  reach another file, local or remote);
- `url(` in any `style` attribute or `<style>` text that is not `url(#...)`,
  and `@import`.

The bytes are stored **as uploaded** (never rewritten), under the same
content names as today (`logo-<12hex>.svg`, `event-<12hex>.svg`,
`brand-<12hex>.svg`), so content addressing, sync keys and caches work as
for a raster. The 2 MB cap applies; the pixel-side cap does not (a vector
has no side), the raster copy is capped instead.

## Serving

The four logo routes (shooter, your brand, event, Look brand) go through one
helper, `identity_media.logo_response`: the suffix picks the media type,
`.svg` is `image/svg+xml`, every response keeps `X-Content-Type-Options:
nosniff`, and an SVG also carries `Content-Security-Policy: default-src
'none'; style-src 'unsafe-inline'`, so a direct navigation to the file runs
nothing even if the check were ever wrong. The SPA and the templates load
logos through `<img>`, where an SVG is inert.

## Rendering

- Templates: the sandbox's `/file/` mount admits `.svg` beside the raster
  suffixes (`_LOGO_SUFFIXES`); Chromium draws it as an image.
- Pillow surfaces: `svg_image.open_image(path)` returns a Pillow image for
  any logo path, rasterising an SVG with `resvg` at a longest side of
  `RASTER_MAX_SIDE` (1024 px, enough for every logo slot) and the SVG's own
  aspect from `viewBox` or `width`/`height`. `logo_spots._open_logo`,
  `logo_spots.watermark_image` and `palette_sources.logo_colours` read
  through it. The segment cache keys a logo by file content, so nothing else
  changes.
- `resvg-py` is a core dependency (a plain wheel, no native libraries), lazily
  imported by `svg_image`, so the slim install and the worker carry it too.

## Not in scope

The share card's inline SVG mark is ours, not an upload. Hosted Looks still
refuse a brand logo file (no file store yet); a hosted shooter or account
logo in SVG goes through `db.account_identity.put_logo`, which already calls
`identity.sniff_logo`.

## Tests

`tests/test_svg_image.py` (the check, case by case; the raster's size and
aspect; `open_image` on both kinds), `tests/test_identity.py` (sniff returns
`svg`, the model admits the name), the upload and serve tests for the
shooter and event logos (an SVG is stored and served with its headers, a
scripted one is a 422), `tests/test_look_brand.py`, `tests/test_look_sandbox.py`
(an `.svg` logo is mounted), `tests/test_logo_spots.py` (an SVG pastes
pixels), and the sync plan's logo shape.
