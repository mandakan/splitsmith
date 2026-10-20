---
paths:
  - "src/splitsmith/identity.py"
  - "src/splitsmith/shooter_book.py"
  - "src/splitsmith/account_profile.py"
  - "src/splitsmith/look/brand.py"
  - "src/splitsmith/logo_spots.py"
  - "src/splitsmith/ui/identity_media.py"
  - "src/splitsmith/ui/shooter_roster.py"
  - "src/splitsmith/ui/me_identity_api.py"
  - "src/splitsmith/ui/account_backfill.py"
  - "src/splitsmith/db/account_identity.py"
  - "src/splitsmith/data/looks/_shared/identity.js"
  - "src/splitsmith/data/looks/_shared/event.js"
  - "src/splitsmith/data/looks/_shared/brand.js"
  - "src/splitsmith/ui_static/src/components/shooters/**"
  - "src/splitsmith/ui_static/src/lib/identityMark*.ts"
  - "src/splitsmith/ui_static/src/lib/logo*.ts"
---

# Shooter identity, the shooter book, brand and event logos

## Three logos, none stands in for another

| Logo | Where it lives | Where it draws |
| --- | --- | --- |
| Shooter's | the shooter book, else `<shooter>/identity/logo-<12hex>.<ext>` | top right (cards, summaries, grid tiles) |
| Your brand | `LookBrand` on a Look, else the account's `AccountProfile` | top left of title page and closing card; sting, watermark |
| Event | `Match.branding.event_logo`, `<match>/identity/event-<12hex>.<ext>` | centre of title page and closing card, above the match name |

Nothing falls back between them. All files are content-named, so a new logo is a
new URL and a new cache key.

## Shooter identity

- `MatchProject.identity`: optional `#rrggbb` accent, a club line, a logo (PNG /
  JPEG incl. MPO / WEBP / SVG, 2 MB, a raster's side capped at `LOGO_MAX_SIDE`;
  the upload sniffs bytes and never reads the client's filename).
- **An SVG logo is an image and nothing else** (`svg_image.check_svg`, run by
  every sniffer: `identity.sniff_logo`, `look_brand.check_brand_logo`): no
  script, `foreignObject`, `on*` attribute, `DOCTYPE` or entity, no `href` or
  CSS `url()` that is not a fragment or a raster `data:` URL, and resvg must
  draw it. The bytes are stored as uploaded under the usual content names. A
  logo file leaves the server only through `identity_media.logo_response`
  (media type by suffix, `nosniff`, and for an SVG a `default-src 'none'`
  policy); the Pillow surfaces read every logo through `svg_image.open_image`,
  which rasterises an SVG with resvg at `RASTER_MAX_SIDE`. The template
  sandbox mounts `.svg` like a raster. Tests: `tests/test_svg_image.py` and
  the upload tests. A new logo reader or route goes through those two helpers.
- **Renderers never read identity raw.** The request layer (export jobs in
  `server.py`, `match_cli`, `compare/cli`, the preview API, the palette route)
  resolves it through `ui/identity_media.resolved_identity_for` /
  `grid_identities` into a `ResolvedIdentity` (accent or `None`, `logo_path` on
  this disk or `None`). A missing file is a card without a logo, never a failed
  render.
- **A shooter who set nothing renders exactly as before identities existed.** The
  Look's `accent_series` slot default (by slot, alphabetical by label, filler
  tiles keep their slot) is opt-in via `series_default`, used only by the frame
  scripts' `--identity-demo`. The pixel gate against main runs the frame
  scripts' default path, which goes through the same resolver an export uses.
- Templates read `data.shooters`; `_shared/identity.js` draws logos top right
  (a lower third only when exactly one shooter has one); `--accent` on the cell
  wrapper colours the summary tile.
- Syncs over the media channel (`identity/` in the push plan, hosted key rule,
  delete route). `GET /api/shooters/{slug}/identity/logo` (`ensure_local_logo`,
  `nosniff`) is on the share GET allowlist because the logo is in every video a
  share shows; the share alias binds it to that match's shooters.

## The shooter book

- "You" is the existing `ScoreboardIdentity.shooter_id`.
- Keeps a shooter's look per account **keyed by SSI shooter id, never by name**.
- **The book wins**: `identity_media.identity_source` takes the book's entry for
  `selected_shooter_id` when it sets anything, else the match's own record (as a
  whole), else nothing. No per-match overrides; an old match record must not
  draw over the Shooters page.
- Request layer loads `load_snapshot(state.shooter_book)` once per export and
  passes `book=` to the resolvers.
- Edits write the book (`scope="book"`, the default); an empty look removes the
  entry. `scope="match"` writes only the match's record, which the book draws
  over; the SPA no longer sends it but the server still accepts it, so keep the
  path. An edit starts from the look the videos draw (the book's, logo copied
  in, else the match's own). `use-book` is refused when the book has nothing to
  fall back to.
- Stores: `JsonShooterBookStore` / `JsonAccountProfileStore` under
  `<user config>/account/` locally; hosted `db.account_identity` (RLS tables,
  files under the tenant's `account/`). `AppState.shooter_book` /
  `account_profile` **never fall back to local files on a hosted server**.
- **The book and the account profile are never a `state_docs` kind, never
  synced between desktop and hosted, and no share route reads either.** The
  roster, Compare payload, logo route and title-page club line show the look the
  video draws (`identity_media.effective_identity`) through `_roster_book`,
  which is the empty book on a share request.
- Filled once from the account's existing matches (`account_backfill`): most
  recent wins, entries the book already holds are kept; marked by
  `account_profiles.backfilled_at` hosted, a `.backfilled` file locally.

## Brand and event

- `look_brand.brand_json` draws the Look's brand when it has one, else the
  account's (`MatchTitle.brand`), as a whole. `account_brand` (default on) on
  every request body, preset and both CLIs turns the account's off. The preview
  key gains `book_identity` / `account_brand` only when the card draws them.
- Event logo: `MatchTitle.logo` -> `data.event` -> `_shared/event.js`, which,
  once images load and before the fit, shrinks a corner mark that touches the
  event logo into the room beside it, or moves the event logo and text below the
  corners when that room is under half the mark's width. There is no match-logo
  fallback for a shooter without a logo any more. Syncs at
  match level (`matches/{id}/identity/event-*`: the push plan, the gc's
  `_EVENT_KEY_LOCAL_RE` and the hosted key rule move together). Every renderer
  resolves it through `identity_media.ensure_local_event_logo` (`None` when
  missing).
- Each logo keeps one corner everywhere: brand top left, shooter top right,
  event centre.

## SPA

`/shooters` (`GET /api/me/shooters`, `ui/shooter_roster`; listing writes
nothing) and `components/shooters/ShooterSheet` edit the book only; the same
sheet opens from a shooter chip and Footage's "Edit look". `Avatar` takes
`accent` and `logo` via `lib/identityMark`. The account pill (`AccountMenu`)
holds You, Shooters, Branding and, hosted, Account.
