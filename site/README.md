# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page (redone 2026-09-14 on the restructured UI;
  refreshed 2026-10-18: a New in splitsmith section, the Looks gallery,
  the videos, Breakdown in the loop, and the copy brought up to date).
  Order: hero, figures, New, Looks, videos, the loop, outputs, install,
  Hosted with the invite request form, detection. Hosted is invite-only,
  so the primary action is the local install. The page reads with
  JavaScript off: the reveal animation only applies under `html.js`, and
  the gallery falls back to its first page of tiles as plain links.
- The Looks gallery is vanilla JS inline at the bottom of `index.html`:
  the item list (`GALLERY`, one entry per tile with its description) and
  the static first page (Splitsmith / Overlay) are both in the file, so
  a new Look or slot means adding entries there and its two images.
  The videos are plain links to unlisted YouTube uploads; no embeds, no
  YouTube scripts.
- `img/` — what the page shows (refreshed 2026-10-18):
  - UI screenshots of the demo match (`scripts/seed_demo_match.py
    --media`, a scratch `SPLITSMITH_HOME`, `SPLITSMITH_AUTO_SYNC=0`,
    1440x900 at 2x, cropped with PIL): `waveform.webp`, `breakdown.webp`,
    `draw.webp`, `breakdown-phone.webp` (390 wide), `budget.webp`,
    `export.webp`; from 2026-09-14 still: `hero-overview.png`,
    `footage-sort.png`, `splits-table.png`, `leaderboard.png`,
    `lanes.png`. Re-shoot them after a visible UI change.
  - Frames from the maintainer's own runs, each checked for other people
    and third-party marks: `pip.webp`, `beep.webp`, `upright-hud.webp`,
    `upright-summary.webp` (the upright export's stage summary, the club
    logo removed), `video-tour.webp` and `video-trailer.webp` (the promo
    videos' poster frames).
  - `img/looks/` — renders with sample data:
    the shipped Looks' previews drawn at 960x540 by
    `scripts/render_look_thumbnails.py`'s builders with its size
    constants widened (into a scratch dir, so the app's committed
    previews are untouched), the Broadcast summary templates drawn at
    1920x1080 through `summary_template`. Each tile is `<id>.webp`
    (480 wide) with `<id>-lg.webp` (960 wide) for the lightbox; the
    `chip-*.webp` files are the Look filter's thumbnails.
- `og.png` — the link card, a 1200x630 render of the hero
- `favicon.svg` — the brand mark (also inlined into the page header)
- The "Request an invite" form in the Hosted section posts straight to
  the hosted app's `POST /api/v1/access-requests` (`https://my.splitsmith.app`)
  now that the app has that route. The Pages Function that used to serve
  this (`functions/api/waitlist.js`) is retired; see "Waitlist backend"
  below for the one-time migration of what it collected.

## Deploy on Cloudflare Pages

The Pages project `splitsmith` is already created and live at
[splitsmith.pages.dev](https://splitsmith.pages.dev). It's wired to
`splitsmith.app` as a custom domain; DNS for the apex + `www` resolves
through Cloudflare in the same account.

**CI deploys** — `.github/workflows/deploy-marketing.yml` runs
`wrangler pages deploy site` on every push to `main` that touches
`site/**` or `wrangler.toml`. Needs repo secrets `CLOUDFLARE_API_TOKEN`
(Pages:Edit + Workers:Edit) and `CLOUDFLARE_ACCOUNT_ID`.

**Manual deploy:**

```bash
export CLOUDFLARE_API_TOKEN=…
export CLOUDFLARE_ACCOUNT_ID=…
npx wrangler pages deploy site --project-name splitsmith --branch main
```

`wrangler.toml` at the repo root pins `pages_build_output_dir = "./site"`
so `wrangler pages deploy` (without args) works from anywhere in the tree.

That's it — no build, no JS bundle, no server.

## Waitlist backend (retired)

The invite form used to POST to `/api/waitlist`, a Pages Function that
stored emails in the `WAITLIST` KV namespace. The form now posts to the
hosted app's `/api/v1/access-requests`, and the app's `access_requests`
table is the one list. The KV entries were imported with
`scripts/import_waitlist.py` on 2026-10-03 and the binding removed; the
namespace itself (id `8e8512c2613e416bab5a445630f11000`) is left in the
Cloudflare account, unbound.

## Local preview

```bash
pnpm pages:dev      # serves site/ with functions/ bound
```

(Without `pnpm pages:dev`, opening `site/index.html` directly still
works: the invite form posts straight to `https://my.splitsmith.app`,
not to a local function.)
